"""Tests for engine/sections.py (PRD §7.2.2)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from veritas.engine.html_text import html_to_text
from veritas.engine.sections import (
    LABEL_8K,
    LABEL_10K,
    LABEL_10Q,
    LABEL_EX13,
    find_longest_span,
    locate_earnings_release,
    locate_mda,
    needs_ex13,
)

HTML_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "html"


def _text(name: str) -> str:
    return html_to_text((HTML_DIR / name).read_bytes())


def test_find_longest_span_prefers_longest() -> None:
    text = "S\nx\nE\nS\nlong body line\nmore\nE\nS\nE"
    start = re.compile(r"^S$", re.M)
    end = re.compile(r"^E$", re.M)
    span = find_longest_span(text, start, end)
    assert span is not None
    assert text[span[0] : span[1]] == "S\nlong body line\nmore\n"


def test_find_longest_span_none_without_end() -> None:
    assert find_longest_span("S\nbody", re.compile("^S", re.M), re.compile("^E", re.M)) is None


def test_10k_toc_plus_real_item7_longest_span_wins() -> None:
    text = _text("tenk_toc_item7.htm")
    sec = locate_mda(text, "10-K")
    assert sec.status == "OK"
    assert sec.label == LABEL_10K
    assert sec.word_count >= 1500
    assert sec.start is not None and sec.end is not None
    assert text[sec.start : sec.end] == sec.text
    assert sec.text.startswith(
        "Item 7. Management's Discussion and Analysis of Financial Condition"
    )
    assert "Revenue increased 18% in fiscal 2025" in sec.text
    # Ends before Item 7A; Item 1A risk factors are excluded.
    assert "Item 7A" not in sec.text
    assert "interest rate risk" not in sec.text
    assert "Historically strong demand" not in sec.text
    # Repeated page header / page numbers were dropped before location.
    assert "ACME DEVICES CORP" not in sec.text


def test_10q_part1_item2_only_title_required() -> None:
    text = _text("tenq_part1_vs_part2.htm")
    sec = locate_mda(text, "10-Q")
    assert sec.status == "OK"
    assert sec.label == LABEL_10Q
    assert sec.word_count >= 800
    assert sec.text.startswith("Item 2. | Management's Discussion and Analysis")
    assert "robust bookings growth" in sec.text
    assert "Unregistered Sales" not in sec.text
    assert "Share repurchases" not in sec.text
    assert "Item 3" not in sec.text


def test_exhibit13_reference_detected_and_followed() -> None:
    text = _text("tenk_ex13_reference.htm")
    assert needs_ex13(text)
    missing = locate_mda(text, "10-K")
    assert missing.status == "NOT_FOUND"
    assert missing.text == ""
    assert missing.reason is not None and "Exhibit 13" in missing.reason

    ex13 = _text("ex13_annual_report.htm")
    sec = locate_mda(text, "10-K", ex13_text=ex13)
    assert sec.status == "OK"
    assert sec.label == LABEL_EX13
    assert sec.text.startswith("MANAGEMENT'S DISCUSSION AND ANALYSIS")
    assert "Report of Independent" not in sec.text
    assert ex13[sec.start : sec.end] == sec.text


def test_full_10k_does_not_need_ex13() -> None:
    assert not needs_ex13(_text("tenk_toc_item7.htm"))


def test_short_mda_not_found_never_whole_document() -> None:
    text = (
        "Item 7. Management's Discussion and Analysis\nShort body with strong demand.\n"
        "Item 8. Financial Statements\n" + "filler words here " * 2000
    )
    sec = locate_mda(text, "10-K")
    assert sec.status == "NOT_FOUND"
    assert sec.text == ""
    assert sec.word_count == 0
    assert sec.reason is not None and "too short" in sec.reason


def test_missing_heading_not_found() -> None:
    sec = locate_mda("no headings at all\n" * 3000, "10-Q")
    assert sec.status == "NOT_FOUND"
    assert sec.text == ""


@pytest.mark.parametrize("form", ["8-K", "DEF 14A"])
def test_unsupported_form(form: str) -> None:
    assert locate_mda("Item 7. Management's Discussion\n", form).status == "NOT_FOUND"


def test_earnings_release_stops_at_first_data_table() -> None:
    sec = locate_earnings_release(_text("earnings_release.htm"))
    assert sec.status == "OK"
    assert sec.label == LABEL_8K
    assert sec.text.endswith("lowered unit costs during the fourth quarter.")
    assert "(in millions)" not in sec.text  # table header row excluded too
    assert "strong orders in the industrial segment" not in sec.text


def test_earnings_release_stops_at_forward_looking_paragraph() -> None:
    sec = locate_earnings_release(_text("earnings_release_fls_first.htm"))
    assert sec.status == "OK"
    assert sec.text.endswith("from our largest data center customers.")
    assert "forward-looking" not in sec.text
    assert "Strong orders continued" not in sec.text


def test_earnings_release_empty() -> None:
    sec = locate_earnings_release("Revenue | $1,100 | $990\nmore")
    assert sec.status == "NOT_FOUND"
    assert sec.text == ""


def test_earnings_deck_stops_at_spaced_financial_summary_heading() -> None:
    sec = locate_earnings_release(_text("earnings_deck.htm"))
    assert sec.status == "OK"
    assert sec.text.endswith("on strong demand in Europe and Asia.")
    assert "energy storage" not in sec.text
    # The contents line mentions "Financial Summary" mid-line and does not cut.
    assert "Highlights 03 Financial Summary" in sec.text


def test_earnings_release_stops_at_run_of_numeric_lines() -> None:
    sec = locate_earnings_release(_text("earnings_numeric_run.htm"))
    assert sec.status == "OK"
    assert sec.text.endswith("from automotive customers worldwide.")
    assert "$1,100" not in sec.text
    assert "Segment results" not in sec.text
