"""§7.5 scoring rules on synthetic ModuleResults."""

from __future__ import annotations

from datetime import date

import pytest

import veritas.config as cfg
from veritas.config import FINANCIAL_SCOPE_NOTE
from veritas.engine.gating import Gating
from veritas.engine.scorer import score
from veritas.models import CheckResult, CheckState, Finding, ModStatus, ModuleResult, Severity

P = date(2024, 12, 31)
ORD = Gating("ordinary", None, "ordinary filer")
IDS = ("A1_DSO", "A2_DIO", "A3_ACCRUALS", "A4_CAPRATE", "A5_RESTRUCT")


def chk(
    cid: str,
    state: CheckState = "COMPUTED",
    severity: Severity | None = "NO_FLAG",
    period: date = P,
    scored: bool = True,
) -> CheckResult:
    return CheckResult(
        check_id=cid,
        title=cid,
        state=state,
        severity=severity if state == "COMPUTED" else None,
        period_end=period,
        inputs={},
        outputs={},
        formula="",
        reason=None,
        annotations=(),
        citations=(),
        scored=scored,
    )


def finding(
    module: str, category: str, severity: Severity, check: CheckResult | None = None, fid: str = ""
) -> Finding:
    return Finding(
        id=fid or f"{module}-{category}-{severity}-{check.check_id if check else ''}",
        module=module,  # type: ignore[arg-type]
        category=category,
        severity=severity,
        title="t",
        explanation="e",
        check=check,
        claims=(),
        contradiction=False,
        citations=(),
    )


def mod(
    findings: tuple[Finding, ...] = (),
    checks: tuple[CheckResult, ...] = (),
    status: ModStatus = "ok",
) -> ModuleResult:
    return ModuleResult(status=status, findings=findings, checks=checks, reason=None, extra={})


def all_computed() -> tuple[CheckResult, ...]:
    return tuple(chk(c) for c in IDS)


def acc(*sev_by_id: tuple[str, Severity], period: date = P) -> ModuleResult:
    checks = list(all_computed())
    findings = []
    for cid, sev in sev_by_id:
        c = chk(cid, severity=sev, period=period)
        checks = [c if x.check_id == cid else x for x in checks]
        findings.append(finding("A", "accounting", sev, c))
    return mod(tuple(findings), tuple(checks))


def deduction(sb_deductions: tuple[tuple[str, int], ...], cat: str) -> int:
    return dict(sb_deductions)[cat]


def test_clean_score_and_label() -> None:
    sb = score(mod(checks=all_computed()), mod(), mod(), ORD)
    assert sb.score == 100
    assert sb.label == "Score 100 · coverage 5/5"
    assert sb.modules_assessed == 3 and sb.unassessed == ()


def test_points_per_severity() -> None:
    sb = score(acc(("A1_DSO", "CRITICAL"), ("A4_CAPRATE", "WARNING")), None, None, ORD)
    assert deduction(sb.deductions, "accounting") == 17
    assert sb.score == 83


def test_category_caps() -> None:
    a = acc(*((c, "CRITICAL") for c in IDS))  # 5 × 12 = 60 → cap 45
    fn = tuple(finding("B", "footnotes", "WARNING", fid=f"f{i}") for i in range(6))  # 30 → 20
    hd = tuple(finding("B", "hedging", "WARNING", fid=f"h{i}") for i in range(2))  # 10 → 5
    inc = tuple(finding("C", "incentives", "WARNING", fid=f"c{i}") for i in range(3))  # 15 → 10
    sb = score(a, mod(fn + hd), mod(inc), ORD)
    assert dict(sb.deductions) == {
        "accounting": 45,
        "footnotes": 20,
        "hedging": 5,
        "incentives": 10,
    }
    assert sb.score == 20


def test_score_clamps_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raise the accounting cap/points via config so the sum exceeds 100."""
    monkeypatch.setitem(cfg.THRESHOLDS, "CAP_ACCOUNTING", 200)
    monkeypatch.setitem(cfg.THRESHOLDS, "PTS_ACCOUNTING_CRIT", 30)
    a = acc(*((c, "CRITICAL") for c in IDS))  # 150
    sb = score(a, None, None, ORD)
    assert sb.score == 0
    assert sb.label == "Score 0 · coverage 5/5"


def test_a3_halving_with_a1_same_period() -> None:
    sb = score(acc(("A1_DSO", "WARNING"), ("A3_ACCRUALS", "WARNING")), None, None, ORD)
    assert deduction(sb.deductions, "accounting") == 5 + 2  # 5 // 2 = 2


def test_a3_halving_critical() -> None:
    sb = score(acc(("A2_DIO", "CRITICAL"), ("A3_ACCRUALS", "CRITICAL")), None, None, ORD)
    assert deduction(sb.deductions, "accounting") == 12 + 6


def test_a3_not_halved_at_different_period() -> None:
    a1 = chk("A1_DSO", severity="WARNING", period=date(2024, 9, 30))
    a3 = chk("A3_ACCRUALS", severity="WARNING")
    m = mod(
        (finding("A", "accounting", "WARNING", a1), finding("A", "accounting", "WARNING", a3)),
        all_computed(),
    )
    assert deduction(score(m, None, None, ORD).deductions, "accounting") == 10


def test_unscored_module_a_categories_deduct_nothing() -> None:
    old = chk("A1_DSO", severity="CRITICAL", period=date(2024, 9, 30))
    nf = chk("A5_RESTRUCT")
    m = mod(
        (
            finding("A", "accounting_prior_period", "CRITICAL", old),
            finding("A", "claims_consistent", "NO_FLAG", nf),
        ),
        all_computed(),
    )
    sb = score(m, None, None, ORD)
    assert deduction(sb.deductions, "accounting") == 0
    assert sb.score == 100


def test_footnotes_10q_unscored() -> None:
    b = mod((finding("B", "footnotes_10q", "WARNING", fid="q"),))
    sb = score(mod(checks=all_computed()), b, None, ORD)
    assert sb.score == 100


def test_unscored_check_finding_ignored() -> None:
    a3 = chk("A3_ACCRUALS", severity="CRITICAL", scored=False)
    m = mod((finding("A", "accounting", "CRITICAL", a3),), all_computed())
    assert score(m, None, None, ORD).score == 100


def test_coverage_below_60_is_insufficient() -> None:
    checks = (
        chk("A1_DSO"),
        chk("A2_DIO"),
        chk("A3_ACCRUALS", "NOT_COMPUTABLE"),
        chk("A4_CAPRATE", "NOT_COMPUTABLE"),
        chk("A5_RESTRUCT", "NOT_COMPUTABLE"),
    )
    sb = score(mod(checks=checks), None, None, ORD)
    assert sb.score is None and sb.label == "INSUFFICIENT DATA"
    assert (sb.coverage_num, sb.coverage_den) == (2, 5)


def test_coverage_excludes_not_applicable() -> None:
    checks = (
        chk("A1_DSO", "NOT_APPLICABLE"),
        chk("A2_DIO"),
        chk("A3_ACCRUALS"),
        chk("A4_CAPRATE", "NOT_COMPUTABLE"),
        chk("A5_RESTRUCT"),
    )
    sb = score(mod(checks=checks), None, None, ORD)
    assert (sb.coverage_num, sb.coverage_den) == (3, 4)
    assert sb.label == "Score 100 · coverage 3/4"


def test_coverage_zero_denominator() -> None:
    checks = tuple(chk(c, "NOT_APPLICABLE") for c in IDS)
    sb = score(mod(checks=checks), None, None, ORD)
    assert sb.score is None and sb.label == "INSUFFICIENT DATA"


def test_unassessed_modules_deduct_nothing_and_are_listed() -> None:
    b = mod((finding("B", "footnotes", "WARNING", fid="x"),), status="unavailable")
    c = mod((finding("C", "incentives", "WARNING", fid="y"),), status="error")
    sb = score(mod(checks=all_computed()), b, c, ORD)
    assert sb.score == 100
    assert sb.modules_assessed == 1
    assert sb.unassessed == ("Module B: footnotes & hedging", "Module C: proxy incentives")
    assert [k for k, _ in sb.deductions] == ["accounting"]


def test_partial_module_counts() -> None:
    b = mod((finding("B", "footnotes", "CRITICAL", fid="x"),), status="partial")
    sb = score(mod(checks=all_computed()), b, None, ORD)
    assert sb.score == 95


def test_shell_no_score() -> None:
    shell = Gating("shell", "Shell or pre-revenue issuer", "SIC 6770")
    sb = score(mod(checks=all_computed()), mod(), mod(), shell)
    assert sb.score is None and sb.label == "NOT SCORED"


def test_financial_scope_note() -> None:
    fin = Gating("financial", FINANCIAL_SCOPE_NOTE, "SIC 6022")
    checks = (
        chk("A1_DSO", "NOT_APPLICABLE"),
        chk("A2_DIO", "NOT_APPLICABLE"),
        chk("A3_ACCRUALS", scored=False),
        chk("A4_CAPRATE", "NOT_APPLICABLE"),
        chk("A5_RESTRUCT"),
    )
    sb = score(mod(checks=checks), None, None, fin)
    assert sb.scope_note == FINANCIAL_SCOPE_NOTE
    assert (sb.coverage_num, sb.coverage_den) == (1, 1)


def test_missing_module_a_is_insufficient() -> None:
    sb = score(None, mod(), mod(), ORD)
    assert sb.score is None and sb.label == "INSUFFICIENT DATA"
    assert "Module A: accounting" in sb.unassessed
