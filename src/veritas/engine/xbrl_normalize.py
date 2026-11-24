"""XBRL normalization (PRD §7.0).

Turns a raw SEC ``companyfacts`` payload into discrete, de-duplicated metric
points. Pure: no I/O, no network.

Rules implemented (numbers refer to §7.0):

1-2. Facts are keyed on ``(tag, unit, start, end)``. ``fy``/``fp``/``frame`` describe
     the reporting filing, not the period, and are never read.
3.   Only the accepted forms are kept; each metric reads exactly one unit (never mixed).
4.   Dedup keeps the latest ``filed`` row; ``restated`` is set when the earliest-filed
     value differs from the latest by more than ``RESTATED_REL``.
5.   Duration classes come from config (3M / 6M / 9M / FY); other durations are dropped.
6.   Discrete quarters: the 3M fact if present, otherwise YTD differencing within the
     same tag and the same fiscal-year ``start`` (``derived=True``).
7.   Fiscal years are the FY facts' ``(start, end)``; 52/53-week years fall in the FY class.
8.   YoY match: same class, ``end`` 365 ± ``YOY_MATCH_TOL_DAYS`` days earlier.
9.   TTM: sum of the last 4 contiguous discrete quarters, all required.
10.  Tag fallback (Appendix A) is applied per period; the tag used is recorded on the
     point, and a series spanning several tags has ``tag_changed=True``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any, Literal

from veritas.config import t, ti
from veritas.engine.tags import INSTANT_METRICS, TAG_FALLBACKS, TAXONOMY
from veritas.models import Frequency, MetricPoint, MetricSeries

log = logging.getLogger(__name__)

ACCEPTED_FORMS: frozenset[str] = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-QT"})
# A fiscal-year fact must come from an annual report. FY-class durations that appear
# only in 10-Qs are trailing-twelve-month columns, not fiscal years (D-214).
ANNUAL_FORMS: frozenset[str] = frozenset({"10-K", "10-K/A", "10-KT"})

# Every Appendix A metric is a money amount. Never mix units (§7.0.3).
DEFAULT_UNIT = "USD"
METRIC_UNITS: dict[str, str] = {metric: DEFAULT_UNIT for metric in TAG_FALLBACKS}

DurClass = Literal["3M", "6M", "9M", "FY"]
_Period = tuple[date | None, date]


def days(start: date, end: date) -> int:
    """``days(period) = (end − start).days + 1`` (§7 global rules)."""
    return (end - start).days + 1


def duration_class(start: date, end: date) -> DurClass | None:
    """Classify a duration by its day count (§7.0.5). ``None`` = discard."""
    n = days(start, end)
    for cls, lo, hi in (
        ("3M", "DUR_3M_MIN", "DUR_3M_MAX"),
        ("6M", "DUR_6M_MIN", "DUR_6M_MAX"),
        ("9M", "DUR_9M_MIN", "DUR_9M_MAX"),
        ("FY", "DUR_FY_MIN", "DUR_FY_MAX"),
    ):
        if t(lo) <= n <= t(hi):
            return cls  # type: ignore[return-value]
    return None


def growth(current: float | None, prior: float | None) -> float | None:
    """``(x_t − x_{t−1}) / x_{t−1}``, defined only when ``x_{t−1} > 0`` (§7 global)."""
    if current is None or prior is None or prior <= 0:
        return None
    return (current - prior) / prior


def _parse_date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _is_restated(earliest: float, latest: float) -> bool:
    if earliest == 0:
        return latest != 0
    return abs(latest - earliest) > t("RESTATED_REL") * abs(earliest)


class _TagData:
    """De-duplicated facts for one tag in one unit."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.instants: dict[date, MetricPoint] = {}
        self.durations: dict[DurClass, dict[tuple[date, date], MetricPoint]] = {
            "3M": {},
            "6M": {},
            "9M": {},
            "FY": {},
        }
        self.quarters: dict[date, MetricPoint] = {}

    @property
    def empty(self) -> bool:
        return not self.instants and not any(self.durations.values())


class NormalizedFacts:
    """Accessors over normalized companyfacts. Construct with :func:`normalize`."""

    def __init__(
        self,
        tags: dict[str, _TagData],
        components: dict[MetricPoint, tuple[MetricPoint, ...]],
        entity_name: str | None,
    ) -> None:
        self._tags = tags
        self._components = components
        self.entity_name = entity_name
        self._ttm_cache: dict[tuple[str, date], MetricPoint | None] = {}

    # ------------------------------------------------------------------ helpers
    def _tag_data(self, metric: str) -> list[_TagData]:
        return [self._tags[tag] for tag in TAG_FALLBACKS.get(metric, ()) if tag in self._tags]

    def has_metric(self, metric: str) -> bool:
        """True if any fallback tag of ``metric`` has at least one accepted fact."""
        return any(not td.empty for td in self._tag_data(metric))

    def components(self, point: MetricPoint) -> tuple[MetricPoint, ...]:
        """Source facts of a derived quarter or TTM point (empty for reported facts)."""
        return self._components.get(point, ())

    # ------------------------------------------------------------- per period
    def quarter(self, metric: str, end: date) -> MetricPoint | None:
        """Discrete quarter ending exactly at ``end`` (first fallback tag with a value)."""
        for td in self._tag_data(metric):
            point = td.quarters.get(end)
            if point is not None:
                return point
        return None

    def fy(self, metric: str, end: date) -> MetricPoint | None:
        """FY fact ending exactly at ``end`` (first fallback tag with a value)."""
        for td in self._tag_data(metric):
            hits = [p for (s, e), p in td.durations["FY"].items() if e == end]
            if hits:
                return max(hits, key=lambda p: (p.filed, p.start or date.min))
        return None

    def instant(self, metric: str, end: date, tol_days: int = 0) -> MetricPoint | None:
        """Instant at ``end``; exact by default (current-period lookups, D-210).

        With ``tol_days`` > 0 (prior-year / prior-quarter matches, §7.0.8) the closest
        instant within ± tolerance is returned. Fallback is per period: the first tag
        with an instant inside the window wins.
        """
        tol = tol_days
        for td in self._tag_data(metric):
            best: MetricPoint | None = None
            best_key: tuple[int, int] | None = None
            for e, point in td.instants.items():
                diff = abs((e - end).days)
                if diff > tol:
                    continue
                key = (diff, -e.toordinal())  # closest, then the later date
                if best_key is None or key < best_key:
                    best, best_key = point, key
            if best is not None:
                return best
        return None

    def ttm(self, metric: str, end: date) -> MetricPoint | None:
        """Sum of the 4 contiguous discrete quarters ending at ``end``; all required."""
        key = (metric, end)
        if key not in self._ttm_cache:
            self._ttm_cache[key] = self._compute_ttm(metric, end)
        return self._ttm_cache[key]

    def previous_quarter(self, metric: str, point: MetricPoint) -> MetricPoint | None:
        """The discrete quarter ending at ``point.start − 1 day`` (± tolerance)."""
        assert point.start is not None
        target = point.start - timedelta(days=1)
        exact = self.quarter(metric, target)
        if exact is not None:
            return exact
        tol = ti("YOY_MATCH_TOL_DAYS")
        candidates = [e for e in self.quarter_ends(metric) if abs((e - target).days) <= tol]
        if not candidates:
            return None
        best = min(candidates, key=lambda e: (abs((e - target).days), -e.toordinal()))
        return self.quarter(metric, best)

    def _compute_ttm(self, metric: str, end: date) -> MetricPoint | None:
        q0 = self.quarter(metric, end)
        if q0 is None:
            return None
        chain = [q0]
        while len(chain) < 4:
            prev = self.previous_quarter(metric, chain[-1])
            if prev is None:
                return None
            chain.append(prev)
        first_start = chain[-1].start
        assert first_start is not None
        if duration_class(first_start, end) != "FY":
            return None
        latest = chain[0]
        point = MetricPoint(
            start=first_start,
            end=end,
            value=sum(p.value for p in chain),
            unit=latest.unit,
            xbrl_tag=latest.xbrl_tag,
            accession=latest.accession,
            form=latest.form,
            filed=latest.filed,
            derived=any(p.derived for p in chain),
            restated=any(p.restated for p in chain),
        )
        self._components[point] = tuple(reversed(chain))
        return point

    # ------------------------------------------------------------- calendars
    def quarter_ends(self, metric: str | None = None) -> list[date]:
        """Discrete quarter end dates, newest first (one metric, or all duration metrics)."""
        metrics = [metric] if metric is not None else self._duration_metrics()
        ends: set[date] = set()
        for m in metrics:
            for td in self._tag_data(m):
                ends.update(td.quarters)
        return sorted(ends, reverse=True)

    def instant_ends(self, metric: str) -> list[date]:
        ends: set[date] = set()
        for td in self._tag_data(metric):
            ends.update(td.instants)
        return sorted(ends, reverse=True)

    def fiscal_years(self, metric: str | None = "Revenue") -> list[tuple[date, date]]:
        """Consecutive fiscal years ``(start, end)``, newest first (§7.0.7, D-214).

        Built from annual-report FY facts of ``metric`` (Revenue by default; if it has
        none, every duration metric). Starting from the newest, each earlier year must end
        within ± ``YOY_MATCH_TOL_DAYS`` of the later year's ``start − 1 day``; the chain
        stops at the first gap, so periods never overlap.
        """
        periods: set[tuple[date, date]] = set()
        if metric is not None:
            for td in self._tag_data(metric):
                periods.update(td.durations["FY"])
        if not periods:
            for m in self._duration_metrics():
                for td in self._tag_data(m):
                    periods.update(td.durations["FY"])
        if not periods:
            return []
        ordered = sorted(periods, key=lambda p: (p[1], p[0]), reverse=True)
        tol = ti("YOY_MATCH_TOL_DAYS")
        chain = [ordered[0]]
        while True:
            target = chain[-1][0] - timedelta(days=1)
            cands = [p for p in ordered if abs((p[1] - target).days) <= tol and p[1] < chain[-1][1]]
            if not cands:
                return chain
            chain.append(min(cands, key=lambda p: (abs((p[1] - target).days), -p[0].toordinal())))

    def prior_year_end(self, end: date, candidates: Iterable[date]) -> date | None:
        """The candidate end 365 ± tolerance days before ``end`` (closest to 365)."""
        tol = ti("YOY_MATCH_TOL_DAYS")
        best: date | None = None
        best_key: tuple[int, int] | None = None
        for e in candidates:
            gap = (end - e).days
            if abs(gap - 365) > tol:
                continue
            key = (abs(gap - 365), -e.toordinal())
            if best_key is None or key < best_key:
                best, best_key = e, key
        return best

    def prior_year_quarter_end(self, end: date, metric: str | None = None) -> date | None:
        """Quarter end of the same fiscal quarter one year earlier (§7.0.8)."""
        return self.prior_year_end(end, self.quarter_ends(metric))

    def yoy_quarter(self, metric: str, end: date) -> MetricPoint | None:
        """The metric's discrete quarter one year before ``end`` (never sequential)."""
        prior = self.prior_year_quarter_end(end, metric)
        return None if prior is None else self.quarter(metric, prior)

    def yoy_instant(self, metric: str, end: date) -> MetricPoint | None:
        """Instant at the prior-year quarter end (± tolerance).

        The prior end is the matched prior-year quarter end when one exists, otherwise
        ``end − 365 days``.
        """
        prior = self.prior_year_quarter_end(end)
        target = prior if prior is not None else end - timedelta(days=365)
        return self.instant(metric, target, tol_days=ti("YOY_MATCH_TOL_DAYS"))

    def quarter_covered(self, metric: str, start: date, end: date) -> bool:
        """True if some reported duration fact of ``metric`` spans ``[start, end]``.

        Used to tell "not reported" (no fact at all) from "reported in a YTD/FY total but
        the discrete quarter is not derivable" (D-209).
        """
        for td in self._tag_data(metric):
            for periods in td.durations.values():
                for s, e in periods:
                    if s <= start and e >= end:
                        return True
        return False

    def yoy_ttm(self, metric: str, end: date) -> MetricPoint | None:
        prior = self.prior_year_quarter_end(end, metric)
        return None if prior is None else self.ttm(metric, prior)

    # ---------------------------------------------------------------- series
    def _duration_metrics(self) -> list[str]:
        return [m for m in TAG_FALLBACKS if m not in INSTANT_METRICS]

    def series(self, metric: str, frequency: Frequency) -> MetricSeries:
        """Chronological (oldest first) series of ``metric`` at ``frequency``."""
        points: list[MetricPoint] = []
        if frequency == "quarter":
            for e in self.quarter_ends(metric):
                q = self.quarter(metric, e)
                if q is not None:
                    points.append(q)
        elif frequency == "ttm":
            for e in self.quarter_ends(metric):
                p = self.ttm(metric, e)
                if p is not None:
                    points.append(p)
        elif frequency == "instant":
            for e in self.instant_ends(metric):
                p = self.instant(metric, e, tol_days=0)
                if p is not None:
                    points.append(p)
        else:  # fy
            ends = {e for td in self._tag_data(metric) for (_s, e) in td.durations["FY"]}
            for e in ends:
                p = self.fy(metric, e)
                if p is not None:
                    points.append(p)
        points.sort(key=lambda p: (p.end, p.start or date.min))
        tags = {p.xbrl_tag for p in points}
        return MetricSeries(
            name=metric, frequency=frequency, points=tuple(points), tag_changed=len(tags) > 1
        )

    def metrics(self) -> dict[str, MetricSeries]:
        """Non-empty series for ``AnalysisResult.metrics`` keyed ``"<Metric>.<frequency>"``."""
        out: dict[str, MetricSeries] = {}
        for metric in sorted(TAG_FALLBACKS):
            freqs: tuple[Frequency, ...] = (
                ("instant",) if metric in INSTANT_METRICS else ("quarter", "fy", "ttm")
            )
            for freq in freqs:
                s = self.series(metric, freq)
                if s.points:
                    out[f"{metric}.{freq}"] = s
        return out

    def restated_points(self) -> list[tuple[str, MetricPoint]]:
        """``(metric, point)`` for every as-reported fact flagged ``restated``.

        Only the tag the fallback would choose is considered per metric/period.
        """
        out: list[tuple[str, MetricPoint]] = []
        for metric in sorted(TAG_FALLBACKS):
            freqs: tuple[Frequency, ...] = (
                ("instant",) if metric in INSTANT_METRICS else ("quarter", "fy")
            )
            for freq in freqs:
                for p in self.series(metric, freq).points:
                    if p.restated:
                        out.append((metric, p))
        return out


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------
def _collect_rows(rows: Iterable[Any]) -> dict[_Period, list[tuple[date, str, str, float]]]:
    grouped: dict[_Period, list[tuple[date, str, str, float]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        form = row.get("form")
        if form not in ACCEPTED_FORMS:
            continue
        end = _parse_date(row.get("end"))
        filed = _parse_date(row.get("filed"))
        val = row.get("val")
        accn = row.get("accn")
        if end is None or filed is None or not isinstance(accn, str):
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        start = _parse_date(row.get("start")) if row.get("start") is not None else None
        if row.get("start") is not None and start is None:
            continue  # malformed start: drop rather than misread as an instant
        if start is not None and start > end:
            continue
        grouped.setdefault((start, end), []).append((filed, accn, str(form), float(val)))
    return grouped


def _dedup(
    tag: str, unit: str, grouped: dict[_Period, list[tuple[date, str, str, float]]]
) -> _TagData:
    td = _TagData(tag)
    for (start, end), rows in grouped.items():
        # Latest filed wins; ties broken by accession then value for determinism.
        ordered = sorted(rows, key=lambda r: (r[0], r[1], r[3], r[2]))
        earliest, latest = ordered[0], ordered[-1]
        point = MetricPoint(
            start=start,
            end=end,
            value=latest[3],
            unit=unit,
            xbrl_tag=tag,
            accession=latest[1],
            form=latest[2],
            filed=latest[0],
            derived=False,
            restated=_is_restated(earliest[3], latest[3]),
        )
        if start is None:
            td.instants[end] = point
            continue
        cls = duration_class(start, end)
        if cls is None:
            continue
        if cls == "FY" and not any(r[2] in ANNUAL_FORMS for r in rows):
            continue  # trailing-twelve-month column in a 10-Q, not a fiscal year
        td.durations[cls][(start, end)] = point
    return td


def _derive(
    big: MetricPoint, small: MetricPoint, comps: dict[MetricPoint, tuple[MetricPoint, ...]]
) -> MetricPoint | None:
    start = small.end + timedelta(days=1)
    if duration_class(start, big.end) != "3M":
        return None
    point = MetricPoint(
        start=start,
        end=big.end,
        value=big.value - small.value,
        unit=big.unit,
        xbrl_tag=big.xbrl_tag,
        accession=big.accession,
        form=big.form,
        filed=big.filed,
        derived=True,
        restated=big.restated or small.restated,
    )
    comps[point] = (small, big)
    return point


def _build_quarters(td: _TagData, comps: dict[MetricPoint, tuple[MetricPoint, ...]]) -> None:
    # 1) Reported 3M facts. Several starts for one end: latest filed, then earliest start.
    for (_s, e), p in sorted(
        td.durations["3M"].items(),
        key=lambda kv: (kv[1].filed, -(kv[0][0].toordinal())),
    ):
        td.quarters[e] = p
    # 2) YTD differencing: Q2 = 6M − 3M, Q3 = 9M − 6M, Q4 = FY − 9M (same tag, same start).
    for big_cls, small_cls in (("6M", "3M"), ("9M", "6M"), ("FY", "9M")):
        smalls_by_start: dict[date, list[MetricPoint]] = {}
        for (s, _e), p in td.durations[small_cls].items():  # type: ignore[index]
            smalls_by_start.setdefault(s, []).append(p)
        for (s, e), big in sorted(td.durations[big_cls].items()):  # type: ignore[index]
            if e in td.quarters:
                continue
            smalls = [p for p in smalls_by_start.get(s, []) if p.end < e]
            if not smalls:
                continue  # a component is missing → no value (§7.0.6)
            small = max(smalls, key=lambda p: p.end)
            derived = _derive(big, small, comps)
            if derived is not None:
                td.quarters[e] = derived


def normalize(companyfacts: dict[str, Any]) -> NormalizedFacts:
    """Normalize a companyfacts payload for every Appendix A tag (§7.0)."""
    facts = companyfacts.get("facts")
    taxonomy: Mapping[str, Any] = {}
    if isinstance(facts, Mapping):
        tx = facts.get(TAXONOMY)
        if isinstance(tx, Mapping):
            taxonomy = tx
    wanted: dict[str, str] = {}
    for metric, tag_list in TAG_FALLBACKS.items():
        for tag in tag_list:
            wanted.setdefault(tag, METRIC_UNITS.get(metric, DEFAULT_UNIT))
    tags: dict[str, _TagData] = {}
    comps: dict[MetricPoint, tuple[MetricPoint, ...]] = {}
    for tag in sorted(wanted):
        entry = taxonomy.get(tag)
        if not isinstance(entry, Mapping):
            continue
        units = entry.get("units")
        if not isinstance(units, Mapping):
            continue
        unit = wanted[tag]
        rows = units.get(unit)
        if not isinstance(rows, list):
            continue
        td = _dedup(tag, unit, _collect_rows(rows))
        _build_quarters(td, comps)
        if not td.empty:
            tags[tag] = td
    name = companyfacts.get("entityName")
    return NormalizedFacts(tags, comps, name if isinstance(name, str) else None)
