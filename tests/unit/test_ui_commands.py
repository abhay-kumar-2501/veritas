"""PRD §8.3 command grammar: the pure parser."""

from __future__ import annotations

import pytest

from veritas.ui.commands import (
    VIEW_LABELS,
    VIEW_OVERVIEW,
    VIEW_PROXY,
    VIEW_RATIOS,
    VIEW_REDLINE,
    parse_command,
)


@pytest.mark.parametrize(
    ("raw", "ticker"),
    [
        ("NVDA", "NVDA"),
        ("nvda", "NVDA"),
        ("  wmt  ", "WMT"),
        ("ANALYZE TSLA", "TSLA"),
        ("analyze   ba", "BA"),
        ("BRK.B", "BRK.B"),
        ("BF-B", "BF-B"),
        ("A", "A"),
        ("ABCDEFGHIJ", "ABCDEFGHIJ"),
    ],
)
def test_ticker_forms(raw: str, ticker: str) -> None:
    cmd = parse_command(raw)
    assert cmd.kind == "analyze"
    assert cmd.arg == ticker
    assert cmd.runs_analysis


@pytest.mark.parametrize(
    ("raw", "digits"),
    [("CIK 320193", "320193"), ("cik 0000320193", "320193"), ("CIK320193", "320193")],
)
def test_cik(raw: str, digits: str) -> None:
    cmd = parse_command(raw)
    assert cmd.kind == "cik"
    assert cmd.arg == digits


@pytest.mark.parametrize(
    ("raw", "view"),
    [
        ("DIFF", VIEW_REDLINE),
        ("diff", VIEW_REDLINE),
        ("RATIOS", VIEW_RATIOS),
        ("proxy", VIEW_PROXY),
        (" Overview ", VIEW_OVERVIEW),
    ],
)
def test_view_keywords_win_over_ticker_rule(raw: str, view: str) -> None:
    cmd = parse_command(raw)
    assert cmd.kind == "view"
    assert cmd.arg == view
    assert cmd.arg in VIEW_LABELS


def test_contra_and_export() -> None:
    contra = parse_command("contra")
    assert contra.kind == "contra"
    assert contra.arg == VIEW_OVERVIEW
    assert parse_command("EXPORT").kind == "export"


def test_empty_is_noop() -> None:
    assert parse_command("").kind == "empty"
    assert parse_command("   ").kind == "empty"


@pytest.mark.parametrize(
    "raw",
    [
        "hello world",
        "ANALYZE",
        "ANALYZE 123",
        "CIK",
        "CIK abc",
        "CIK 0",
        "1NVDA",
        "ABCDEFGHIJK",
        "<script>",
        "DIFF NOW",
    ],
)
def test_invalid(raw: str) -> None:
    cmd = parse_command(raw)
    assert cmd.kind == "invalid"
    assert cmd.error
    assert not cmd.runs_analysis
