"""Module D — Consistency Score (PRD §7.5). The only place score points are assigned."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from veritas.config import t, ti
from veritas.engine.gating import Gating
from veritas.models import SEVERITY_RANK, Finding, ModuleResult, ScoreBreakdown

ASSESSED_STATUSES = frozenset({"ok", "partial"})
FIRING = frozenset({"WARNING", "CRITICAL"})

MODULE_NAMES: dict[str, str] = {
    "A": "Module A: accounting",
    "B": "Module B: footnotes & hedging",
    "C": "Module C: proxy incentives",
}

INSUFFICIENT = "INSUFFICIENT DATA"
NOT_SCORED = "NOT SCORED"


def _assessed(module: ModuleResult | None) -> bool:
    return module is not None and module.status in ASSESSED_STATUSES


def _coverage(module_a: ModuleResult | None) -> tuple[int, int]:
    if module_a is None:
        return 0, 0
    scored = [c for c in module_a.checks if c.scored and c.check_id.startswith("A")]
    den = [c for c in scored if c.state != "NOT_APPLICABLE"]
    num = [c for c in den if c.state == "COMPUTED"]
    return len(num), len(den)


def _accounting_points(findings: Iterable[Finding]) -> int:
    """Points for A1–A5 findings. At most one finding per check (highest severity,
    then latest period). A3 is halved (round down) when A1 or A2 fires at the same P."""
    best: dict[str, Finding] = {}
    for f in findings:
        if f.module != "A" or f.category != "accounting" or f.severity not in FIRING:
            continue
        if f.check is not None and not f.check.scored:
            continue
        key = f.check.check_id if f.check is not None else f.id
        cur = best.get(key)
        if cur is None or _rank(f) > _rank(cur):
            best[key] = f
    crit, warn = ti("PTS_ACCOUNTING_CRIT"), ti("PTS_ACCOUNTING_WARN")
    total = 0
    a12_periods = {
        f.check.period_end
        for k, f in best.items()
        if k in ("A1_DSO", "A2_DIO") and f.check is not None and f.check.period_end is not None
    }
    for key, f in best.items():
        pts = crit if f.severity == "CRITICAL" else warn
        if (
            key == "A3_ACCRUALS"
            and f.check is not None
            and f.check.period_end is not None
            and f.check.period_end in a12_periods
        ):
            pts //= 2
        total += pts
    return total


def _rank(f: Finding) -> tuple[int, date]:
    end = f.check.period_end if f.check is not None and f.check.period_end else date.min
    return SEVERITY_RANK[f.severity], end


def _count(module: ModuleResult, category: str) -> int:
    return sum(1 for f in module.findings if f.category == category and f.severity in FIRING)


def score(
    module_a: ModuleResult | None,
    module_b: ModuleResult | None,
    module_c: ModuleResult | None,
    gating: Gating,
) -> ScoreBreakdown:
    """Compute the consistency score (§7.5)."""
    modules = {"A": module_a, "B": module_b, "C": module_c}
    assessed = [k for k, m in modules.items() if _assessed(m)]
    unassessed = tuple(MODULE_NAMES[k] for k in modules if k not in assessed)
    num, den = _coverage(module_a)

    if gating.kind in ("shell", "unsupported"):
        return ScoreBreakdown(
            score=None,
            coverage_num=num,
            coverage_den=den,
            deductions=(),
            label=NOT_SCORED,
            scope_note=gating.scope_note,
            modules_assessed=len(assessed),
            unassessed=unassessed,
        )

    deductions: list[tuple[str, int]] = []
    if module_a is not None:
        raw = _accounting_points(module_a.findings)
        deductions.append(("accounting", min(raw, ti("CAP_ACCOUNTING"))))
    if module_b is not None and _assessed(module_b):
        fn = _count(module_b, "footnotes") * ti("PTS_FOOTNOTE")
        deductions.append(("footnotes", min(fn, ti("CAP_FOOTNOTES"))))
        hd = _count(module_b, "hedging") * ti("PTS_HEDGING")
        deductions.append(("hedging", min(hd, ti("CAP_HEDGING"))))
    if module_c is not None and _assessed(module_c):
        inc = _count(module_c, "incentives") * ti("PTS_INCENTIVE")
        deductions.append(("incentives", min(inc, ti("CAP_INCENTIVES"))))

    if den == 0 or num / den < t("COVERAGE_MIN"):
        value: int | None = None
        label = INSUFFICIENT
    else:
        value = max(0, 100 - sum(p for _c, p in deductions))
        label = f"Score {value} · coverage {num}/{den}"
    return ScoreBreakdown(
        score=value,
        coverage_num=num,
        coverage_den=den,
        deductions=tuple(deductions),
        label=label,
        scope_note=gating.scope_note,
        modules_assessed=len(assessed),
        unassessed=unassessed,
    )
