"""Tests for engine/html_text.py (PRD §7.2.1)."""

from __future__ import annotations

from pathlib import Path

from veritas.engine.html_text import (
    drop_data_rows,
    element_text,
    html_to_text,
    is_data_row,
    normalize_text,
    parse_html,
)

HTML_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "html"


def test_normalize_text_maps_characters() -> None:
    raw = "Café ‘a’ “b” 2024–2025 — x­yz​q\t\n  end"
    assert normalize_text(raw) == "Café 'a' \"b\" 2024-2025 - xyz q end"


def test_normalize_text_nfkc_and_idempotent() -> None:
    s = "ﬁnance  thin narrow ‑dash"
    once = normalize_text(s)
    assert once == "finance thin narrow -dash"
    assert normalize_text(once) == once


def test_ixbrl_hidden_header_removed_and_nonnumeric_kept() -> None:
    text = html_to_text((HTML_DIR / "ixbrl_hidden_header.htm").read_bytes())
    for banned in (
        "HIDDENREGISTRANT",
        "RESOURCETEXT",
        "BAREHEADERTEXT",
        "INLINEHIDDEN",
        "SCRIPTTEXT",
        "Synthetic 10-K",  # <title>
        "margin",  # <style>
    ):
        assert banned not in text
    assert "Synthetic Company 10-K annual report." in text
    assert "Net revenue was $4,100 million for fiscal 2025." in text
    assert "Visible after hidden." in text


def test_parse_html_handles_namespaced_tags_from_str() -> None:
    html = (
        '<?xml version="1.0"?><html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>'
        "<ix:header>H</ix:header><p>a <ix:nonNumeric>kept</ix:nonNumeric> b</p></body></html>"
    )
    root = parse_html(html)
    assert element_text(root.find(".//body")) == "a kept b"


def test_table_rows_serialized_with_pipes() -> None:
    text = html_to_text((HTML_DIR / "ixbrl_hidden_header.htm").read_bytes())
    lines = text.split("\n")
    assert "Item 7. | Management's Discussion and Analysis | 45" in lines
    assert "Net revenue | $ | 4,100 | $ | 3,900" in lines
    assert "Change | (5.1 | )% | n/a" in lines


def test_data_rows_dropped_for_narrative() -> None:
    assert is_data_row("Net revenue | $ | 4,100 | $ | 3,900")
    assert is_data_row("Change | (5.1 | )% | n/a")  # 2 of 4 numeric = 50%
    assert not is_data_row("Data center | Strong demand | 4,100")  # 1 of 3 = 33%
    assert not is_data_row("Revenue grew 12% to $4.1 billion.")
    text = html_to_text((HTML_DIR / "ixbrl_hidden_header.htm").read_bytes())
    narrative = drop_data_rows(text)
    assert "Net revenue | $" not in narrative
    assert "Data center | Strong demand | 4,100" in narrative
    assert "Visible after hidden." in narrative


def test_block_elements_and_br_emit_lines() -> None:
    html = "<html><body><div>one<p>two</p>three<br>four</div><ul><li>five</li></ul></body></html>"
    assert html_to_text(html).split("\n") == ["one", "two", "three", "four", "five"]


def test_inline_spans_do_not_split_words() -> None:
    html = "<html><body><p><span>Reve</span><span>nue</span> grew <b>12</b>%</p></body></html>"
    assert html_to_text(html) == "Revenue grew 12%"


def test_page_numbers_toc_and_repeated_headers_dropped() -> None:
    page = (
        "<div>ACME CORP | 2025 Form 10-K</div><p>Body paragraph {i} with plenty of words "
        "in it.</p><p>{n}</p><p>Table of Contents</p>"
    )
    body = "".join(page.format(i=i, n=i + 1) for i in range(6))
    text = html_to_text(f"<html><body>{body}<p>1234</p></body></html>")
    lines = text.split("\n")
    assert "ACME CORP | 2025 Form 10-K" not in lines
    assert not any(ln.isdigit() and len(ln) <= 3 for ln in lines)
    assert "Table of Contents" not in lines
    assert "1234" in lines  # four digits is not a page number
    assert sum(ln.startswith("Body paragraph") for ln in lines) == 6


def test_repeated_long_lines_kept() -> None:
    long_line = "This sentence has well over twelve words in it so it is never a header line."
    body = "".join(f"<p>{long_line}</p>" for _ in range(6))
    assert html_to_text(f"<html><body>{body}</body></html>").count(long_line) == 6


def test_empty_input() -> None:
    assert html_to_text("") == ""
    assert html_to_text(b"   ") == ""
