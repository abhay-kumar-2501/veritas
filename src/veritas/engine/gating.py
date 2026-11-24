"""Sector & filer-type gating (PRD §7.6). Runs first. Pure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from veritas.config import FINANCIAL_SCOPE_NOTE
from veritas.models import _Serializable

GateKind = Literal["unsupported", "shell", "financial", "ordinary"]

SHELL_SIC = 6770
FINANCIAL_SIC_RANGE = (6000, 6799)  # inclusive

SHELL_SCOPE_NOTE = "Shell or pre-revenue issuer: accounting checks not applicable; no score."
UNSUPPORTED_SCOPE_NOTE = "Unsupported filer: no 10-K in the last 3 years."


@dataclass(frozen=True)
class Gating(_Serializable):
    kind: GateKind
    scope_note: str | None
    reason: str


def _sic_int(sic: str | None) -> int | None:
    if sic is None:
        return None
    s = sic.strip()
    return int(s) if s.isdigit() else None


def gate(sic: str | None, has_recent_10k: bool, ttm_revenue: float | None) -> Gating:
    """Classify the filer (§7.6). See docs/decisions/accounting.md D-203 for ordering."""
    if not has_recent_10k:
        return Gating("unsupported", UNSUPPORTED_SCOPE_NOTE, "no 10-K filed in the last 3 years")
    code = _sic_int(sic)
    if code == SHELL_SIC:
        return Gating("shell", SHELL_SCOPE_NOTE, "SIC 6770 (blank check / shell company)")
    if code is not None and FINANCIAL_SIC_RANGE[0] <= code <= FINANCIAL_SIC_RANGE[1]:
        return Gating("financial", FINANCIAL_SCOPE_NOTE, f"SIC {code}: financial issuer")
    if ttm_revenue is None:
        return Gating("shell", SHELL_SCOPE_NOTE, "TTM revenue missing (pre-revenue or untagged)")
    if ttm_revenue <= 0:
        return Gating("shell", SHELL_SCOPE_NOTE, "TTM revenue is zero or negative (pre-revenue)")
    return Gating("ordinary", None, "ordinary filer" + (f" (SIC {code})" if code else ""))
