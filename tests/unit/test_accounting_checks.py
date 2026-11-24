"""§7.1 accounting checks on synthetic companyfacts (tests/fixtures/xbrl/factory.py).

Default synthetic issuer: Dec fiscal year, 12 quarters 2022-Q1 … 2024-Q4 (index 0…11).
Latest quarter P = 2024-12-31 (92 days); prior-year quarter 2023-12-31 (92 days);
previous quarter 2024-09-30 (92 days, prior-year 2023-09-30, 92 days).
Defaults per quarter: Revenue 1000, COGS 600, NI 300, OCF 300; AR 500, Inventory 300,
Assets 10000. TTM revenue 4000, TTM COGS 2400.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import pytest

from tests.fixtures.xbrl.factory import CIK, build, calendar, const, fact
from veritas.engine.accounting_checks import (
    acquisition_guard,
    evaluate_check,
    run_accounting,
    total_debt,
)
from veritas.engine.gating import Gating
from veritas.engine.xbrl_normalize import NormalizedFacts, normalize
from veritas.models import CheckResult

QS = calendar(2022, 3)
N = len(QS)
P = date(2024, 12, 31)
ORD = Gating("ordinary", None, "ordinary filer")
FIN = Gating("financial", "Financial issuer: limited scope", "SIC 6022: financial issuer")
SHELL = Gating("shell", "shell", "SIC 6770 (blank check / shell company)")

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"
COGS = "CostOfGoodsAndServicesSold"
AR = "AccountsReceivableNetCurrent"
INV = "InventoryNet"
NI = "NetIncomeLoss"
OCF = "NetCashProvidedByUsedInOperatingActivities"
ASSETS = "Assets"
GW = "Goodwill"
ACQ = "PaymentsToAcquireBusinessesNetOfCashAcquired"
CAPSW = "PaymentsToDevelopSoftware"
RND = "ResearchAndDevelopmentExpense"
RESTR = "RestructuringCharges"
CAPEX = "PaymentsToAcquirePropertyPlantAndEquipment"
DA = "DepreciationDepletionAndAmortization"
SBC = "ShareBasedCompensation"
AP = "AccountsPayableCurrent"
PRETAX = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
)

Vals = list[float | None]


def series(default: float | None, **at: float | None) -> Vals:
    """12 quarterly values; override by index with i<k>=value."""
    out = const(default, N)
    for k, v in at.items():
        out[int(k[1:])] = v
    return out


def by_year(v22: float | None, v23: float | None, v24: float | None) -> Vals:
    return [v22] * 4 + [v23] * 4 + [v24] * 4


def company(
    flows: Mapping[str, Sequence[float | None]] | None = None,
    ytd: Mapping[str, Sequence[float | None]] | None = None,
    instants: Mapping[str, Sequence[float | None]] | None = None,
    drop: tuple[str, ...] = (),
    extra: dict[str, list[dict[str, Any]]] | None = None,
) -> NormalizedFacts:
    f: dict[str, Vals] = {REV: const(1000.0, N), COGS: const(600.0, N), NI: const(300.0, N)}
    y: dict[str, Vals] = {OCF: const(300.0, N)}
    i: dict[str, Vals] = {AR: const(500.0, N), INV: const(300.0, N), ASSETS: const(10000.0, N)}
    f.update({k: list(v) for k, v in (flows or {}).items()})
    y.update({k: list(v) for k, v in (ytd or {}).items()})
    i.update({k: list(v) for k, v in (instants or {}).items()})
    for d in (f, y, i):
        for tag in drop:
            d.pop(tag, None)
    return normalize(build(QS, flows=f, ytd=y, instants=i, extra=extra))


def ev(check_id: str, nf: NormalizedFacts, gating: Gating = ORD, p: date = P) -> CheckResult:
    return evaluate_check(check_id, nf, p, gating, CIK, "SYN")


# ====================================================================== A1
def test_a1_critical_with_persistence() -> None:
    """DSO_P = 700/1000×92 = 64.4; prior 500/1000×92 = 46.0 → Δ 18.4 d, rel 40%.
    Previous quarter 2024-09-30: 600 → 55.2 vs 46.0 → Δ 9.2, rel 20% (WARNING held)."""
    nf = company(instants={AR: series(500.0, i10=600.0, i11=700.0)})
    r = ev("A1_DSO", nf)
    assert r.state == "COMPUTED" and r.severity == "CRITICAL"
    assert r.outputs["dso"] == pytest.approx(64.4)
    assert r.outputs["dso_prior_year"] == pytest.approx(46.0)
    assert r.outputs["delta_dso_days"] == pytest.approx(18.4)
    assert r.outputs["dso_rel_change"] == pytest.approx(0.4)
    assert r.scored is True
    assert all(c.section == "XBRL" and c.xbrl_tag for c in r.citations)
    assert any(c.xbrl_tag == AR for c in r.citations)
    c0 = r.citations[0]
    assert (
        c0.url == f"https://www.sec.gov/Archives/edgar/data/{CIK}/{c0.accession.replace('-', '')}/"
    )


def test_a1_critical_thresholds_without_persistence_is_warning() -> None:
    nf = company(instants={AR: series(500.0, i11=700.0)})
    r = ev("A1_DSO", nf)
    assert r.severity == "WARNING"
    assert r.reason is not None and "previous quarter" in r.reason


def test_a1_warning() -> None:
    """AR 560 → DSO 51.52 vs 46.0: Δ 5.52 d, rel 12% → WARNING only."""
    r = ev("A1_DSO", company(instants={AR: series(500.0, i11=560.0)}))
    assert r.severity == "WARNING"


def test_a1_no_flag() -> None:
    r = ev("A1_DSO", company())
    assert r.severity == "NO_FLAG"
    assert r.outputs["delta_dso_days"] == pytest.approx(0.0)


def test_a1_ar_below_materiality_is_not_applicable() -> None:
    """AR 50 < 2% × TTM revenue 4000 = 80."""
    r = ev("A1_DSO", company(instants={AR: const(50.0, N)}))
    assert r.state == "NOT_APPLICABLE" and r.severity is None
    assert r.reason is not None and "receivables immaterial" in r.reason


def test_a1_missing_ar() -> None:
    r = ev("A1_DSO", company(drop=(AR,)))
    assert r.state == "NOT_COMPUTABLE" and r.reason is not None and "AR not tagged" in r.reason


def test_a1_prior_year_missing() -> None:
    nf = company(flows={REV: by_year(None, None, 1000.0)})
    r = ev("A1_DSO", nf)
    assert r.state == "NOT_COMPUTABLE"
    assert r.reason is not None and "prior-year quarter missing" in r.reason


def test_a1_zero_revenue_quarter() -> None:
    nf = company(flows={REV: series(1000.0, i11=0.0)})
    r = ev("A1_DSO", nf)
    assert r.state == "NOT_COMPUTABLE" and r.reason is not None and "≤ 0" in r.reason


def test_a1_zero_prior_year_revenue_base() -> None:
    nf = company(flows={REV: series(1000.0, i7=0.0)})
    r = ev("A1_DSO", nf)
    assert r.state == "NOT_COMPUTABLE" and r.reason is not None and "prior-year" in r.reason


def test_a1_tag_changed_annotation() -> None:
    nf = company(
        flows={REV: by_year(None, None, 1000.0), "SalesRevenueNet": by_year(1000.0, 1000.0, None)}
    )
    r = ev("A1_DSO", nf)
    assert r.state == "COMPUTED"
    assert "tag changed" in r.annotations


# ====================================================================== guard
def test_acquisition_guard_caps_critical_at_warning() -> None:
    """Goodwill 1000 → 1200 (+20% ≥ 10%) caps the CRITICAL A1 case at WARNING."""
    nf = company(
        instants={AR: series(500.0, i10=600.0, i11=700.0), GW: by_year(1000.0, 1000.0, 1200.0)}
    )
    r = ev("A1_DSO", nf)
    assert r.severity == "WARNING"
    assert "acquisition-distorted" in r.annotations
    assert run_accounting(nf, ORD, CIK, "SYN").acquisition_guard is True


def test_acquisition_guard_payments_leg() -> None:
    """TTM acquisitions 600 ≥ 5% of prior-year assets 10000."""
    nf = company(ytd={ACQ: by_year(0.0, 0.0, 150.0)})
    active, details = acquisition_guard(nf, P)
    assert active is True
    assert details["acquisitions_pct_prior_assets"] == pytest.approx(0.06)
    nf2 = company(ytd={ACQ: by_year(0.0, 0.0, 100.0)})  # 400 / 10000 = 4%
    assert acquisition_guard(nf2, P)[0] is False


def test_acquisition_guard_goodwill_zero_base_not_triggered() -> None:
    nf = company(instants={GW: by_year(0.0, 0.0, 1000.0)})
    active, details = acquisition_guard(nf, P)
    assert details["goodwill_growth"] is None
    assert active is False


def test_acquisition_guard_missing_data_not_triggered() -> None:
    assert acquisition_guard(company(), P)[0] is False


# ====================================================================== A2
def test_a2_critical() -> None:
    """DIO = 420/600×92 = 64.4 vs 46.0; previous quarter 360 → 55.2 vs 46.0."""
    nf = company(instants={INV: series(300.0, i10=360.0, i11=420.0)})
    r = ev("A2_DIO", nf)
    assert r.severity == "CRITICAL"
    assert r.outputs["dio"] == pytest.approx(64.4)


def test_a2_no_flag() -> None:
    assert ev("A2_DIO", company()).severity == "NO_FLAG"


def test_a2_no_inventory_tag_is_not_applicable() -> None:
    r = ev("A2_DIO", company(drop=(INV,)))
    assert r.state == "NOT_APPLICABLE" and r.reason == "no inventory tag reported"


def test_a2_inventory_immaterial() -> None:
    """Inventory 40 < 2% × TTM COGS 2400 = 48."""
    r = ev("A2_DIO", company(instants={INV: const(40.0, N)}))
    assert r.state == "NOT_APPLICABLE" and r.reason is not None and "immaterial" in r.reason


def test_a2_inventory_missing_at_period() -> None:
    r = ev("A2_DIO", company(instants={INV: series(300.0, i11=None)}))
    assert r.state == "NOT_COMPUTABLE"


def test_a2_missing_cogs() -> None:
    r = ev("A2_DIO", company(drop=(COGS,)))
    assert r.state == "NOT_COMPUTABLE" and r.reason is not None and "COGS" in r.reason


# ====================================================================== A3
def test_a3_critical_accruals() -> None:
    """(1200 − 100) / 10000 = 0.11 ≥ 10%."""
    r = ev("A3_ACCRUALS", company(ytd={OCF: const(25.0, N)}))
    assert r.severity == "CRITICAL"
    assert r.outputs["accrual_ratio"] == pytest.approx(0.11)


def test_a3_warning() -> None:
    """(1200 − 600) / 10000 = 0.06 → WARNING; CCR 0.5 (not < 0.5) → WARNING."""
    r = ev("A3_ACCRUALS", company(ytd={OCF: const(150.0, N)}))
    assert r.severity == "WARNING"
    assert r.outputs["ccr"] == pytest.approx(0.5)


def test_a3_ccr_only_warning() -> None:
    """OCF 840: ratio 0.036 (no flag), CCR 0.7 < 0.75 → WARNING."""
    r = ev("A3_ACCRUALS", company(ytd={OCF: const(210.0, N)}))
    assert r.severity == "WARNING"
    assert r.outputs["accrual_ratio"] == pytest.approx(0.036)


def test_a3_no_flag() -> None:
    r = ev("A3_ACCRUALS", company(ytd={OCF: const(330.0, N)}))
    assert r.severity == "NO_FLAG"
    assert r.outputs["ccr"] == pytest.approx(1.1)


def test_a3_negative_ni() -> None:
    """NI −400, OCF 200: ratio −0.06; CCR n/m with loss-making note."""
    r = ev("A3_ACCRUALS", company(flows={NI: const(-100.0, N)}, ytd={OCF: const(50.0, N)}))
    assert r.severity == "NO_FLAG"
    assert r.outputs["ccr"] is None
    assert "loss-making: cash conversion n/m" in r.annotations


def test_a3_negative_ni_large_accruals_still_flags() -> None:
    """NI −400, OCF −2800 → (−400 + 2800) / 10000 = 0.24 → CRITICAL via accrual ratio."""
    r = ev("A3_ACCRUALS", company(flows={NI: const(-100.0, N)}, ytd={OCF: const(-700.0, N)}))
    assert r.severity == "CRITICAL"
    assert r.outputs["ccr"] is None


def test_a3_near_zero_ni() -> None:
    """NI 40 < 2% × 4000 = 80 → CCR n/m (would be 1.2)."""
    r = ev("A3_ACCRUALS", company(flows={NI: const(10.0, N)}, ytd={OCF: const(12.0, N)}))
    assert r.severity == "NO_FLAG"
    assert r.outputs["ccr"] is None
    assert any("below 2% of revenue" in a for a in r.annotations)


def test_a3_ni_up_ocf_down_is_critical() -> None:
    """NI 1000 → 1200 while OCF 1500 → 1300 (−13.3%) → CRITICAL; CCR 1.083."""
    nf = company(flows={NI: by_year(250.0, 250.0, 300.0)}, ytd={OCF: by_year(375.0, 375.0, 325.0)})
    r = ev("A3_ACCRUALS", nf)
    assert r.severity == "CRITICAL"
    assert r.outputs["OCF_ttm_growth"] == pytest.approx(-200 / 1500)


def test_a3_missing_ocf() -> None:
    r = ev("A3_ACCRUALS", company(drop=(OCF,)))
    assert (
        r.state == "NOT_COMPUTABLE" and r.reason is not None and "operating cash flow" in r.reason
    )


def test_a3_financial_is_unscored() -> None:
    r = ev("A3_ACCRUALS", company(ytd={OCF: const(25.0, N)}), FIN)
    assert r.state == "COMPUTED" and r.scored is False
    assert "low reliability for financials" in r.annotations


# ====================================================================== A4
def test_a4_critical() -> None:
    """CapRate 160/(160+240) = 0.40 vs 80/(80+320) = 0.20 → +20 pp."""
    nf = company(ytd={CAPSW: by_year(20.0, 20.0, 40.0)}, flows={RND: by_year(80.0, 80.0, 60.0)})
    r = ev("A4_CAPRATE", nf)
    assert r.severity == "CRITICAL"
    assert r.outputs["cap_rate_change_pp"] == pytest.approx(0.20)


def test_a4_warning() -> None:
    """112/(112+288) = 0.28 vs 0.20 → +8 pp."""
    nf = company(ytd={CAPSW: by_year(20.0, 20.0, 28.0)}, flows={RND: by_year(80.0, 80.0, 72.0)})
    assert ev("A4_CAPRATE", nf).severity == "WARNING"


def test_a4_no_flag() -> None:
    nf = company(ytd={CAPSW: const(20.0, N)}, flows={RND: const(80.0, N)})
    assert ev("A4_CAPRATE", nf).severity == "NO_FLAG"


def test_a4_immaterial() -> None:
    """CapSW TTM 20 < 1% × 4000 = 40."""
    nf = company(ytd={CAPSW: const(5.0, N)}, flows={RND: const(80.0, N)})
    assert ev("A4_CAPRATE", nf).state == "NOT_APPLICABLE"


def test_a4_tags_absent() -> None:
    r = ev("A4_CAPRATE", company(flows={RND: const(80.0, N)}))
    assert r.state == "NOT_COMPUTABLE"


def test_a4_prior_year_missing() -> None:
    nf = company(ytd={CAPSW: by_year(None, None, 40.0)}, flows={RND: const(80.0, N)})
    r = ev("A4_CAPRATE", nf)
    assert r.state == "NOT_COMPUTABLE" and r.reason is not None and "prior-year" in r.reason


# ====================================================================== A5
def test_a5_warning_three_of_four() -> None:
    nf = company(flows={RESTR: [0.0] * 9 + [10.0, 10.0, 10.0]})
    r = ev("A5_RESTRUCT", nf)
    assert r.severity == "WARNING"
    assert r.outputs["material_quarters_last4"] == 3.0


def test_a5_critical_six_of_eight() -> None:
    vals = [0.0] * 4 + [10.0, 10.0, 0.0, 10.0, 10.0, 10.0, 10.0, 0.0]
    r = ev("A5_RESTRUCT", company(flows={RESTR: vals}))
    assert r.severity == "CRITICAL"
    assert r.outputs["material_quarters_last8"] == 6.0


def test_a5_critical_each_of_last_three_fiscal_years() -> None:
    """A 30 charge every Q4: FY 30 / 4000 = 0.75% ≥ 0.5% in 2022, 2023 and 2024."""
    vals: Vals = [0.0, 0.0, 0.0, 30.0] * 3
    r = ev("A5_RESTRUCT", company(flows={RESTR: vals}))
    assert r.severity == "CRITICAL"
    assert r.outputs["material_quarters_last4"] == 1.0
    assert r.outputs["material_fiscal_years_last3"] == 3.0


def test_a5_no_flag_single_charge() -> None:
    r = ev("A5_RESTRUCT", company(flows={RESTR: [0.0] * 11 + [10.0]}))
    assert r.severity == "NO_FLAG"


def test_a5_below_materiality_does_not_count() -> None:
    """4 < 0.5% × 1000 = 5 in every quarter."""
    r = ev("A5_RESTRUCT", company(flows={RESTR: const(4.0, N)}))
    assert r.severity == "NO_FLAG" and r.outputs["material_quarters_last8"] == 0.0


def test_a5_no_tags() -> None:
    r = ev("A5_RESTRUCT", company())
    assert r.state == "COMPUTED" and r.severity == "NO_FLAG"
    assert r.reason == "no restructuring tags reported"


def test_a5_impairments_never_trigger() -> None:
    r = ev(
        "A5_RESTRUCT",
        company(flows={RESTR: const(0.0, N), "GoodwillImpairmentLoss": const(500.0, N)}),
    )
    assert r.severity == "NO_FLAG"
    assert r.inputs["GoodwillImpairment_q (display only)"] == 500.0


def test_a5_missing_revenue() -> None:
    nf = company(flows={RESTR: const(10.0, N)}, drop=(REV,))
    r = ev("A5_RESTRUCT", nf)
    assert r.state == "NOT_COMPUTABLE"


# ====================================================================== gating
@pytest.mark.parametrize("check_id", ["A1_DSO", "A2_DIO", "A4_CAPRATE"])
def test_financial_issuer_not_applicable(check_id: str) -> None:
    r = ev(check_id, company(), FIN)
    assert r.state == "NOT_APPLICABLE" and r.reason == "financial issuer"


def test_financial_a5_runs_normally() -> None:
    r = ev("A5_RESTRUCT", company(flows={RESTR: [0.0] * 9 + [10.0] * 3}), FIN)
    assert r.severity == "WARNING" and r.scored is True


def test_shell_everything_not_applicable() -> None:
    out = run_accounting(company(), SHELL, CIK, "SYN")
    assert out.checks and all(c.state == "NOT_APPLICABLE" for c in out.checks)
    assert out.acquisition_guard is False


# ====================================================================== display
def test_display_metrics() -> None:
    """OCF 1200, CapEx 400 → FCF 800; SBC 200 → 600. CapEx/D&A = 400/160 = 2.5 vs
    240/160 = 1.5 (+66.7%) → note. ROIC: OI 1600, t = 200/1600 = 0.125 → NOPAT 1400;
    IC = 2000 + 5000 − 1000 = 6000 → 0.2333."""
    nf = company(
        ytd={CAPEX: by_year(60.0, 60.0, 100.0), DA: const(40.0, N), SBC: const(50.0, N)},
        flows={
            "OperatingIncomeLoss": const(400.0, N),
            "IncomeTaxExpenseBenefit": const(50.0, N),
            PRETAX: const(400.0, N),
        },
        instants={
            "LongTermDebt": const(2000.0, N),
            "StockholdersEquity": const(5000.0, N),
            "CashAndCashEquivalentsAtCarryingValue": const(1000.0, N),
            AP: series(300.0, i11=420.0),
        },
    )
    fcf = ev("X_FCF", nf)
    assert fcf.outputs["fcf"] == 800.0 and fcf.scored is False and fcf.severity == "NO_FLAG"
    assert ev("X_FCF_SBC", nf).outputs["fcf_sbc"] == 600.0
    cd = ev("X_CAPEX_DA", nf)
    assert cd.outputs["capex_da"] == pytest.approx(2.5)
    assert "CapEx at least 2x D&A and rising" in cd.annotations
    roic = ev("X_ROIC", nf)
    assert roic.outputs["tax_rate"] == pytest.approx(0.125)
    assert roic.outputs["roic"] == pytest.approx(1400 / 6000)
    dpo = ev("X_DPO", nf)  # 420/600×92 = 64.4 vs 46.0 → +18.4 d, +40%
    assert dpo.outputs["dpo"] == pytest.approx(64.4)
    assert "payables stretch supporting OCF" in dpo.annotations


def test_capex_da_nm_when_da_not_positive() -> None:
    nf = company(ytd={CAPEX: const(100.0, N), DA: const(0.0, N)})
    r = ev("X_CAPEX_DA", nf)
    assert r.state == "NOT_COMPUTABLE" and r.reason is not None and "n/m" in r.reason


def test_roic_negative_invested_capital_nm_and_default_tax() -> None:
    nf = company(
        flows={
            "OperatingIncomeLoss": const(-100.0, N),
            PRETAX: const(-100.0, N),
        },
        instants={
            "LongTermDebt": const(2000.0, N),
            "StockholdersEquity": const(-5000.0, N),
            "CashAndCashEquivalentsAtCarryingValue": const(1000.0, N),
        },
    )
    r = ev("X_ROIC", nf)
    assert r.outputs["tax_rate"] == pytest.approx(0.21)
    assert r.state == "NOT_COMPUTABLE" and r.reason == "n/m: invested capital ≤ 0"


def test_total_debt_components() -> None:
    nf = company(
        instants={
            "LongTermDebtNoncurrent": const(900.0, N),
            "LongTermDebtCurrent": const(100.0, N),
            "CommercialPaper": const(50.0, N),
        }
    )
    assert total_debt(nf, P)[0] == 1050.0
    assert total_debt(company(), P)[0] is None


def test_restated_display() -> None:
    nf = company(extra={AR: [fact(None, date(2024, 9, 30), 550.0, "10-K", filed=date(2025, 2, 1))]})
    r = ev("X_RESTATED", nf)
    assert r.outputs["AR_restated_points"] == 1.0
    assert "restated: AR" in r.annotations


# ====================================================================== run
def test_run_accounting_history_and_order() -> None:
    nf = company(instants={AR: series(500.0, i10=600.0, i11=700.0)})
    out = run_accounting(nf, ORD, CIK, "SYN")
    assert out.latest_period == P
    ids = [c.check_id for c in out.checks]
    assert ids[:5] == ["A1_DSO", "A2_DIO", "A3_ACCRUALS", "A4_CAPRATE", "A5_RESTRUCT"]
    assert all(c.startswith("X_") for c in ids[5:])
    a1 = out.checks[0]
    assert a1.severity == "CRITICAL"
    assert len(a1.history) == 12
    assert a1.history[-1] == {"period_end": "2024-12-31", "value": pytest.approx(64.4)}
    assert a1.history[0]["period_end"] == "2022-03-31"
    # 2022-03-31 has no TTM revenue / prior year → not computable → None, never 0.
    assert a1.history[0]["value"] is None
    assert a1.history[4]["value"] == pytest.approx(500 / 1000 * 90)  # 2023-Q1: 90 days
    assert run_accounting(nf, ORD, CIK, "SYN").checks == out.checks  # deterministic


def test_run_accounting_no_data() -> None:
    out = run_accounting(normalize({}), ORD, CIK, "SYN")
    assert out.latest_period is None
    assert all(c.state == "NOT_COMPUTABLE" and c.severity is None for c in out.checks)


def test_unknown_check() -> None:
    with pytest.raises(KeyError):
        ev("A9_NOPE", company())


# ====================================================================== audit fixes
def test_a5_ltm_columns_do_not_create_fiscal_years() -> None:
    """One 30 charge in 2024-Q4 plus two 10-Q LTM columns must not satisfy the
    'material charge in each of the last 3 fiscal years' leg (MAJOR-1, D-214)."""
    ltm = {
        REV: [
            fact(date(2023, 10, 1), date(2024, 9, 30), 4000.0, "10-Q"),
            fact(date(2023, 7, 1), date(2024, 6, 30), 4000.0, "10-Q"),
        ],
        RESTR: [
            fact(date(2023, 10, 1), date(2024, 9, 30), 30.0, "10-Q"),
            fact(date(2023, 7, 1), date(2024, 6, 30), 30.0, "10-Q"),
        ],
    }
    nf = company(flows={RESTR: [0.0] * 11 + [30.0]}, extra=ltm)
    r = ev("A5_RESTRUCT", nf)
    assert r.severity == "NO_FLAG"
    assert r.outputs["material_fiscal_years_last3"] == 1.0


def test_a1_persistence_only_at_previous_quarter() -> None:
    """AR missing at 2024-09-30: WARNING held at 2024-06-30 does not count (MAJOR-2)."""
    nf = company(instants={AR: series(500.0, i9=600.0, i10=None, i11=700.0)})
    r = ev("A1_DSO", nf)
    assert r.severity == "WARNING"
    assert r.reason is not None and "persistence not established" in r.reason
    assert "2024-09-30" in r.reason


def test_a1_current_balance_exact_match_only() -> None:
    """AR tagged only 9 days before P → 'AR not tagged' (MINOR-3)."""
    nf = company(
        instants={AR: series(500.0, i11=None)},
        extra={AR: [fact(None, date(2024, 12, 22), 500.0, "10-K")]},
    )
    r = ev("A1_DSO", nf)
    assert r.state == "NOT_COMPUTABLE" and r.reason is not None and "AR not tagged" in r.reason


def test_a1_tag_changed_when_ttm_mixes_tags() -> None:
    """Quarter and prior-year quarter both use REV; TTM uses SalesRevenueNet for Q1–Q3."""
    nf = company(
        flows={
            REV: [*const(1000.0, 8), None, None, None, None],
            "SalesRevenueNet": [*const(None, 8), 1000.0, 1000.0, 1000.0, None],
        },
        extra={REV: [fact(date(2024, 10, 1), P, 1000.0, "10-K")]},
    )
    r = ev("A1_DSO", nf)
    assert r.state == "COMPUTED"
    assert "tag changed" in r.annotations


def test_a5_non_derivable_quarters_are_unknown() -> None:
    """Q3 and Q4 2024 only inside the FY total (no 3M, no 9M): unknown, not 'not material'.
    Known: Q1, Q2 = 10 (2 of 4) → NO_FLAG, but 2 + 2 unknown ≥ 3 → reason says so."""
    nf = company(
        flows={RESTR: [*const(0.0, 8), 10.0, 10.0, None, None]},
        extra={RESTR: [fact(date(2024, 1, 1), P, 40.0, "10-K")]},
    )
    r = ev("A5_RESTRUCT", nf)
    assert r.severity == "NO_FLAG"
    assert r.outputs["quarters_unknown_last4"] == 2.0
    assert "restructuring quarter(s) not derivable: 2024-09-30, 2024-12-31" in r.annotations
    assert r.reason is not None and "could raise it to WARNING" in r.reason
