"""Synthetic `AnalysisResult` factory for UI and PDF tests.

Everything here is FICTIONAL (issuer "SAMPLECO", CIK 9999999). It exists only to
exercise rendering; it must never be shown as real data or used in seed_cache.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from veritas.config import PARSER_VERSION
from veritas.models import (
    AnalysisResult,
    CheckResult,
    Citation,
    Claim,
    DocRef,
    Finding,
    MetricPoint,
    MetricSeries,
    ModuleResult,
    ScoreBreakdown,
    Severity,
)

CIK = 9999999
TICKER = "SAMPLECO"

TENK = DocRef(
    cik=CIK,
    ticker=TICKER,
    form="10-K",
    accession="0009999999-25-000010",
    filing_date=date(2025, 2, 20),
    report_date=date(2024, 12, 31),
    doc_name="sampleco-20241231.htm",
    url="https://www.sec.gov/Archives/edgar/data/9999999/000999999925000010/sampleco-20241231.htm",
)
PRIOR_TENK = DocRef(
    cik=CIK,
    ticker=TICKER,
    form="10-K",
    accession="0009999999-24-000008",
    filing_date=date(2024, 2, 21),
    report_date=date(2023, 12, 31),
    doc_name="sampleco-20231231.htm",
    url="https://www.sec.gov/Archives/edgar/data/9999999/000999999924000008/sampleco-20231231.htm",
)
PROXY = DocRef(
    cik=CIK,
    ticker=TICKER,
    form="DEF 14A",
    accession="0009999999-25-000030",
    filing_date=date(2025, 4, 1),
    report_date=None,
    doc_name="sampleco-def14a.htm",
    url="https://www.sec.gov/Archives/edgar/data/9999999/000999999925000030/sampleco-def14a.htm",
)


def _xbrl_cite(tag: str) -> Citation:
    return TENK.cite("XBRL", PARSER_VERSION, xbrl_tag=tag)


def _quote_cite(text: str, start: int = 1200) -> Citation:
    return TENK.cite("10-K Item 7", PARSER_VERSION, text, start, start + len(text))


def _history(base: float, step: float, n: int = 12) -> tuple[dict[str, Any], ...]:
    end = date(2024, 12, 31)
    out = []
    for i in range(n):
        d = end - timedelta(days=91 * (n - 1 - i))
        out.append({"period_end": d.isoformat(), "value": round(base + step * i, 2)})
    return tuple(out)


def _check(
    cid: str,
    title: str,
    severity: Severity | None,
    state: str = "COMPUTED",
    *,
    inputs: dict[str, float | None] | None = None,
    outputs: dict[str, float | None] | None = None,
    formula: str = "",
    reason: str | None = None,
    annotations: tuple[str, ...] = (),
    tag: str = "Revenues",
    scored: bool = True,
    history: tuple[dict[str, Any], ...] = (),
) -> CheckResult:
    return CheckResult(
        check_id=cid,
        title=title,
        state=state,  # type: ignore[arg-type]
        severity=severity,
        period_end=date(2024, 12, 31),
        inputs=inputs or {},
        outputs=outputs or {},
        formula=formula,
        reason=reason,
        annotations=annotations,
        citations=(_xbrl_cite(tag),),
        scored=scored,
        history=history,
    )


def sample_checks() -> tuple[CheckResult, ...]:
    return (
        _check(
            "A1_DSO",
            "Receivables outpacing sales (DSO up)",
            "WARNING",
            inputs={"AR_end": 1840.0, "Revenue_q": 2410.0, "days_q": 92.0, "DSO_prior": 58.1},
            outputs={"DSO_q": 70.2, "delta_DSO": 12.1, "rel": 0.208},
            formula="DSO_q = AR_end / Revenue_q × days(q)",
            tag="AccountsReceivableNetCurrent",
            history=_history(55, 1.4),
        ),
        _check(
            "A2_DIO",
            "Inventory outpacing cost of sales (DIO up)",
            "NO_FLAG",
            inputs={"Inventory_end": 900.0, "COGS_q": 1500.0, "days_q": 92.0},
            outputs={"DIO_q": 55.2, "delta_DIO": 1.0, "rel": 0.018},
            formula="DIO_q = Inventory_end / COGS_q × days(q)",
            tag="InventoryNet",
            history=_history(54, 0.1),
        ),
        _check(
            "A3_ACCRUALS",
            "Accruals / cash conversion",
            "WARNING",
            inputs={"NI_TTM": 1200.0, "OCF_TTM": 640.0, "avg_assets": 9800.0},
            outputs={"accrual_ratio": 0.057, "CCR": 0.53},
            formula="(NI_TTM − OCF_TTM) / avg(Assets_P, Assets_P−1y)",
            tag="NetCashProvidedByUsedInOperatingActivities",
            history=_history(0.01, 0.004),
        ),
        _check(
            "A4_CAPRATE",
            "Capitalization intensity",
            None,
            "NOT_COMPUTABLE",
            reason="Capitalized software not tagged",
            formula="CapSW_TTM / (CapSW_TTM + R&D_TTM)",
        ),
        _check(
            "A5_RESTRUCT",
            'Recurring "one-time" restructuring',
            "CRITICAL",
            inputs={"quarters_counted_last4": 4.0, "quarters_counted_last8": 7.0},
            outputs={"count_last4": 4.0, "count_last8": 7.0},
            formula="count of quarters with |R_q| ≥ 0.5% of revenue",
            tag="RestructuringCharges",
            history=_history(20, 3),
        ),
        _check(
            "X_FCF",
            "Free cash flow (TTM)",
            None,
            inputs={"OCF_TTM": 640.0, "CapEx_TTM": 410.0},
            outputs={"FCF": 230.0},
            formula="OCF_TTM − CapEx_TTM",
            scored=False,
        ),
    )


def _finding(i: int, check: CheckResult, severity: Severity, with_claim: bool) -> Finding:
    quote = (
        "We continued to see strong customer demand across all regions, "
        "supported by record bookings in the fourth quarter."
    )
    claims: tuple[Claim, ...] = ()
    if with_claim:
        claims = (Claim("DEMAND", _quote_cite(quote, 1200 + i * 10), date(2024, 12, 31)),)
    return Finding(
        id=f"A-{check.check_id}-{i}",
        module="A",
        category="accounting",
        severity=severity,
        title=check.title,
        explanation="Days sales outstanding rose year over year while management described demand "
        "as strong for the same period.",
        check=check,
        claims=claims,
        contradiction=with_claim and severity != "NO_FLAG",
        citations=check.citations + tuple(c.citation for c in claims),
    )


def sample_result(n_extra_findings: int = 0) -> AnalysisResult:
    checks = sample_checks()
    a1, _a2, a3, _a4, a5, _x = checks
    findings = [
        _finding(0, a1, "CRITICAL", True),
        _finding(1, a3, "WARNING", False),
        _finding(2, a5, "CRITICAL", False),
    ]
    for i in range(n_extra_findings):
        findings.append(_finding(10 + i, a1, "WARNING" if i % 2 else "CRITICAL", i % 3 == 0))

    redline = {
        "pairs": {
            "10-K": {
                "prior": PRIOR_TENK.to_dict(),
                "current": TENK.to_dict(),
                "notes": [
                    {
                        "key": "DebtDisclosureTextBlock",
                        "title": "Debt",
                        "category": "debt",
                        "status": "MATCHED",
                        "counts": {
                            "inserted": 1,
                            "deleted": 1,
                            "modified": 1,
                            "softened": 1,
                            "material": 2,
                        },
                        "rows": [
                            {
                                "kind": "MODIFIED",
                                "prior": "The Company will remain in compliance "
                                "with all covenants.",
                                "current": "The Company may not be able to remain in compliance "
                                "with all covenants.",
                                "material": True,
                                "softened": True,
                            },
                            {
                                "kind": "INSERTED",
                                "prior": None,
                                "current": "In the fourth quarter the Company obtained a waiver of "
                                "the leverage covenant under its revolving credit facility.",
                                "material": True,
                                "softened": False,
                            },
                        ],
                    }
                ],
            }
        },
        "hedging": {
            "status": "FIRED",
            "prior_rate": 6.1,
            "current_rate": 8.4,
            "prior_hits": 22,
            "current_hits": 34,
            "prior_tokens": 3600,
            "current_tokens": 4050,
        },
    }
    mod_b_finding = Finding(
        id="B-debt-1",
        module="B",
        category="footnotes",
        severity="WARNING",
        title="Material edit: Debt note",
        explanation="A covenant sentence moved from 'will' to 'may not be able to'.",
        check=None,
        claims=(),
        contradiction=False,
        citations=(TENK.cite("Note: DebtDisclosureTextBlock", PARSER_VERSION),),
    )
    proxy_extra = {
        "source": "DEF 14A CD&A",
        "metrics": [
            {
                "metric": "ADJ_EBITDA",
                "context": "annual incentive",
                "weight": 50.0,
                "sentence": "Adjusted EBITDA | 50% | annual incentive plan target",
                "citation": PROXY.cite("DEF 14A CD&A", PARSER_VERSION).to_dict(),
            },
            {
                "metric": "TSR",
                "context": "PSU",
                "weight": None,
                "sentence": "Relative total shareholder return determines PSU payout.",
                "citation": PROXY.cite("DEF 14A CD&A", PARSER_VERSION).to_dict(),
            },
        ],
        "notes": ["ROIC/FCF not identified among incentive metrics"],
    }
    mod_c_finding = Finding(
        id="C1",
        module="C",
        category="incentives",
        severity="WARNING",
        title="SBC vs non-GAAP pay metric",
        explanation="Adjusted EBITDA is an incentive metric while SBC is ≥ 10% of revenue.",
        check=None,
        claims=(),
        contradiction=False,
        citations=(PROXY.cite("DEF 14A CD&A", PARSER_VERSION),),
    )
    pts = (
        MetricPoint(
            start=date(2024, 10, 1),
            end=date(2024, 12, 31),
            value=2410.0,
            unit="USD",
            xbrl_tag="Revenues",
            accession=TENK.accession,
            form="10-K",
            filed=TENK.filing_date,
            derived=True,
            restated=False,
        ),
    )
    return AnalysisResult(
        ticker=TICKER,
        cik=CIK,
        company_name="SampleCo Holdings (synthetic)",
        sic="3571",
        fiscal_year_end="1231",
        as_of=datetime(2025, 5, 1, 12, 0, 0),
        source="seed",
        parser_version=PARSER_VERSION,
        filings=(
            TENK.cite("10-K", PARSER_VERSION),
            PRIOR_TENK.cite("10-K", PARSER_VERSION),
            PROXY.cite("DEF 14A", PARSER_VERSION),
        ),
        metrics={"Revenue.quarter": MetricSeries("Revenue", "quarter", pts, False)},
        modules={
            "A": ModuleResult("ok", tuple(findings), checks, None, {}),
            "B": ModuleResult("ok", (mod_b_finding,), (), None, redline),
            "C": ModuleResult("ok", (mod_c_finding,), (), None, proxy_extra),
        },
        score=ScoreBreakdown(
            score=58,
            coverage_num=4,
            coverage_den=5,
            deductions=(("accounting", 29), ("footnotes", 5), ("incentives", 5), ("hedging", 5)),
            label="Score 58 · coverage 4/5",
            scope_note=None,
            modules_assessed=3,
            unassessed=(),
        ),
        banners=("Synthetic sample data — not a real issuer.",),
    )
