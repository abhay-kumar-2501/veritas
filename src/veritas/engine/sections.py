"""Section location over normalized filing text (PRD §7.2.2).

Input text is always the output of `html_text.html_to_text` (post-normalization).
`Section.text` is an exact slice of that text, and it is the cached section text that
every narrative citation offset refers to. Nothing here ever falls back to the
whole document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from veritas.config import ti
from veritas.engine.html_text import CELL_SEPARATOR, is_data_row
from veritas.lexicons import (
    EARNINGS_SUMMARY_HEADING,
    EX13_MDA_END,
    EX13_MDA_START,
    FORWARD_LOOKING_PARA,
    INCORPORATED_BY_REFERENCE,
    MDA_10K_END,
    MDA_10K_START,
    MDA_10Q_END,
    MDA_10Q_START,
)

SectionStatus = Literal["OK", "NOT_FOUND", "TRUNCATED"]

LABEL_10K = "10-K Item 7"
LABEL_10Q = "10-Q Item 2"
LABEL_EX13 = "EX-13 MD&A"
LABEL_8K = "8-K EX-99.1"
# Consecutive numeric lines (single- or multi-cell) that end an earnings release (D-312).


@dataclass(frozen=True)
class Section:
    status: SectionStatus
    text: str  # exact slice of the source text ("" unless status is OK/TRUNCATED)
    word_count: int
    label: str
    start: int | None = None  # offsets of `text` within the source document text
    end: int | None = None
    reason: str | None = None


def word_count(text: str) -> int:
    return len(text.split())


def find_longest_span(
    text: str, start_re: re.Pattern[str], end_re: re.Pattern[str]
) -> tuple[int, int] | None:
    """Longest-span rule (§7.2.2): for every start match, the span runs to the first
    end match after it; the longest span wins (earliest on ties). A start with no end
    match after it is not a candidate."""
    best: tuple[int, int] | None = None
    for m in start_re.finditer(text):
        e = end_re.search(text, m.end())
        if e is None:
            continue
        span = (m.start(), e.start())
        if best is None or (span[1] - span[0]) > (best[1] - best[0]):
            best = span
    return best


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    while end > start and text[end - 1].isspace():
        end -= 1
    while start < end and text[start].isspace():
        start += 1
    return start, end


def _not_found(label: str, reason: str) -> Section:
    return Section(status="NOT_FOUND", text="", word_count=0, label=label, reason=reason)


def _ok(text: str, span: tuple[int, int], label: str) -> Section:
    start, end = _trimmed(text, *span)
    body = text[start:end]
    return Section(
        status="OK", text=body, word_count=word_count(body), label=label, start=start, end=end
    )


def _is_10k(form: str) -> bool:
    return form.upper().startswith("10-K")


def _is_10q(form: str) -> bool:
    return form.upper().startswith("10-Q")


def needs_ex13(text: str) -> bool:
    """True when the 10-K Item 7 span is too short and incorporates MD&A by reference
    (Exhibit 13 fallback, §7.2.2)."""
    span = find_longest_span(text, MDA_10K_START, MDA_10K_END)
    if span is None:
        return False
    body = text[span[0] : span[1]]
    return (
        word_count(body) < ti("MDA_10K_MIN_WORDS")
        and INCORPORATED_BY_REFERENCE.search(body) is not None
    )


def locate_mda(text: str, form: str, ex13_text: str | None = None) -> Section:
    """Locate MD&A in a 10-K (Item 7) or 10-Q (Part I Item 2), with the Exhibit 13
    fallback for 10-Ks that incorporate MD&A by reference."""
    if _is_10k(form):
        label, start_re, end_re = LABEL_10K, MDA_10K_START, MDA_10K_END
        min_words = ti("MDA_10K_MIN_WORDS")
    elif _is_10q(form):
        label, start_re, end_re = LABEL_10Q, MDA_10Q_START, MDA_10Q_END
        min_words = ti("MDA_10Q_MIN_WORDS")
    else:
        return _not_found("MD&A", f"unsupported form {form!r} for MD&A location")

    span = find_longest_span(text, start_re, end_re)
    if span is not None and word_count(text[span[0] : span[1]]) >= min_words:
        return _ok(text, span, label)

    if _is_10k(form) and needs_ex13(text):
        if ex13_text is None:
            return _not_found(
                LABEL_EX13, "MD&A incorporated by reference to Exhibit 13; exhibit unavailable"
            )
        ex_span = find_longest_span(ex13_text, EX13_MDA_START, EX13_MDA_END)
        if ex_span is not None and word_count(ex13_text[ex_span[0] : ex_span[1]]) >= min_words:
            return _ok(ex13_text, ex_span, LABEL_EX13)
        return _not_found(LABEL_EX13, "MD&A heading not found in Exhibit 13")

    if span is None:
        return _not_found(label, "MD&A heading not found")
    words = word_count(text[span[0] : span[1]])
    return _not_found(label, f"MD&A span too short ({words} words < {min_words})")


def locate_earnings_release(text: str) -> Section:
    """8-K EX-99.1 narrative: from the start of the exhibit text up to the first of
    - the first data table (cut before its contiguous multi-cell header rows),
    - a run of config EARNINGS_DATA_RUN_MIN consecutive numeric lines,
    - a financial/operational summary heading (slide-deck releases, D-312),
    - the first paragraph mentioning forward-looking statements."""
    cut = len(text)
    pos = 0
    row_run_start: int | None = None  # offset of the current run of multi-cell rows
    data_run_start: int | None = None  # offset of the current run of numeric lines
    data_run_len = 0
    for line in text.split("\n"):
        is_row = CELL_SEPARATOR in line
        if is_row and row_run_start is None:
            row_run_start = pos
        elif not is_row:
            row_run_start = None
        numeric = bool(line.strip()) and is_data_row(line)
        if numeric:
            if data_run_start is None:
                data_run_start = pos
            data_run_len += 1
        else:
            data_run_start, data_run_len = None, 0
        if is_row and numeric:
            cut = row_run_start if row_run_start is not None else pos
            break
        if data_run_start is not None and data_run_len >= ti("EARNINGS_DATA_RUN_MIN"):
            cut = data_run_start
            break
        if FORWARD_LOOKING_PARA.search(line) or EARNINGS_SUMMARY_HEADING.search(line):
            cut = pos
            break
        pos += len(line) + 1
    start, end = _trimmed(text, 0, cut)
    if end <= start:
        return _not_found(LABEL_8K, "no narrative text before the first data table")
    return _ok(text, (start, end), LABEL_8K)
