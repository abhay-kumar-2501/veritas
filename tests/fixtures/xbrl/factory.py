"""Builder for SYNTHETIC companyfacts payloads (fictional issuer, CIK 9999999).

It emits facts the way a real filer does, so tests exercise the normaliser end to end:

* ``flows`` (income-statement items): 10-Q Q1 3M; Q2 3M + 6M YTD; Q3 3M + 9M YTD;
  10-K FY. A 3M Q4 fact is never emitted, so Q4 is always derived (FY − 9M).
* ``ytd`` (cash-flow items): 10-Q Q1 3M, Q2 6M, Q3 9M, 10-K FY (no discrete 3M for
  Q2–Q4), so Q2–Q4 are always derived by YTD differencing.
* ``instants`` (balances): one fact at every quarter end.

A value of ``None`` means "not reported" for that quarter; YTD/FY facts that would
need the missing quarter are not emitted. Every expected value used by the tests is
computed by hand in the test docstrings from the inputs given here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

CIK = 9999999


@dataclass(frozen=True)
class Quarter:
    fy_start: date
    start: date
    end: date
    q: int  # 1..4
    fy_end: date


def _month_end(y: int, m: int) -> date:
    nxt = date(y + (m // 12), m % 12 + 1, 1)
    return nxt - timedelta(days=1)


def calendar(first_fy: int, n_years: int, fy_end_month: int = 12) -> list[Quarter]:
    """Quarters (oldest first) for fiscal years ending on the last day of ``fy_end_month``.

    ``first_fy`` is the calendar year of the first fiscal year's end.
    """
    out: list[Quarter] = []
    for k in range(n_years):
        fy_end = _month_end(first_fy + k, fy_end_month)
        prev_end = _month_end(first_fy + k - 1, fy_end_month)
        fy_start = prev_end + timedelta(days=1)
        start = fy_start
        for q in range(1, 5):
            m = (fy_end_month + 3 * q - 1) % 12 + 1
            y = fy_start.year + ((fy_start.month - 1 + 3 * q - 1) // 12)
            end = _month_end(y, m) if q < 4 else fy_end
            out.append(Quarter(fy_start, start, end, q, fy_end))
            start = end + timedelta(days=1)
    return out


def _accn(end: date, form: str) -> str:
    seq = end.month * 100 + end.day + (500000 if form == "10-K" else 0)
    return f"0009999999-{end.year % 100:02d}-{seq:06d}"


def fact(
    start: date | None,
    end: date,
    val: float,
    form: str,
    filed: date | None = None,
    accn: str | None = None,
) -> dict[str, Any]:
    filed = filed or end + timedelta(days=45 if form.startswith("10-K") else 35)
    row: dict[str, Any] = {
        "end": end.isoformat(),
        "val": val,
        "accn": accn or _accn(end, form),
        "fy": 1999,  # deliberately wrong: fy/fp/frame must never be read
        "fp": "Q9",
        "form": form,
        "filed": filed.isoformat(),
    }
    if start is not None:
        row["start"] = start.isoformat()
    return row


def build(
    quarters: list[Quarter],
    flows: dict[str, list[float | None]] | None = None,
    ytd: dict[str, list[float | None]] | None = None,
    instants: dict[str, list[float | None]] | None = None,
    extra: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {}

    def add(tag: str, r: dict[str, Any]) -> None:
        rows.setdefault(tag, []).append(r)

    for tag, vals in (flows or {}).items():
        assert len(vals) == len(quarters), tag
        for i, qt in enumerate(quarters):
            v = vals[i]
            form = "10-K" if qt.q == 4 else "10-Q"
            if v is not None and qt.q < 4:
                add(tag, fact(qt.start, qt.end, v, form))
            if qt.q >= 2:
                ytd_vals = vals[i - qt.q + 1 : i + 1]
                if all(x is not None for x in ytd_vals):
                    total = sum(x for x in ytd_vals if x is not None)
                    add(tag, fact(qt.fy_start, qt.end, total, form))
    for tag, vals in (ytd or {}).items():
        assert len(vals) == len(quarters), tag
        for i, qt in enumerate(quarters):
            form = "10-K" if qt.q == 4 else "10-Q"
            ytd_vals = vals[i - qt.q + 1 : i + 1]
            if all(x is not None for x in ytd_vals):
                total = sum(x for x in ytd_vals if x is not None)
                add(tag, fact(qt.fy_start, qt.end, total, form))
    for tag, vals in (instants or {}).items():
        assert len(vals) == len(quarters), tag
        for qt, v in zip(quarters, vals, strict=True):
            if v is not None:
                add(tag, fact(None, qt.end, v, "10-K" if qt.q == 4 else "10-Q"))
    for tag, extra_rows in (extra or {}).items():
        for r in extra_rows:
            add(tag, r)
    return {
        "cik": CIK,
        "entityName": "SYNTHETIC TEST CO",
        "facts": {"us-gaap": {tag: {"units": {"USD": r}} for tag, r in rows.items()}},
    }


def const(v: float | None, n: int) -> list[float | None]:
    return [v] * n
