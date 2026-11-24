"""HTML preprocessing shared by every text module (PRD §7.2.1).

Pure functions: no I/O. The output of `html_to_text` is the "cached section text"
universe: every citation offset produced downstream refers to normalized text
produced here (or a slice of it).

Public API
----------
- `normalize_text(s)`: NFKC + space/quote/dash/soft-hyphen mapping + collapse.
- `parse_html(html)`: lxml tree with ix:header, script, style and
  `display:none` elements removed and every other ix:* tag unwrapped.
- `element_text(el)`: block-aware normalized text of one element.
- `html_to_text(html)`: whole document -> normalized lines, noise lines dropped.
- `is_data_row(line)` / `drop_data_rows(text)`: narrative-only numeric row filter.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Any

import lxml.etree as etree
import lxml.html

from veritas.config import t, ti
from veritas.lexicons import (
    HIDDEN_STYLE,
    NUMERIC_CELL,
    PAGE_NUMBER_LINE,
    TOC_LINE,
    WHITESPACE,
)

IX_NAMESPACE = "http://www.xbrl.org/2013/inlineXBRL"
CELL_SEPARATOR = " | "

# Characters mapped before whitespace collapse (§7.2.1). NFKC runs first and already
# folds most compatibility spaces; the explicit entries keep the mapping total.
_CHAR_MAP: dict[int, str | None] = {
    0x00A0: " ",  # no-break space
    0x2002: " ",
    0x2003: " ",
    0x2007: " ",  # figure space
    0x2009: " ",  # thin space
    0x200A: " ",
    0x200B: " ",  # zero-width space
    0x202F: " ",  # narrow no-break space
    0x2018: "'",
    0x2019: "'",
    0x201A: "'",
    0x2032: "'",
    0x201C: '"',
    0x201D: '"',
    0x201E: '"',
    0x2010: "-",  # hyphen (NFKC target of U+2011)
    0x2011: "-",
    0x2012: "-",
    0x2013: "-",  # en dash
    0x2014: "-",  # em dash
    0x2015: "-",
    0x2212: "-",  # minus sign
    0x00AD: None,  # soft hyphen
    0xFEFF: None,  # BOM / zero-width no-break space
    0x200C: None,
    0x200D: None,
}

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "td",
        "th",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tr",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "body",
        "section",
        "article",
        "header",
        "footer",
        "blockquote",
        "pre",
        "center",
        "hr",
        "caption",
        "form",
        "address",
        "nav",
        "aside",
        "figure",
        "figcaption",
    }
)
_SKIP_TAGS = frozenset({"head", "script", "style", "title", "meta", "link", "noscript"})
_REMOVE_TAGS = frozenset({"script", "style"})


def normalize_text(s: str) -> str:
    """§7.2.1 normalization: NFKC, map spaces/quotes/dashes, delete soft hyphens,
    collapse whitespace and strip. Idempotent."""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_CHAR_MAP)
    return WHITESPACE.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# Parsing & tree cleanup
# ---------------------------------------------------------------------------
def _tag_info(el: Any) -> tuple[str, bool]:
    """Return (lower-case local name, is_inline_xbrl) for an element.

    Handles both namespaced tags (`{ns}header`, from an XML parse) and prefixed
    tags (`ix:header`, which is how lxml.html reports them)."""
    tag = el.tag
    if not isinstance(tag, str):
        return "", False
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        return local.lower(), ns == IX_NAMESPACE
    low = tag.lower()
    if low.startswith("ix:"):
        return low[3:], True
    if ":" in low:  # other prefixes (xbrli:, link:) only occur inside ix:header
        return low.rpartition(":")[2], False
    return low, False


def _append_text(parent: Any, index: int, text: str) -> None:
    """Attach `text` at child position `index` of `parent` (before child `index`)."""
    if not text:
        return
    if index == 0:
        parent.text = (parent.text or "") + text
    else:
        prev = parent[index - 1]
        prev.tail = (prev.tail or "") + text


def _remove_keep_tail(el: Any) -> None:
    parent = el.getparent()
    if parent is None:
        return
    idx = parent.index(el)
    tail = el.tail or ""
    parent.remove(el)
    _append_text(parent, idx, tail)


def _unwrap(el: Any) -> None:
    parent = el.getparent()
    if parent is None:
        return
    idx = parent.index(el)
    text = el.text or ""
    tail = el.tail or ""
    children = list(el)
    parent.remove(el)
    _append_text(parent, idx, text)
    for offset, child in enumerate(children):
        parent.insert(idx + offset, child)
    _append_text(parent, idx + len(children), tail)


def _is_hidden(el: Any) -> bool:
    style = el.get("style") if isinstance(el.tag, str) else None
    return isinstance(style, str) and HIDDEN_STYLE.search(style) is not None


def clean_tree(root: Any) -> Any:
    """Remove ix:header/script/style/hidden elements and unwrap other ix:* tags."""
    doomed = []
    for el in root.iter():
        if el is root:
            continue
        local, is_ix = _tag_info(el)
        if not isinstance(el.tag, str):
            if isinstance(el, (etree._Comment, etree._ProcessingInstruction)):
                doomed.append(el)
            continue
        if (is_ix and local == "header") or local in _REMOVE_TAGS or _is_hidden(el):
            doomed.append(el)
    for el in doomed:
        _remove_keep_tail(el)
    wrappers = [el for el in root.iter() if el is not root and _tag_info(el)[1]]
    for el in wrappers:
        _unwrap(el)
    return root


def parse_html(html: str | bytes) -> lxml.html.HtmlElement:
    """Parse a filing document with lxml.html (huge_tree) and clean it (§7.2.1)."""
    parser = lxml.html.HTMLParser(huge_tree=True, remove_comments=True, remove_pis=True)
    if isinstance(html, str):
        stripped = html.lstrip()
        if stripped.startswith("<?xml"):
            end = stripped.find("?>")
            html = stripped[end + 2 :] if end >= 0 else stripped
    if not html or (isinstance(html, (str, bytes)) and not html.strip()):
        root: lxml.html.HtmlElement = lxml.html.fromstring("<html><body></body></html>")
        return root
    root = lxml.html.document_fromstring(html, parser=parser)
    clean_tree(root)
    return root


# ---------------------------------------------------------------------------
# Text emission
# ---------------------------------------------------------------------------
def _raw_lines(el: Any) -> list[str]:
    """Walk `el` and return raw (unnormalized) lines, one per block element."""
    lines: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            lines.append("".join(buf))
            buf.clear()

    stack: list[tuple[Any, bool]] = [(el, False)]
    while stack:
        node, closing = stack.pop()
        is_root = node is el
        if closing:
            if _tag_info(node)[0] in _BLOCK_TAGS:
                flush()
            if not is_root and node.tail:
                buf.append(node.tail)
            continue
        local, _ = _tag_info(node)
        if not local or local in _SKIP_TAGS:
            if not is_root and node.tail:
                buf.append(node.tail)
            continue
        if local == "tr":
            flush()
            row = _row_text(node)
            if row:
                lines.append(row)
            if not is_root and node.tail:
                buf.append(node.tail)
            continue
        if local == "br":
            flush()
            if not is_root and node.tail:
                buf.append(node.tail)
            continue
        if local in _BLOCK_TAGS:
            flush()
        if node.text:
            buf.append(node.text)
        stack.append((node, True))
        for child in reversed(node):
            stack.append((child, False))
    flush()
    return lines


def _row_text(tr: Any) -> str:
    """Serialize a table row as its non-empty normalized cells joined by ' | '."""
    cells: list[str] = []
    for child in tr:
        local, _ = _tag_info(child)
        if local not in ("td", "th"):
            continue
        text = normalize_text(" ".join(_raw_lines(child)))
        if text:
            cells.append(text)
    return CELL_SEPARATOR.join(cells)


def element_text(el: Any) -> str:
    """Normalized, block-aware text of one element: one line per block element,
    table rows serialized as cells joined with ' | '. Empty lines are omitted."""
    out: list[str] = []
    for raw in _raw_lines(el):
        line = normalize_text(raw)
        if line:
            out.append(line)
    return "\n".join(out)


def _word_count(line: str) -> int:
    return sum(1 for w in line.split() if w != "|")


def drop_noise_lines(lines: list[str]) -> list[str]:
    """Drop page numbers, 'table of contents' lines and repeated short header lines."""
    kept = [ln for ln in lines if not PAGE_NUMBER_LINE.match(ln) and not TOC_LINE.match(ln)]
    max_words = ti("HEADER_REPEAT_MAX_WORDS")
    min_count = ti("HEADER_REPEAT_MIN_COUNT")
    counts = Counter(kept)
    repeated = {ln for ln, n in counts.items() if n >= min_count and _word_count(ln) <= max_words}
    return [ln for ln in kept if ln not in repeated]


def html_to_text(html: str | bytes) -> str:
    """Whole document -> normalized text lines joined with '\\n' (§7.2.1)."""
    root = parse_html(html)
    body = root.find(".//body")
    target = body if body is not None else root
    text = element_text(target)
    if not text:
        return ""
    return "\n".join(drop_noise_lines(text.split("\n")))


# ---------------------------------------------------------------------------
# Narrative-only data-row filter
# ---------------------------------------------------------------------------
def is_data_row(line: str) -> bool:
    """True when >= DATA_ROW_NUMERIC_FRAC of the non-empty cells are numeric."""
    cells = [c.strip() for c in line.split(CELL_SEPARATOR)]
    cells = [c for c in cells if c]
    if not cells:
        return False
    numeric = sum(1 for c in cells if NUMERIC_CELL.match(c))
    return numeric / len(cells) >= t("DATA_ROW_NUMERIC_FRAC")


def drop_data_rows(text: str) -> str:
    """Narrative scanning only: drop rows where >= 40% of non-empty cells are numeric."""
    return "\n".join(ln for ln in text.split("\n") if not is_data_row(ln))
