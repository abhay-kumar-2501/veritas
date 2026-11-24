"""§7.6 sector & filer-type gating."""

from __future__ import annotations

from veritas.config import FINANCIAL_SCOPE_NOTE
from veritas.engine.gating import gate


def test_unsupported_without_recent_10k() -> None:
    g = gate("3674", False, 1e9)
    assert g.kind == "unsupported" and "10-K" in g.reason


def test_sic_6770_is_shell_even_with_revenue() -> None:
    g = gate("6770", True, 1e6)
    assert g.kind == "shell" and g.scope_note is not None


def test_sic_6022_is_financial() -> None:
    g = gate("6022", True, 5e9)
    assert g.kind == "financial"
    assert g.scope_note == FINANCIAL_SCOPE_NOTE


def test_financial_without_revenue_tag_stays_financial() -> None:
    """Banks often do not use the Appendix A revenue tags (D-203)."""
    assert gate("6022", True, None).kind == "financial"


def test_financial_range_bounds() -> None:
    assert gate("6000", True, 1.0).kind == "financial"
    assert gate("6799", True, 1.0).kind == "financial"
    assert gate("5999", True, 1.0).kind == "ordinary"
    assert gate("6800", True, 1.0).kind == "ordinary"


def test_missing_or_zero_revenue_is_shell() -> None:
    assert gate("3674", True, None).kind == "shell"
    assert gate("3674", True, 0.0).kind == "shell"
    assert gate("3674", True, -5.0).kind == "shell"


def test_ordinary() -> None:
    g = gate("3674", True, 1e9)
    assert g.kind == "ordinary" and g.scope_note is None


def test_unknown_sic_with_revenue_is_ordinary() -> None:
    assert gate(None, True, 1e9).kind == "ordinary"
    assert gate("", True, 1e9).kind == "ordinary"
