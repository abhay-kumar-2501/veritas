"""Module A numeric accounting checks (PRD §7.1). Pure: no I/O.

Each check is evaluated at a discrete quarter end ``P``. Missing inputs give
``NOT_COMPUTABLE`` with a specific reason; failed materiality or sector gates give
``NOT_APPLICABLE``. No value is ever defaulted to 0 (§0.1.2), except the
Appendix A "total debt" rule, which explicitly counts missing components as 0.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from veritas.config import PARSER_VERSION, t, ti
from veritas.engine.gating import Gating
from veritas.engine.xbrl_normalize import NormalizedFacts, days, growth
from veritas.models import (
    SEVERITY_RANK,
    CheckResult,
    CheckState,
    Citation,
    MetricPoint,
    Severity,
)

log = logging.getLogger(__name__)

SCORED_CHECKS: tuple[str, ...] = (
    "A1_DSO",
    "A2_DIO",
    "A3_ACCRUALS",
    "A4_CAPRATE",
    "A5_RESTRUCT",
)
DISPLAY_CHECKS: tuple[str, ...] = (
    "X_CAPEX_DA",
    "X_DPO",
    "X_FCF",
    "X_FCF_SBC",
    "X_ROIC",
    "X_RESTATED",
)
ALL_CHECKS: tuple[str, ...] = SCORED_CHECKS + DISPLAY_CHECKS

TITLES: dict[str, str] = {
    "A1_DSO": "Receivables outpacing sales (DSO)",
    "A2_DIO": "Inventory outpacing cost of sales (DIO)",
    "A3_ACCRUALS": "Accruals / cash conversion",
    "A4_CAPRATE": "Capitalization intensity (software)",
    "A5_RESTRUCT": 'Recurring "one-time" restructuring',
    "X_CAPEX_DA": "CapEx / D&A",
    "X_DPO": "Days payable outstanding (DPO)",
    "X_FCF": "Free cash flow",
    "X_FCF_SBC": "Free cash flow after stock-based compensation",
    "X_ROIC": "Return on invested capital (approximate)",
    "X_RESTATED": "Restated values",
}

FORMULAS: dict[str, str] = {
    "A1_DSO": (
        "DSO_q = AR_end / Revenue_q × days(q); ΔDSO = DSO_q − DSO_q,−1y; rel = ΔDSO / DSO_q,−1y. "
        "WARNING: ΔDSO ≥ +5 days and rel ≥ +10%. CRITICAL: ΔDSO ≥ +10 days and rel ≥ +20% "
        "and WARNING also held at the previous quarter end."
    ),
    "A2_DIO": (
        "DIO_q = Inventory_end / COGS_q × days(q); ΔDIO = DIO_q − DIO_q,−1y; "
        "rel = ΔDIO / DIO_q,−1y. Same thresholds and persistence rule as A1."
    ),
    "A3_ACCRUALS": (
        "AccrualRatio = (NI_TTM − OCF_TTM) / avg(Assets_P, Assets_P−1y) "
        "(WARNING ≥ 5%, CRITICAL ≥ 10%); CCR = OCF_TTM / NI_TTM if NI_TTM > 0 and "
        "NI_TTM ≥ 2% of TTM revenue (WARNING < 0.75; CRITICAL < 0.5, or NI_TTM up YoY while "
        "OCF_TTM down ≥ 10%). Severity = max."
    ),
    "A4_CAPRATE": (
        "CapRate = CapSW_TTM / (CapSW_TTM + R&D_TTM); WARNING: up ≥ 5 pp YoY; "
        "CRITICAL: up ≥ 10 pp YoY."
    ),
    "A5_RESTRUCT": (
        "Quarter counts if |R_q| ≥ 0.5% of Revenue_q. WARNING: ≥ 3 of last 4 quarters. "
        "CRITICAL: ≥ 6 of last 8 quarters, or a material charge in each of the last 3 fiscal years."
    ),
    "X_CAPEX_DA": "CapEx_TTM / D&A_TTM (noted when ≥ 2.0 and up ≥ 25% rel YoY; D&A ≤ 0 → n/m)",
    "X_DPO": "DPO_q = AP_end / COGS_q × days(q) (noted when up ≥ 10 days and ≥ 20% rel YoY)",
    "X_FCF": "FCF = OCF_TTM − CapEx_TTM",
    "X_FCF_SBC": "FCF_SBC = OCF_TTM − CapEx_TTM − SBC_TTM",
    "X_ROIC": (
        "ROIC = OperatingIncome_TTM × (1 − t) / avg(TotalDebt + Equity − Cash) over P and P−1y; "
        "t = clamp(Tax_TTM / PreTax_TTM, 0, 0.35), 0.21 if PreTax_TTM ≤ 0; IC ≤ 0 → n/m"
    ),
    "X_RESTATED": "Metrics whose latest-filed value differs > 1% from the earliest-filed value",
}

# Output plotted in CheckResult.history for each check.
PRIMARY_OUTPUT: dict[str, str] = {
    "A1_DSO": "dso",
    "A2_DIO": "dio",
    "A3_ACCRUALS": "accrual_ratio",
    "A4_CAPRATE": "cap_rate",
    "A5_RESTRUCT": "material_quarters_last4",
    "X_CAPEX_DA": "capex_da",
    "X_DPO": "dpo",
    "X_FCF": "fcf",
    "X_FCF_SBC": "fcf_sbc",
    "X_ROIC": "roic",
    "X_RESTATED": "restated_metric_count",
}

ANN_ACQ = "acquisition-distorted"
ANN_TAG = "tag changed"
ANN_LOSS = "loss-making: cash conversion n/m"
ANN_FIN = "low reliability for financials"


@dataclass(frozen=True)
class AccountingOutput:
    checks: tuple[CheckResult, ...]
    acquisition_guard: bool
    latest_period: date | None


@dataclass
class _Ctx:
    nf: NormalizedFacts
    gating: Gating
    cik: int
    ticker: str
    period: date
    check_id: str
    inputs: dict[str, float | None] = field(default_factory=dict)
    outputs: dict[str, float | None] = field(default_factory=dict)
    annotations: list[str] = field(default_factory=list)
    points: list[MetricPoint] = field(default_factory=list)

    def use(self, *pts: MetricPoint | None) -> None:
        for p in pts:
            if p is not None:
                self.points.append(p)

    def note(self, text: str) -> None:
        if text not in self.annotations:
            self.annotations.append(text)

    def result(
        self, state: CheckState, severity: Severity | None = None, reason: str | None = None
    ) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=TITLES[self.check_id],
            state=state,
            severity=severity if state == "COMPUTED" else None,
            period_end=self.period,
            inputs=dict(self.inputs),
            outputs=dict(self.outputs),
            formula=FORMULAS[self.check_id],
            reason=reason,
            annotations=tuple(self.annotations),
            citations=citations_for(self.nf, self.points, self.cik, self.ticker),
            scored=self.check_id in SCORED_CHECKS,
        )

    def nc(self, reason: str) -> CheckResult:
        return self.result("NOT_COMPUTABLE", None, reason)

    def na(self, reason: str) -> CheckResult:
        return self.result("NOT_APPLICABLE", None, reason)

    def ok(self, severity: Severity, reason: str | None = None) -> CheckResult:
        return self.result("COMPUTED", severity, reason)


# --------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------
def _flatten(nf: NormalizedFacts, point: MetricPoint) -> list[MetricPoint]:
    comps = nf.components(point)
    if not comps:
        return [point]
    out: list[MetricPoint] = []
    for c in comps:
        out.extend(_flatten(nf, c))
    return out


def citation_for_point(point: MetricPoint, cik: int, ticker: str) -> Citation:
    acc_nodash = point.accession.replace("-", "")
    return Citation(
        cik=cik,
        ticker=ticker,
        form=point.form,
        accession=point.accession,
        filing_date=point.filed,
        report_date=None,
        doc_name="",
        url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/",
        section="XBRL",
        quote_raw=None,
        char_start=None,
        char_end=None,
        xbrl_tag=point.xbrl_tag,
        parser_version=PARSER_VERSION,
    )


def citations_for(
    nf: NormalizedFacts, points: Iterable[MetricPoint], cik: int, ticker: str
) -> tuple[Citation, ...]:
    """One XBRL citation per (accession, tag) among the source facts of ``points``."""
    seen: dict[tuple[str, str], Citation] = {}
    for p in points:
        for src in _flatten(nf, p):
            key = (src.accession, src.xbrl_tag)
            if key not in seen:
                seen[key] = citation_for_point(src, cik, ticker)
    return tuple(seen[k] for k in sorted(seen, key=lambda k: (seen[k].filing_date, k)))


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _tags(nf: NormalizedFacts, p: MetricPoint) -> set[str]:
    return {src.xbrl_tag for src in _flatten(nf, p)}


def _note_tag_change(ctx: _Ctx, *groups: Iterable[MetricPoint | None]) -> None:
    """Annotate when the same metric used different tags across compared periods."""
    for group in groups:
        tags: set[str] = set()
        for p in group:
            if p is not None:
                tags |= _tags(ctx.nf, p)
        if len(tags) > 1:
            ctx.note(ANN_TAG)
            return


def _max_sev(*sevs: Severity) -> Severity:
    return max(sevs, key=lambda s: SEVERITY_RANK[s])


def acquisition_guard(nf: NormalizedFacts, period: date) -> tuple[bool, dict[str, float | None]]:
    """Acquisition guard at ``period`` (§7.1): TTM acquisitions ≥ 5% of prior-year assets,
    or goodwill up ≥ 10% YoY. Missing data never triggers it."""
    acq = nf.ttm("Acquisitions", period)
    assets_prior = nf.yoy_instant("Assets", period)
    gw = nf.instant("Goodwill", period)
    gw_prior = nf.yoy_instant("Goodwill", period)
    acq_share: float | None = None
    if acq is not None and assets_prior is not None and assets_prior.value > 0:
        acq_share = acq.value / assets_prior.value
    gw_growth = growth(
        gw.value if gw else None,
        gw_prior.value if gw_prior else None,
    )
    active = (acq_share is not None and acq_share >= t("ACQ_PAYMENTS_PCT_ASSETS")) or (
        gw_growth is not None and gw_growth >= t("ACQ_GOODWILL_REL")
    )
    details: dict[str, float | None] = {
        "acquisitions_ttm": acq.value if acq else None,
        "assets_prior_year": assets_prior.value if assets_prior else None,
        "acquisitions_pct_prior_assets": acq_share,
        "goodwill": gw.value if gw else None,
        "goodwill_prior_year": gw_prior.value if gw_prior else None,
        "goodwill_growth": gw_growth,
    }
    return active, details


# --------------------------------------------------------------------------
# A1 / A2 — days-based turnover
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _DaysCfg:
    balance: str  # instant metric (AR / Inventory)
    flow: str  # quarterly flow (Revenue / COGS)
    label: str  # "DSO" / "DIO"
    materiality: str  # config key
    immaterial_reason: str
    require_balance_tag: bool  # A2: no inventory tag at all → NOT_APPLICABLE


_A1 = _DaysCfg(
    "AR",
    "Revenue",
    "dso",
    "AR_MATERIALITY_PCT",
    "receivables immaterial (AR < 2% of TTM revenue)",
    False,
)
_A2 = _DaysCfg(
    "Inventory",
    "COGS",
    "dio",
    "INV_MATERIALITY_PCT",
    "inventory immaterial (inventory < 2% of TTM cost of sales)",
    True,
)


def _days_ratio(
    nf: NormalizedFacts, cfg: _DaysCfg, period: date, bal_tol: int = 0
) -> tuple[float, float, float, float] | None:
    """(ratio, prior ratio, Δ days, rel) at ``period`` or None if not computable.

    The balance at ``period`` is matched exactly unless ``bal_tol`` is given (used for
    the previous-quarter persistence test); the prior-year balance uses the YoY tolerance.
    """
    q = nf.quarter(cfg.flow, period)
    bal = nf.instant(cfg.balance, period, tol_days=bal_tol)
    pq = nf.yoy_quarter(cfg.flow, period)
    if q is None or bal is None or pq is None or q.value <= 0 or pq.value <= 0:
        return None
    pbal = nf.instant(cfg.balance, pq.end, tol_days=ti("YOY_MATCH_TOL_DAYS"))
    if pbal is None:
        return None
    assert q.start is not None and pq.start is not None
    cur = bal.value / q.value * days(q.start, q.end)
    prior = pbal.value / pq.value * days(pq.start, pq.end)
    if prior <= 0:
        return None
    delta = cur - prior
    return cur, prior, delta, delta / prior


def _warn(delta: float, rel: float) -> bool:
    return delta >= t("DSO_WARN_DAYS") and rel >= t("DSO_WARN_REL")


def _crit(delta: float, rel: float) -> bool:
    return delta >= t("DSO_CRIT_DAYS") and rel >= t("DSO_CRIT_REL")


def _previous_warn(nf: NormalizedFacts, cfg: _DaysCfg, q: MetricPoint) -> tuple[date | None, bool]:
    """WARNING condition at the previous quarter end only (D-204).

    The previous quarter is the flow quarter ending at ``q.start − 1 day`` (± tolerance).
    If the comparison is not computable there, persistence is not established (False).
    """
    prev = nf.previous_quarter(cfg.flow, q)
    if prev is None:
        return None, False
    r = _days_ratio(nf, cfg, prev.end, bal_tol=ti("YOY_MATCH_TOL_DAYS"))
    if r is None:
        return prev.end, False
    return prev.end, _warn(r[2], r[3])


def _check_days(ctx: _Ctx, cfg: _DaysCfg) -> CheckResult:
    nf, p = ctx.nf, ctx.period
    name, flow, bal_name = cfg.label.upper(), cfg.flow, cfg.balance
    if cfg.require_balance_tag and not nf.has_metric(bal_name):
        return ctx.na("no inventory tag reported")
    q = nf.quarter(flow, p)
    bal = nf.instant(bal_name, p)
    ctx.use(q, bal)
    ctx.inputs[f"{flow}_q"] = q.value if q else None
    ctx.inputs[f"{bal_name}_end"] = bal.value if bal else None
    if q is None:
        return ctx.nc(f"{flow} not tagged for the quarter ending {p.isoformat()}")
    if bal is None:
        return ctx.nc(f"{bal_name} not tagged at {p.isoformat()}")
    assert q.start is not None
    ctx.inputs["days_q"] = float(days(q.start, q.end))
    if q.value <= 0:
        return ctx.nc(f"{flow}_q ≤ 0: {name} undefined")
    ttm = nf.ttm(flow, p)
    ctx.use(ttm)
    ctx.inputs[f"{flow}_ttm"] = ttm.value if ttm else None
    if ttm is None:
        return ctx.nc(f"TTM {flow} unavailable (4 discrete quarters required)")
    if bal.value < t(cfg.materiality) * ttm.value:
        return ctx.na(cfg.immaterial_reason)
    pq = nf.yoy_quarter(flow, p)
    if pq is None:
        return ctx.nc(f"prior-year quarter missing ({flow})")
    pbal = nf.instant(bal_name, pq.end, tol_days=ti("YOY_MATCH_TOL_DAYS"))
    ctx.use(pq, pbal)
    ctx.inputs[f"{flow}_q_prior_year"] = pq.value
    ctx.inputs[f"{bal_name}_end_prior_year"] = pbal.value if pbal else None
    if pbal is None:
        return ctx.nc(f"prior-year {bal_name} missing at {pq.end.isoformat()}")
    if pq.value <= 0:
        return ctx.nc(f"prior-year {flow}_q ≤ 0: {name} undefined")
    _note_tag_change(ctx, (q, pq), (bal, pbal), (ttm,))
    r = _days_ratio(nf, cfg, p)
    if r is None:
        return ctx.nc(f"prior-year {name} ≤ 0: relative change undefined")
    cur, prior, delta, rel = r
    key = cfg.label
    ctx.outputs.update(
        {key: cur, f"{key}_prior_year": prior, f"delta_{key}_days": delta, f"{key}_rel_change": rel}
    )
    severity: Severity = "NO_FLAG"
    reason: str | None = None
    if _warn(delta, rel):
        severity = "WARNING"
        if _crit(delta, rel):
            prev_end, prev_warn = _previous_warn(nf, cfg, q)
            if prev_warn:
                severity = "CRITICAL"
            else:
                reason = (
                    "CRITICAL thresholds met but persistence not established: the WARNING "
                    "condition did not hold (or was not computable) at the previous quarter end"
                    + (f" ({prev_end.isoformat()})" if prev_end else " (no prior-quarter data)")
                )
    guard, gdetails = acquisition_guard(nf, p)
    ctx.inputs.update(gdetails)
    if guard:
        ctx.note(ANN_ACQ)
        if severity == "CRITICAL":
            severity = "WARNING"
    return ctx.ok(severity, reason)


# --------------------------------------------------------------------------
# A3 — accruals / cash conversion
# --------------------------------------------------------------------------
def _check_a3(ctx: _Ctx) -> CheckResult:
    nf, p = ctx.nf, ctx.period
    ni = nf.ttm("NetIncome", p)
    ocf = nf.ttm("OCF", p)
    assets = nf.instant("Assets", p)
    assets_prior = nf.yoy_instant("Assets", p)
    ctx.use(ni, ocf, assets, assets_prior)
    ctx.inputs.update(
        {
            "NI_ttm": ni.value if ni else None,
            "OCF_ttm": ocf.value if ocf else None,
            "Assets_end": assets.value if assets else None,
            "Assets_end_prior_year": assets_prior.value if assets_prior else None,
        }
    )
    if ni is None:
        return ctx.nc("TTM net income unavailable (4 discrete quarters required)")
    if ocf is None:
        return ctx.nc("TTM operating cash flow unavailable (4 discrete quarters required)")
    if assets is None:
        return ctx.nc(f"Assets not tagged at {p.isoformat()}")
    if assets_prior is None:
        return ctx.nc("prior-year Assets missing")
    avg_assets = (assets.value + assets_prior.value) / 2
    if avg_assets <= 0:
        return ctx.nc("average assets ≤ 0: accrual ratio undefined")
    ratio = (ni.value - ocf.value) / avg_assets
    ctx.outputs["accrual_ratio"] = ratio
    primary: Severity = "NO_FLAG"
    if ratio >= t("ACCRUAL_CRIT"):
        primary = "CRITICAL"
    elif ratio >= t("ACCRUAL_WARN"):
        primary = "WARNING"

    secondary: Severity = "NO_FLAG"
    ccr: float | None = None
    rev = nf.ttm("Revenue", p)
    ctx.inputs["Revenue_ttm"] = rev.value if rev else None
    if ni.value <= 0:
        ctx.note(ANN_LOSS)
    elif rev is None:
        ctx.note("cash conversion n/m: TTM revenue unavailable")
    elif ni.value < t("CCR_NI_MIN_PCT_REV") * rev.value:
        ctx.note("cash conversion n/m: net income below 2% of revenue")
    else:
        ctx.use(rev)
        ccr = ocf.value / ni.value
        if ccr < t("CCR_CRIT"):
            secondary = "CRITICAL"
        elif ccr < t("CCR_WARN"):
            secondary = "WARNING"
        ni_prior = nf.yoy_ttm("NetIncome", p)
        ocf_prior = nf.yoy_ttm("OCF", p)
        ctx.use(ni_prior, ocf_prior)
        ctx.inputs["NI_ttm_prior_year"] = ni_prior.value if ni_prior else None
        ctx.inputs["OCF_ttm_prior_year"] = ocf_prior.value if ocf_prior else None
        ocf_g = growth(ocf.value, ocf_prior.value if ocf_prior else None)
        ctx.outputs["OCF_ttm_growth"] = ocf_g
        if (
            ni_prior is not None
            and ni.value > ni_prior.value
            and ocf_g is not None
            and ocf_g <= -t("OCF_FALL_REL")
        ):
            secondary = "CRITICAL"
            ctx.note("net income up YoY while operating cash flow fell ≥ 10%")
        _note_tag_change(ctx, (ni, ni_prior), (ocf, ocf_prior))
    ctx.outputs["ccr"] = ccr
    _note_tag_change(ctx, (ni,), (ocf,), (assets, assets_prior))
    if ctx.gating.kind == "financial":
        ctx.note(ANN_FIN)
    return ctx.ok(_max_sev(primary, secondary))


# --------------------------------------------------------------------------
# A4 — capitalization intensity
# --------------------------------------------------------------------------
def _cap_rate(nf: NormalizedFacts, end: date) -> tuple[MetricPoint | None, MetricPoint | None]:
    return nf.ttm("CapSW", end), nf.ttm("RnD", end)


def _check_a4(ctx: _Ctx) -> CheckResult:
    nf, p = ctx.nf, ctx.period
    if not nf.has_metric("CapSW"):
        return ctx.nc("capitalized software not tagged")
    cap, rnd = _cap_rate(nf, p)
    ctx.use(cap, rnd)
    ctx.inputs["CapSW_ttm"] = cap.value if cap else None
    ctx.inputs["RnD_ttm"] = rnd.value if rnd else None
    if cap is None:
        return ctx.nc("TTM capitalized software unavailable (4 discrete quarters required)")
    if rnd is None:
        return ctx.nc("TTM R&D expense unavailable (ResearchAndDevelopmentExpense)")
    rev = nf.ttm("Revenue", p)
    ctx.use(rev)
    ctx.inputs["Revenue_ttm"] = rev.value if rev else None
    if rev is None:
        return ctx.nc("TTM revenue unavailable for the materiality gate")
    if cap.value < t("CAPSW_MATERIALITY_PCT") * rev.value:
        return ctx.na("capitalized software immaterial (< 1% of TTM revenue)")
    if cap.value + rnd.value <= 0:
        return ctx.nc("CapSW_TTM + R&D_TTM ≤ 0: CapRate undefined")
    rate = cap.value / (cap.value + rnd.value)
    ctx.outputs["cap_rate"] = rate
    pend = nf.prior_year_quarter_end(p, "CapSW")
    pcap, prnd = _cap_rate(nf, pend) if pend else (None, None)
    ctx.use(pcap, prnd)
    ctx.inputs["CapSW_ttm_prior_year"] = pcap.value if pcap else None
    ctx.inputs["RnD_ttm_prior_year"] = prnd.value if prnd else None
    if pcap is None or prnd is None:
        return ctx.nc("prior-year TTM capitalized software or R&D missing")
    if pcap.value + prnd.value <= 0:
        return ctx.nc("prior-year CapSW_TTM + R&D_TTM ≤ 0: CapRate undefined")
    prate = pcap.value / (pcap.value + prnd.value)
    delta = rate - prate
    ctx.outputs["cap_rate_prior_year"] = prate
    ctx.outputs["cap_rate_change_pp"] = delta
    _note_tag_change(ctx, (cap, pcap), (rnd, prnd))
    severity: Severity = "NO_FLAG"
    if delta >= t("CAPRATE_CRIT_PP"):
        severity = "CRITICAL"
    elif delta >= t("CAPRATE_WARN_PP"):
        severity = "WARNING"
    return ctx.ok(severity)


# --------------------------------------------------------------------------
# A5 — recurring restructuring
# --------------------------------------------------------------------------
def _material(r: MetricPoint | None, rev: MetricPoint | None) -> bool:
    return (
        r is not None
        and rev is not None
        and rev.value > 0
        and abs(r.value) >= t("RESTRUCT_MATERIAL_PCT") * rev.value
    )


def _check_a5(ctx: _Ctx) -> CheckResult:
    nf, p = ctx.nf, ctx.period
    if not nf.has_metric("Restructuring"):
        ctx.note("no restructuring tags reported")
        return ctx.ok("NO_FLAG", "no restructuring tags reported")
    rev_p = nf.quarter("Revenue", p)
    r_p = nf.quarter("Restructuring", p)
    ctx.use(rev_p, r_p)
    ctx.inputs["Restructuring_q"] = r_p.value if r_p else None
    ctx.inputs["Revenue_q"] = rev_p.value if rev_p else None
    for metric in ("GoodwillImpairment", "AssetImpairment", "LongLivedImpairment"):
        imp = nf.quarter(metric, p)
        ctx.use(imp)
        ctx.inputs[f"{metric}_q (display only)"] = imp.value if imp else None
    if rev_p is None:
        return ctx.nc(f"Revenue not tagged for the quarter ending {p.isoformat()}")
    win4, win8 = ti("RESTRUCT_WARN_WINDOW"), ti("RESTRUCT_CRIT_WINDOW")
    ends = [e for e in nf.quarter_ends("Revenue") if e <= p][:win8]
    flags: list[bool] = []
    with_data: list[bool] = []
    unknown: list[date] = []
    unknown_flags: list[bool] = []
    for e in ends:
        r = nf.quarter("Restructuring", e)
        rev = nf.quarter("Revenue", e)
        m = _material(r, rev)
        # No discrete value although a YTD/FY total spans the quarter → not derivable:
        # unknown, never silently "not material" (D-209).
        unk = rev is None or (
            r is None
            and rev.start is not None
            and nf.quarter_covered("Restructuring", rev.start, rev.end)
        )
        flags.append(m)
        unknown_flags.append(unk)
        if unk:
            unknown.append(e)
        with_data.append(r is not None and rev is not None)
        if m:
            ctx.use(r, rev)
    count4, count8 = sum(flags[:win4]), sum(flags[:win8])
    unk4, unk8 = sum(unknown_flags[:win4]), sum(unknown_flags[:win8])
    ctx.outputs["material_quarters_last4"] = float(count4)
    ctx.outputs["material_quarters_last8"] = float(count8)
    ctx.outputs["quarters_with_data_last4"] = float(sum(with_data[:win4]))
    ctx.outputs["quarters_with_data_last8"] = float(sum(with_data[:win8]))
    ctx.outputs["quarters_unknown_last4"] = float(unk4)
    ctx.outputs["quarters_unknown_last8"] = float(unk8)
    if unknown:
        ctx.note(
            "restructuring quarter(s) not derivable: "
            + ", ".join(d.isoformat() for d in sorted(unknown))
        )

    n_years = ti("RESTRUCT_CRIT_YEARS")
    fys = [fy for fy in nf.fiscal_years() if fy[1] <= p][:n_years]
    fy_material = 0
    for _s, e in fys:
        r_fy, rev_fy = nf.fy("Restructuring", e), nf.fy("Revenue", e)
        if _material(r_fy, rev_fy):
            fy_material += 1
            ctx.use(r_fy, rev_fy)
    ctx.outputs["material_fiscal_years_last3"] = float(fy_material)
    years_ok = len(fys) == n_years and fy_material == n_years
    severity: Severity = "NO_FLAG"
    if count8 >= ti("RESTRUCT_CRIT_COUNT") or years_ok:
        severity = "CRITICAL"
    elif count4 >= ti("RESTRUCT_WARN_COUNT"):
        severity = "WARNING"
    reason: str | None = None
    could_crit = severity != "CRITICAL" and count8 + unk8 >= ti("RESTRUCT_CRIT_COUNT")
    could_warn = severity == "NO_FLAG" and count4 + unk4 >= ti("RESTRUCT_WARN_COUNT")
    if could_crit or could_warn:
        reason = "severity uses known quarters only; non-derivable quarters could raise it to " + (
            "CRITICAL" if could_crit else "WARNING"
        )
    return ctx.ok(severity, reason)


# --------------------------------------------------------------------------
# Display-only metrics
# --------------------------------------------------------------------------
def _check_capex_da(ctx: _Ctx) -> CheckResult:
    nf, p = ctx.nf, ctx.period
    capex, da = nf.ttm("CapEx", p), nf.ttm("DA", p)
    ctx.use(capex, da)
    ctx.inputs["CapEx_ttm"] = capex.value if capex else None
    ctx.inputs["DA_ttm"] = da.value if da else None
    if capex is None:
        return ctx.nc("TTM CapEx unavailable (4 discrete quarters required)")
    if da is None:
        return ctx.nc("TTM D&A unavailable (4 discrete quarters required)")
    if da.value <= 0:
        return ctx.nc("n/m: D&A_TTM ≤ 0")
    ratio = capex.value / da.value
    ctx.outputs["capex_da"] = ratio
    pcap, pda = nf.yoy_ttm("CapEx", p), nf.yoy_ttm("DA", p)
    prior = pcap.value / pda.value if pcap and pda and pda.value > 0 else None
    if prior is not None:
        ctx.use(pcap, pda)
    rel = growth(ratio, prior)
    ctx.outputs["capex_da_prior_year"] = prior
    ctx.outputs["capex_da_rel_change"] = rel
    _note_tag_change(ctx, (capex, pcap), (da, pda))
    if ratio >= t("CAPEX_DA_NOTE") and rel is not None and rel >= t("CAPEX_DA_NOTE_REL"):
        ctx.note("CapEx at least 2x D&A and rising")
    return ctx.ok("NO_FLAG")


def _check_dpo(ctx: _Ctx) -> CheckResult:
    nf, p = ctx.nf, ctx.period
    q, ap = nf.quarter("COGS", p), nf.instant("AP", p)
    ctx.use(q, ap)
    ctx.inputs["COGS_q"] = q.value if q else None
    ctx.inputs["AP_end"] = ap.value if ap else None
    if q is None:
        return ctx.nc(f"COGS not tagged for the quarter ending {p.isoformat()}")
    if ap is None:
        return ctx.nc(f"AP not tagged at {p.isoformat()}")
    if q.value <= 0:
        return ctx.nc("COGS_q ≤ 0: DPO undefined")
    assert q.start is not None
    dpo = ap.value / q.value * days(q.start, q.end)
    ctx.outputs["dpo"] = dpo
    pq = nf.yoy_quarter("COGS", p)
    pap = nf.instant("AP", pq.end, tol_days=ti("YOY_MATCH_TOL_DAYS")) if pq else None
    prior: float | None = None
    if pq is not None and pap is not None and pq.value > 0 and pq.start is not None:
        prior = pap.value / pq.value * days(pq.start, pq.end)
        ctx.use(pq, pap)
    ctx.outputs["dpo_prior_year"] = prior
    delta = dpo - prior if prior is not None else None
    rel = growth(dpo, prior)
    ctx.outputs["delta_dpo_days"] = delta
    ctx.outputs["dpo_rel_change"] = rel
    _note_tag_change(ctx, (q, pq), (ap, pap))
    if (
        delta is not None
        and rel is not None
        and delta >= t("DPO_NOTE_DAYS")
        and rel >= t("DPO_NOTE_REL")
    ):
        ctx.note("payables stretch supporting OCF")
    return ctx.ok("NO_FLAG")


def _fcf(ctx: _Ctx, with_sbc: bool) -> CheckResult:
    nf, p = ctx.nf, ctx.period
    ocf, capex = nf.ttm("OCF", p), nf.ttm("CapEx", p)
    ctx.use(ocf, capex)
    ctx.inputs["OCF_ttm"] = ocf.value if ocf else None
    ctx.inputs["CapEx_ttm"] = capex.value if capex else None
    if ocf is None:
        return ctx.nc("TTM operating cash flow unavailable (4 discrete quarters required)")
    if capex is None:
        return ctx.nc("TTM CapEx unavailable (4 discrete quarters required)")
    fcf = ocf.value - capex.value
    ctx.outputs["fcf"] = fcf
    if with_sbc:
        sbc = nf.ttm("SBC", p)
        ctx.use(sbc)
        ctx.inputs["SBC_ttm"] = sbc.value if sbc else None
        if sbc is None:
            return ctx.nc("TTM stock-based compensation unavailable (4 discrete quarters required)")
        ctx.outputs["fcf_sbc"] = fcf - sbc.value
    return ctx.ok("NO_FLAG")


def total_debt(
    nf: NormalizedFacts, end: date, tol_days: int = 0
) -> tuple[float | None, list[MetricPoint]]:
    """Appendix A approximate total debt; missing components count as 0, all missing → None."""

    def inst(metric: str) -> MetricPoint | None:
        return nf.instant(metric, end, tol_days=tol_days)

    ltd = inst("LongTermDebt")
    parts: list[MetricPoint | None]
    if ltd is not None:
        parts = [ltd]
    else:
        parts = [inst("LongTermDebtNoncurrent"), inst("LongTermDebtCurrent")]
    parts += [inst("ShortTermBorrowings"), inst("CommercialPaper")]
    present = [x for x in parts if x is not None]
    if not present:
        return None, []
    return sum(x.value for x in present), present


def _invested_capital(ctx: _Ctx, end: date | None, suffix: str) -> tuple[float | None, str | None]:
    if end is None:
        return None, f"prior-year quarter end missing{suffix}"
    tol = ti("YOY_MATCH_TOL_DAYS") if suffix else 0  # exact at P, YoY tolerance prior
    debt, debt_pts = total_debt(ctx.nf, end, tol)
    eq = ctx.nf.instant("Equity", end, tol_days=tol)
    cash = ctx.nf.instant("Cash", end, tol_days=tol)
    ctx.use(*debt_pts, eq, cash)
    ctx.inputs[f"TotalDebt_approx{suffix}"] = debt
    ctx.inputs[f"Equity{suffix}"] = eq.value if eq else None
    ctx.inputs[f"Cash{suffix}"] = cash.value if cash else None
    if debt is None:
        return None, f"no debt tags reported{suffix}"
    if eq is None:
        return None, f"stockholders' equity not tagged{suffix}"
    if cash is None:
        return None, f"cash not tagged{suffix}"
    return debt + eq.value - cash.value, None


def _check_roic(ctx: _Ctx) -> CheckResult:
    nf, p = ctx.nf, ctx.period
    oi, tax, pti = nf.ttm("OperatingIncome", p), nf.ttm("Tax", p), nf.ttm("PreTaxIncome", p)
    ctx.use(oi, tax, pti)
    ctx.inputs["OperatingIncome_ttm"] = oi.value if oi else None
    ctx.inputs["Tax_ttm"] = tax.value if tax else None
    ctx.inputs["PreTaxIncome_ttm"] = pti.value if pti else None
    if oi is None:
        return ctx.nc("TTM operating income unavailable (4 discrete quarters required)")
    if pti is None:
        return ctx.nc("TTM pre-tax income unavailable (4 discrete quarters required)")
    if pti.value <= 0:
        rate = t("TAX_RATE_DEFAULT")
    else:
        if tax is None:
            return ctx.nc("TTM income tax expense unavailable (4 discrete quarters required)")
        rate = min(max(tax.value / pti.value, 0.0), t("TAX_RATE_MAX"))
    nopat = oi.value * (1 - rate)
    ctx.outputs["tax_rate"] = rate
    ctx.outputs["nopat"] = nopat
    ic_now, why = _invested_capital(ctx, p, "")
    if ic_now is None:
        return ctx.nc(why or "invested capital unavailable")
    prior_end = nf.prior_year_quarter_end(p)
    ic_prior, why = _invested_capital(ctx, prior_end, "_prior_year")
    if ic_prior is None:
        return ctx.nc(why or "prior-year invested capital unavailable")
    ic = (ic_now + ic_prior) / 2
    ctx.outputs["invested_capital"] = ic
    ctx.note("total debt approximate (Appendix A)")
    if ic <= 0:
        ctx.outputs["roic"] = None
        return ctx.nc("n/m: invested capital ≤ 0")
    ctx.outputs["roic"] = nopat / ic
    return ctx.ok("NO_FLAG")


def _restated_window(nf: NormalizedFacts, p: date) -> list[tuple[str, MetricPoint]]:
    ends = [e for e in nf.quarter_ends() if e <= p][: ti("HISTORY_QUARTERS")]
    if not ends:
        return []
    lo = ends[-1]
    return [(m, pt) for m, pt in nf.restated_points() if lo <= pt.end <= p]


def _check_restated(ctx: _Ctx) -> CheckResult:
    hits = _restated_window(ctx.nf, ctx.period)
    counts: dict[str, int] = {}
    for metric, pt in hits:
        counts[metric] = counts.get(metric, 0) + 1
        ctx.use(pt)
    for metric in sorted(counts):
        ctx.outputs[f"{metric}_restated_points"] = float(counts[metric])
        ctx.note(f"restated: {metric}")
    ctx.outputs["restated_metric_count"] = float(len(counts))
    reason = None if counts else "no restated values in the last 12 quarters"
    return ctx.ok("NO_FLAG", reason)


_DISPATCH: dict[str, Callable[[_Ctx], CheckResult]] = {
    "A1_DSO": lambda c: _check_days(c, _A1),
    "A2_DIO": lambda c: _check_days(c, _A2),
    "A3_ACCRUALS": _check_a3,
    "A4_CAPRATE": _check_a4,
    "A5_RESTRUCT": _check_a5,
    "X_CAPEX_DA": _check_capex_da,
    "X_DPO": _check_dpo,
    "X_FCF": lambda c: _fcf(c, False),
    "X_FCF_SBC": lambda c: _fcf(c, True),
    "X_ROIC": _check_roic,
    "X_RESTATED": _check_restated,
}

_FINANCIAL_NA = frozenset({"A1_DSO", "A2_DIO", "A4_CAPRATE"})


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def evaluate_check(
    check_id: str,
    nf: NormalizedFacts,
    period_end: date,
    gating: Gating,
    cik: int,
    ticker: str,
) -> CheckResult:
    """Evaluate one check at discrete quarter end ``period_end`` (§7.1)."""
    if check_id not in _DISPATCH:
        raise KeyError(f"unknown check {check_id!r}")
    ctx = _Ctx(nf, gating, cik, ticker, period_end, check_id)
    if gating.kind in ("shell", "unsupported"):
        return ctx.na(f"{gating.kind} issuer: {gating.reason}")
    if gating.kind == "financial" and check_id in _FINANCIAL_NA:
        return ctx.na("financial issuer")
    res = _DISPATCH[check_id](ctx)
    if gating.kind == "financial" and check_id == "A3_ACCRUALS":
        anns = res.annotations if ANN_FIN in res.annotations else (*res.annotations, ANN_FIN)
        res = replace(res, annotations=anns, scored=False)
    return res


def evaluation_quarters(nf: NormalizedFacts) -> list[date]:
    """Quarter ends used for evaluation, newest first: Revenue quarters, else all metrics."""
    ends = nf.quarter_ends("Revenue")
    return ends if ends else nf.quarter_ends()


def latest_period(nf: NormalizedFacts) -> date | None:
    ends = evaluation_quarters(nf)
    return ends[0] if ends else None


def _unavailable(check_id: str, cik: int, ticker: str, reason: str) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=TITLES[check_id],
        state="NOT_COMPUTABLE",
        severity=None,
        period_end=None,
        inputs={},
        outputs={},
        formula=FORMULAS[check_id],
        reason=reason,
        annotations=(),
        citations=(),
        scored=check_id in SCORED_CHECKS,
    )


def run_accounting(nf: NormalizedFacts, gating: Gating, cik: int, ticker: str) -> AccountingOutput:
    """All checks at the latest quarter with data, with 12-quarter history (§7.1)."""
    latest = latest_period(nf)
    if latest is None:
        checks = tuple(
            _unavailable(c, cik, ticker, "no discrete quarterly data in XBRL") for c in ALL_CHECKS
        )
        if gating.kind in ("shell", "unsupported"):
            checks = tuple(
                replace(c, state="NOT_APPLICABLE", reason=f"{gating.kind} issuer: {gating.reason}")
                for c in checks
            )
        return AccountingOutput(checks, False, None)
    hist_ends = sorted(evaluation_quarters(nf)[: ti("HISTORY_QUARTERS")])
    results: list[CheckResult] = []
    for cid in ALL_CHECKS:
        primary = evaluate_check(cid, nf, latest, gating, cik, ticker)
        key = PRIMARY_OUTPUT[cid]
        history: list[dict[str, Any]] = []
        for e in hist_ends:
            r = primary if e == latest else evaluate_check(cid, nf, e, gating, cik, ticker)
            history.append({"period_end": e.isoformat(), "value": r.outputs.get(key)})
        results.append(replace(primary, history=tuple(history)))
    guard = gating.kind not in ("shell", "unsupported") and acquisition_guard(nf, latest)[0]
    return AccountingOutput(tuple(results), guard, latest)
