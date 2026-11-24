"""Module B: footnote redline and hedging drift (PRD §7.3).

Pure: no I/O, no network. Inputs are raw filing HTML; outputs are a `ModuleResult`
whose `extra` payload follows `tests/fixtures/sample_result.py`.

Display text is always the original (post html_text-normalization) text of a note.
Only the comparison copy is normalized (§7.3.3). Every citation quote is a verbatim
slice of the note display text stored in `extra["note_texts"]`.
"""

from __future__ import annotations

import bisect
import contextlib
import difflib
import re
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

from lxml import etree
from lxml import html as lxml_html
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from veritas.config import PARSER_VERSION, t, ti
from veritas.engine.html_text import drop_data_rows, element_text, html_to_text
from veritas.lexicons import (
    ABBREVIATIONS,
    ALPHA_TOKEN,
    CAMEL_BOUNDARY,
    DECIMAL_POINT,
    HEDGE,
    HIDDEN_STYLE,
    ITEM8_END,
    ITEM8_START,
    IX_TEXTBLOCK_HINT,
    IX_TEXTBLOCK_OPEN,
    MATERIAL_EXCLUDE,
    MATERIAL_TERMS,
    MODALS,
    NON_SPACE,
    NORM_AMOUNT,
    NORM_DATE,
    NORM_NUMBER,
    NORM_PERCENT,
    NORM_YEAR,
    NORMALIZATION_ORDER,
    NOTE_CATEGORIES,
    NOTE_HEADER,
    NOTE_NUMBER,
    NOTE_TITLE_PREFIX,
    NOTE_TITLE_PREFIX_BARE,
    NOTES_HEADING,
    SENTENCE_SPLIT,
    TEN_Q_PART1_ITEM1_END,
    TEN_Q_PART1_ITEM1_START,
    TOKEN,
    TRAILING_PAGE_NUMBER,
    WHITESPACE,
    XBRL_PREFIX,
    XML_DECLARATION,
)
from veritas.models import DocRef, Finding, ModuleResult

PairKind = Literal["10-K", "10-Q"]
Pair = tuple[DocRef, str, DocRef, str]

_MASK = "\x01"
_ABBR_RES = tuple(re.compile(r"(?<![A-Za-z])" + re.escape(a)) for a in ABBREVIATIONS)

IX_NAMESPACE = "http://www.xbrl.org/2013/inlineXBRL"
_MAX_NOTES_HEADING_WORDS = 12

KIND_RANK = {"MODIFIED": 0, "INSERTED": 1, "DELETED": 2}


class Span(NamedTuple):
    start: int
    end: int


@dataclass(frozen=True)
class Segment:
    concept: str | None
    title: str
    text: str
    category: str | None

    @property
    def base_key(self) -> str:
        return self.concept or self.title


# ---------------------------------------------------------------------------
# Sentence splitting with offsets
# ---------------------------------------------------------------------------
def _mask_line(line: str) -> str:
    chars = list(line)
    for rx in _ABBR_RES:
        for m in rx.finditer(line):
            for k in range(m.start(), m.end()):
                if chars[k] == ".":
                    chars[k] = _MASK
    for m in DECIMAL_POINT.finditer(line):
        chars[m.start()] = _MASK
    return "".join(chars)


def split_sentences(text: str) -> list[Span]:
    """Sentence spans (offsets into `text`), whitespace-trimmed. Table rows (" | ") stay whole."""
    spans: list[Span] = []
    offset = 0
    for line in text.split("\n"):
        if line.strip():
            if " | " in line:
                pieces = [(0, len(line))]
            else:
                masked = _mask_line(line)
                pieces = []
                cursor = 0
                for m in SENTENCE_SPLIT.finditer(masked):
                    pieces.append((cursor, m.start()))
                    cursor = m.end()
                pieces.append((cursor, len(line)))
            for a, b in pieces:
                while a < b and line[a].isspace():
                    a += 1
                while b > a and line[b - 1].isspace():
                    b -= 1
                if b > a:
                    spans.append(Span(offset + a, offset + b))
        offset += len(line) + 1
    return spans


# ---------------------------------------------------------------------------
# Normalization (comparison copy only)
# ---------------------------------------------------------------------------
def normalize_comparison(s: str) -> str:
    """§7.3.3 in the exact order: dates, amounts, percents, years, numbers, lowercase."""
    for rx, repl in NORMALIZATION_ORDER:
        s = rx.sub(repl, s)
    return WHITESPACE.sub(" ", s).strip().lower()


_MARK_ORDER: tuple[tuple[re.Pattern[str], str], ...] = (
    (NORM_DATE, "<date>"),
    (NORM_AMOUNT, "<amt>"),
    (NORM_PERCENT, "<pct>"),
    (NORM_YEAR, "<year>"),
    (NORM_NUMBER, "<num>"),
)


@dataclass(frozen=True)
class _Unit:
    orig: str
    key: str


def _units(sentence: str) -> list[_Unit]:
    """Whitespace tokens grouped into units whose key is the normalized form.

    Numeric/date/amount spans (the same patterns as §7.3.3, same priority order) collapse
    to one unit so that "$4.1 billion" and "$4.3 billion" compare equal while the
    original text is kept for display.
    """
    marks = [0] * len(sentence)
    tags: dict[int, str] = {}
    next_id = 1
    for rx, tag in _MARK_ORDER:
        for m in rx.finditer(sentence):
            if m.end() == m.start() or any(marks[m.start() : m.end()]):
                continue
            for k in range(m.start(), m.end()):
                marks[k] = next_id
            tags[next_id] = tag
            next_id += 1
    toks = list(NON_SPACE.finditer(sentence))
    units: list[_Unit] = []
    cur_ids: set[int] = set()
    cur_parts: list[str] = []

    def flush() -> None:
        if not cur_parts:
            return
        orig = " ".join(cur_parts)
        key = tags[min(cur_ids)] if cur_ids else orig.lower()
        units.append(_Unit(orig, key))

    for m in toks:
        ids = {i for i in marks[m.start() : m.end()] if i}
        if cur_parts and ids and (ids & cur_ids):
            cur_parts.append(m.group())
            cur_ids |= ids
            continue
        flush()
        cur_parts = [m.group()]
        cur_ids = set(ids)
    flush()
    return units


def _modal_strength(units: list[_Unit]) -> dict[int, int]:
    """Map unit index -> strongest modal phrase covering it (longest phrases first)."""
    text = ""
    starts: list[int] = []
    for u in units:
        starts.append(len(text))
        text += u.orig + " "
    work = text
    out: dict[int, int] = {}
    for rx, strength in MODALS:
        for m in rx.finditer(work):
            lo = bisect.bisect_right(starts, m.start()) - 1
            hi = bisect.bisect_right(starts, max(m.start(), m.end() - 1)) - 1
            for idx in range(max(lo, 0), hi + 1):
                out[idx] = max(out.get(idx, 0), strength)
            work = work[: m.start()] + " " * (m.end() - m.start()) + work[m.end() :]
    return out


def word_diff(prior: str, current: str) -> tuple[float, list[list[str]], bool]:
    """Return (ratio, ops, softened) for one MODIFIED pair (§7.3.4 step 3, §7.3.5)."""
    a = _units(prior)
    b = _units(current)
    sm = difflib.SequenceMatcher(None, [u.key for u in a], [u.key for u in b], autojunk=False)
    ratio = round(sm.ratio(), 6)
    ops: list[list[str]] = []

    def push(op: str, text: str) -> None:
        if ops and ops[-1][0] == op:
            ops[-1][1] += " " + text
        else:
            ops.append([op, text])

    deleted: set[int] = set()
    inserted: set[int] = set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                ua, ub = a[i1 + k], b[j1 + k]
                if ua.orig == ub.orig:
                    push("equal", ub.orig)
                else:
                    push("delete", ua.orig)
                    push("insert", ub.orig)
            continue
        if i2 > i1:
            deleted.update(range(i1, i2))
            push("delete", " ".join(u.orig for u in a[i1:i2]))
        if j2 > j1:
            inserted.update(range(j1, j2))
            push("insert", " ".join(u.orig for u in b[j1:j2]))
    sa = _modal_strength(a)
    sb = _modal_strength(b)
    max_del = max((sa.get(i, 0) for i in deleted), default=0)
    max_ins = max((sb.get(i, 0) for i in inserted), default=0)
    return ratio, ops, max_del > max_ins


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------
def _category(title: str, concept: str | None) -> str | None:
    for source in (title, CAMEL_BOUNDARY.sub(" ", concept) if concept else ""):
        if not source:
            continue
        for name, rx in NOTE_CATEGORIES:
            if rx.search(source):
                return name
    return None


def _clean_title(line: str) -> str:
    s = NOTE_TITLE_PREFIX.sub("", line, count=1)
    s = NOTE_TITLE_PREFIX_BARE.sub("", s, count=1)
    s = TRAILING_PAGE_NUMBER.sub("", s) if len(s.split()) > 1 else s
    return s.strip(" \t.:-")


def _concept_title(concept: str) -> str:
    base = re.sub(r"(TextBlock|Disclosure)$", "", concept)
    base = re.sub(r"(Disclosure)?TextBlock$", "", base)
    return CAMEL_BOUNDARY.sub(" ", base).strip() or concept


def _title_for(text: str, concept: str | None) -> str:
    first = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
    words = len(first.split())
    if first and " | " not in first and words <= ti("NOTE_HEADER_MAX_WORDS"):
        heading_like = bool(NOTE_HEADER.match(first)) or not first.endswith((".", ":", ";"))
        if heading_like:
            cleaned = _clean_title(first)
            if cleaned:
                return cleaned
    return _concept_title(concept) if concept else _clean_title(first) or "Untitled note"


def _drop(el: Any) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    if tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


def _local(el: Any) -> tuple[str, bool]:
    """(lower-case local name, is inline-XBRL) for XML-namespaced or html-prefixed tags."""
    tag = el.tag
    if not isinstance(tag, str):
        return "", False
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        return local.lower(), ns == IX_NAMESPACE
    low = tag.lower()
    if low.startswith("ix:"):
        return low[3:], True
    return low, False


def _attr(el: Any, name: str) -> str:
    return str(el.get(name) or el.get(name.lower()) or "")


def _is_textblock(el: Any) -> bool:
    local, is_ix = _local(el)
    return is_ix and local == "nonnumeric" and _attr(el, "name").lower().endswith("textblock")


def _parse_ix(html: str) -> Any | None:
    """Parse iXBRL as XML first (keeps true nesting); fall back to the HTML parser.

    The HTML parser mis-nests unknown inline elements that wrap block elements, which
    loses the note/policy nesting, so it is only used when XML recovery drops blocks.
    """
    cleaned = XML_DECLARATION.sub("", html, count=1).lstrip()
    expected = len(IX_TEXTBLOCK_OPEN.findall(cleaned))
    best: Any | None = None
    best_n = -1
    attempts: list[Any] = []
    with contextlib.suppress(etree.LxmlError):
        attempts.append(
            etree.fromstring(
                cleaned.encode("utf-8"),
                parser=etree.XMLParser(
                    huge_tree=True,
                    recover=True,
                    resolve_entities=False,
                    no_network=True,
                    encoding="utf-8",
                ),
            )
        )
    for root in attempts + [None]:
        if root is None:
            try:
                root = lxml_html.document_fromstring(
                    cleaned, parser=lxml_html.HTMLParser(huge_tree=True)
                )
            except (ValueError, etree.LxmlError):
                continue
        n = sum(1 for e in root.iter() if _is_textblock(e))
        if n > best_n:
            best, best_n = root, n
        if n >= expected and n > 0:
            return root
    return best


def _ixbrl_segments(html: str) -> list[Segment]:
    if not IX_TEXTBLOCK_HINT.search(html):
        return []
    root = _parse_ix(html)
    if root is None:
        return []
    elements = [e for e in root.iter() if isinstance(e.tag, str)]
    continuations: dict[str, Any] = {}
    for e in elements:
        local, is_ix = _local(e)
        if is_ix and local == "continuation" and _attr(e, "id"):
            continuations[_attr(e, "id")] = e
    for el in elements:
        local, is_ix = _local(el)
        if (is_ix and local == "header") or local in ("script", "style"):
            _drop(el)
    for el in elements:
        if el.getparent() is None or not HIDDEN_STYLE.search(_attr(el, "style")):
            continue
        local, is_ix = _local(el)
        if (is_ix and local == "continuation") or any(
            _local(d) == ("continuation", True) for d in el.iter() if isinstance(d.tag, str)
        ):
            continue
        _drop(el)
    # Chain ownership: every continuation belongs to the nonNumeric whose continuedAt chain
    # reaches it. Policy blocks sit physically inside another note's chain, so a block that
    # encloses a foreign continuation (or sits inside one) is nested in that note.
    owner: dict[str, Any] = {}
    for el in elements:
        local, is_ix = _local(el)
        if not (is_ix and local == "nonnumeric"):
            continue
        nxt = _attr(el, "continuedAt")
        seen_ids: set[str] = set()
        while nxt and nxt not in seen_ids and nxt in continuations:
            seen_ids.add(nxt)
            owner.setdefault(nxt, el)
            nxt = _attr(continuations[nxt], "continuedAt")
    position = {el: i for i, el in enumerate(elements)}
    nested_blocks: set[Any] = set()

    def nest_later(x: Any, y: Any) -> None:
        # Of two entangled chains the later one is the nested (policy) block.
        nested_blocks.add(x if position[x] > position[y] else y)

    for cid, cont in continuations.items():
        own = owner.get(cid)
        if own is None:
            continue
        for anc in cont.iterancestors():
            if _is_textblock(anc) and anc is not own:
                nest_later(own, anc)
            elif _local(anc) == ("continuation", True):
                other = owner.get(_attr(anc, "id"))
                if other is not None and other is not own:
                    nest_later(own, other)
    segments: list[Segment] = []
    for el in root.iter():
        if not isinstance(el.tag, str) or not _is_textblock(el):
            continue
        nested = el in nested_blocks or any(
            _is_textblock(anc) or _local(anc) == ("continuation", True)
            for anc in el.iterancestors()
        )
        if nested:
            continue
        parts = [element_text(el)]
        seen: set[str] = set()
        nxt = _attr(el, "continuedAt")
        while nxt and nxt not in seen and nxt in continuations:
            seen.add(nxt)
            cont = continuations[nxt]
            parts.append(element_text(cont))
            nxt = _attr(cont, "continuedAt")
        text = drop_data_rows("\n".join(p for p in parts if p.strip())).strip()
        if not text:
            continue
        concept = XBRL_PREFIX.sub("", _attr(el, "name"))
        title = _title_for(text, concept)
        segments.append(Segment(concept, title, text, _category(title, concept)))
    return segments


def longest_span(
    text: str, start_rx: re.Pattern[str], end_rx: re.Pattern[str], require_end: bool = False
) -> tuple[int, int] | None:
    """Longest-span rule (§7.2.2): every start match is tried, the longest span wins."""
    ends = [m.start() for m in end_rx.finditer(text)]
    best: tuple[int, int] | None = None
    for m in start_rx.finditer(text):
        i = bisect.bisect_left(ends, m.end())
        if i >= len(ends) and require_end:
            continue
        end = ends[i] if i < len(ends) else len(text)
        if best is None or end - m.start() > best[1] - best[0]:
            best = (m.start(), end)
    return best


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", pos)
    return a, (len(text) if b < 0 else b)


@dataclass(frozen=True)
class _Header:
    start: int
    number: int
    title: str
    prefixed: bool


def _find_headers(text: str, start: int, end: int) -> list[_Header]:
    headers: list[_Header] = []
    last = 0
    offset = start
    for line in text[start:end].split("\n"):
        stripped = line.strip()
        pos = offset
        offset += len(line) + 1
        if not stripped or len(stripped.split()) > ti("NOTE_HEADER_MAX_WORDS"):
            continue
        m = NOTE_HEADER.match(stripped)
        if not m or not stripped[m.end() - 1].isupper():
            continue
        if stripped.endswith(".") and len(stripped.split()) > 10:
            continue
        nm = NOTE_NUMBER.match(stripped)
        if nm is None:
            continue
        number = int(nm.group(1))
        prefixed = stripped.lower().startswith("note")
        if number == last + 1 or (prefixed and number == 1):
            headers.append(_Header(pos, number, _clean_title(stripped), prefixed))
            last = number
    return headers


def _header_segments(text: str, kind: PairKind) -> list[Segment]:
    if kind == "10-K":
        region = longest_span(text, ITEM8_START, ITEM8_END)
    else:
        region = longest_span(text, TEN_Q_PART1_ITEM1_START, TEN_Q_PART1_ITEM1_END)

    def heading_start(lo: int, hi: int) -> int | None:
        for m in NOTES_HEADING.finditer(text, lo, hi):
            a, b = _line_bounds(text, m.start())
            if len(text[a:b].split()) <= _MAX_NOTES_HEADING_WORDS:
                return a
        return None

    start: int | None = None
    end = len(text)
    if region is not None:
        start = heading_start(region[0], region[1])
        end = region[1]
    if start is None:
        # Item 8 / Part I Item 1 may only reference F-pages: search the rest of the filing.
        start = heading_start(0, len(text))
        end = len(text)
        if start is None:
            return []
    headers = _find_headers(text, start, end)
    segs: dict[str, tuple[int, Segment]] = {}
    for i, h in enumerate(headers):
        stop = headers[i + 1].start if i + 1 < len(headers) else end
        body = text[h.start : stop].strip()
        title = h.title or "Untitled note"
        seg = Segment(None, title, body, _category(title, None))
        key = title.lower()
        prev = segs.get(key)
        if prev is None or len(body.split()) > len(prev[1].text.split()):
            segs[key] = (h.start, seg)
    out = sorted(segs.values(), key=lambda p: p[0])
    return [Segment(s.concept, s.title, drop_data_rows(s.text).strip(), s.category) for _, s in out]


def segment_filing(html: str, kind: PairKind) -> tuple[list[Segment], str]:
    """Return (segments, method) with method in {"ixbrl", "header", "none"}."""
    ix = _ixbrl_segments(html)
    if len(ix) >= 3:
        return ix, "ixbrl"
    hdr = _header_segments(html_to_text(html), kind)
    if hdr:
        return hdr, "header"
    if ix:
        return ix, "ixbrl"
    return [], "none"


# ---------------------------------------------------------------------------
# Cross-year note matching
# ---------------------------------------------------------------------------
def _title_tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in ALPHA_TOKEN.findall(title.lower()) if w != "note")


def _greedy(
    pairs: list[tuple[float, int, int]], used_p: set[int], used_c: set[int]
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for _score, pi, ci in sorted(pairs, key=lambda x: (-round(x[0], 12), x[2], x[1])):
        if pi in used_p or ci in used_c:
            continue
        used_p.add(pi)
        used_c.add(ci)
        out.append((pi, ci))
    return out


def _tfidf_cosine(
    left: list[str], right: list[str], ngram: tuple[int, int] = (1, 2)
) -> list[list[float]]:
    if not left or not right:
        return [[0.0] * len(right) for _ in left]
    try:
        vec = TfidfVectorizer(ngram_range=ngram)
        mat = vec.fit_transform(left + right)
    except ValueError:
        return [[0.0] * len(right) for _ in left]
    sims = cosine_similarity(mat[: len(left)], mat[len(left) :])
    return [[float(x) for x in row] for row in sims]


def match_notes(
    prior: list[Segment], current: list[Segment]
) -> list[tuple[Segment | None, Segment | None]]:
    """Concept -> title Jaccard -> body TF-IDF cosine. Note numbers are never used."""
    used_p: set[int] = set()
    used_c: set[int] = set()
    matched: dict[int, int] = {}
    by_concept: dict[str, list[int]] = {}
    for i, s in enumerate(prior):
        if s.concept:
            by_concept.setdefault(s.concept, []).append(i)
    for ci, c in enumerate(current):
        if c.concept and by_concept.get(c.concept):
            pi = by_concept[c.concept].pop(0)
            matched[ci] = pi
            used_p.add(pi)
            used_c.add(ci)
    jac: list[tuple[float, int, int]] = []
    ptoks = [_title_tokens(s.title) for s in prior]
    ctoks = [_title_tokens(s.title) for s in current]
    for pi, pt in enumerate(ptoks):
        for ci, ct in enumerate(ctoks):
            if pi in used_p or ci in used_c or not pt or not ct:
                continue
            j = len(pt & ct) / len(pt | ct)
            if j >= t("NOTE_TITLE_JACCARD"):
                jac.append((j, pi, ci))
    for pi, ci in _greedy(jac, used_p, used_c):
        matched[ci] = pi
    rem_p = [i for i in range(len(prior)) if i not in used_p]
    rem_c = [i for i in range(len(current)) if i not in used_c]
    if rem_p and rem_c:
        sims = _tfidf_cosine(
            [normalize_comparison(prior[i].text) for i in rem_p],
            [normalize_comparison(current[i].text) for i in rem_c],
        )
        cand = [
            (sims[a][b], rem_p[a], rem_c[b])
            for a in range(len(rem_p))
            for b in range(len(rem_c))
            if sims[a][b] >= t("NOTE_BODY_COSINE")
        ]
        for pi, ci in _greedy(cand, used_p, used_c):
            matched[ci] = pi
    out: list[tuple[Segment | None, Segment | None]] = []
    for ci, c in enumerate(current):
        out.append((prior[matched[ci]] if ci in matched else None, c))
    matched_prior = set(matched.values())
    for pi, p in enumerate(prior):
        if pi not in matched_prior:
            out.append((p, None))
    return out


# ---------------------------------------------------------------------------
# Alignment and material edits
# ---------------------------------------------------------------------------
@dataclass
class NoteDiff:
    rows: list[dict[str, Any]]
    truncated: bool


def _is_material(
    kind: str, prior: str | None, current: str | None, ratio: float | None, softened: bool
) -> bool:
    sides = [s for s in (prior, current) if s]
    if not sides or any(" | " in s for s in sides):
        return False  # table rows are not sentences (D-401)
    if any(MATERIAL_EXCLUDE.search(s) for s in sides):
        return False
    if max(sum(1 for w in s.split() if w != "|") for s in sides) < ti("MATERIAL_MIN_WORDS"):
        return False
    if not any(MATERIAL_TERMS.search(s) for s in sides):
        return False
    if kind in ("INSERTED", "DELETED"):
        return True
    return softened or (ratio is not None and ratio < t("MATERIAL_RATIO_MAX"))


def diff_note(prior_text: str, current_text: str, scored_category: bool) -> NoteDiff:
    """§7.3.4 alignment of one matched note. Rows carry spans into the given texts."""
    ps = split_sentences(prior_text)
    cs = split_sentences(current_text)
    pn = [normalize_comparison(prior_text[s.start : s.end]) for s in ps]
    cn = [normalize_comparison(current_text[s.start : s.end]) for s in cs]
    common = (set(pn) & set(cn)) - {""}
    p_res = [i for i, n in enumerate(pn) if n not in common]
    c_res = [i for i, n in enumerate(cn) if n not in common]
    if len(p_res) > ti("RESIDUE_MAX_SENTENCES") or len(c_res) > ti("RESIDUE_MAX_SENTENCES"):
        return NoteDiff([], True)
    sims = _tfidf_cosine([pn[i] for i in p_res], [cn[i] for i in c_res])
    cand = [
        (sims[a][b], a, b)
        for a in range(len(p_res))
        for b in range(len(c_res))
        if sims[a][b] >= t("SENTENCE_MATCH_COSINE")
    ]
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs = _greedy(cand, used_a, used_b)
    rows: list[dict[str, Any]] = []

    def row(kind: str, pi: int | None, ci: int | None) -> dict[str, Any]:
        ptxt = prior_text[ps[pi].start : ps[pi].end] if pi is not None else None
        ctxt = current_text[cs[ci].start : cs[ci].end] if ci is not None else None
        ratio: float | None = None
        ops: list[list[str]] | None = None
        softened = False
        if kind == "MODIFIED" and ptxt is not None and ctxt is not None:
            ratio, ops, softened = word_diff(ptxt, ctxt)
        material = scored_category and _is_material(kind, ptxt, ctxt, ratio, softened)
        return {
            "kind": kind,
            "prior": ptxt,
            "current": ctxt,
            "material": material,
            "softened": softened,
            "ratio": ratio,
            "ops": ops,
            "prior_span": [ps[pi].start, ps[pi].end] if pi is not None else None,
            "current_span": [cs[ci].start, cs[ci].end] if ci is not None else None,
        }

    for a, b in pairs:
        rows.append(row("MODIFIED", p_res[a], c_res[b]))
    for b, ci in enumerate(c_res):
        if b not in used_b:
            rows.append(row("INSERTED", None, ci))
    for a, pi in enumerate(p_res):
        if a not in used_a:
            rows.append(row("DELETED", pi, None))

    def sort_key(r: dict[str, Any]) -> tuple[int, int]:
        span = r["current_span"] if r["current_span"] is not None else r["prior_span"]
        return (KIND_RANK[r["kind"]], int(span[0]))

    rows.sort(key=sort_key)
    return NoteDiff(rows, False)


# ---------------------------------------------------------------------------
# Hedging drift
# ---------------------------------------------------------------------------
def hedging_stats(text: str) -> tuple[int, int]:
    """(hedge hits, tokens) on already-normalized text."""
    return len(HEDGE.findall(text)), len(TOKEN.findall(text))


def hedging_drift(prior_norm: str, current_norm: str) -> dict[str, Any]:
    ph, pt = hedging_stats(prior_norm)
    ch, ct = hedging_stats(current_norm)
    prior_rate = round(ph / pt * 1000, 3) if pt else None
    cur_rate = round(ch / ct * 1000, 3) if ct else None
    out: dict[str, Any] = {
        "status": "INSUFFICIENT_TEXT",
        "label": "Insufficient text",
        "prior_rate": prior_rate,
        "current_rate": cur_rate,
        "prior_hits": ph,
        "current_hits": ch,
        "prior_tokens": pt,
        "current_tokens": ct,
    }
    minimum = ti("HEDGE_MIN_TOKENS")
    if pt < minimum or ct < minimum or prior_rate is None or cur_rate is None:
        return out
    abs_inc = cur_rate - prior_rate
    rel_ok = cur_rate > prior_rate * (1 + t("HEDGE_REL_INC"))
    fires = rel_ok and abs_inc >= t("HEDGE_ABS_INC_PER_K") and (ch - ph) >= ti("HEDGE_MIN_HIT_INC")
    out["status"] = "FIRED" if fires else "NO_DRIFT"
    out["label"] = "Hedging language increased" if fires else "No significant drift"
    return out


# ---------------------------------------------------------------------------
# Pair processing
# ---------------------------------------------------------------------------
def _unique_keys(items: list[tuple[Segment | None, Segment | None]]) -> list[str]:
    seen: dict[str, int] = {}
    keys: list[str] = []
    for p, c in items:
        base = (c or p).base_key if (c or p) else "note"  # type: ignore[union-attr]
        n = seen.get(base, 0) + 1
        seen[base] = n
        keys.append(base if n == 1 else f"{base}#{n}")
    return keys


def _process_pair(
    kind: PairKind,
    prior_ref: DocRef,
    prior_html: str,
    cur_ref: DocRef,
    cur_html: str,
) -> dict[str, Any]:
    p_segs, p_method = segment_filing(prior_html, kind)
    c_segs, c_method = segment_filing(cur_html, kind)
    payload: dict[str, Any] = {
        "prior": prior_ref.to_dict(),
        "current": cur_ref.to_dict(),
        "notes": [],
        "status": "OK",
        "reason": None,
        "method": {"prior": p_method, "current": c_method},
        "segments": {"prior": len(p_segs), "current": len(c_segs)},
        "counts": {"matched": 0, "new": 0, "removed": 0, "material": 0, "truncated": 0},
    }
    result: dict[str, Any] = {
        "payload": payload,
        "findings": [],
        "note_texts": {},
        "assessed": False,
        "hedge_prior": "",
        "hedge_current": "",
    }
    if not p_segs or not c_segs:
        payload["status"] = "NOT_FOUND"
        side = "prior" if not p_segs else "current"
        payload["reason"] = f"Footnotes could not be located in the {side} filing"
        return result
    result["assessed"] = True
    items = match_notes(p_segs, c_segs)
    keys = _unique_keys(items)
    category_findings = "footnotes" if kind == "10-K" else "footnotes_10q"
    findings: list[Finding] = []
    prior_corpus: list[str] = []
    cur_corpus: list[str] = []
    counts: dict[str, int] = payload["counts"]
    for (p, c), key in zip(items, keys, strict=True):
        seg = c or p
        assert seg is not None
        category = (c.category if c else None) or (p.category if p else None)
        ptext = p.text if p else ""
        ctext = c.text if c else ""
        result["note_texts"][key] = {"prior": ptext, "current": ctext}
        note: dict[str, Any] = {
            "key": key,
            "concept": seg.concept,
            "prior_key": p.base_key if p else None,
            "title": seg.title,
            "category": category,
            "status": "MATCHED",
            "truncated": False,
            "counts": {"inserted": 0, "deleted": 0, "modified": 0, "softened": 0, "material": 0},
            "rows": [],
        }
        if p is None:
            note["status"] = "NEW NOTE"
            counts["new"] += 1
        elif c is None:
            note["status"] = "REMOVED NOTE"
            counts["removed"] += 1
        else:
            counts["matched"] += 1
            if category is not None:
                prior_corpus.append(normalize_comparison(ptext))
                cur_corpus.append(normalize_comparison(ctext))
            if ptext != ctext:
                nd = diff_note(ptext, ctext, category is not None)
                if nd.truncated:
                    note["status"] = "TRUNCATED"
                    note["truncated"] = True
                    counts["truncated"] += 1
                note["rows"] = nd.rows
                nc = note["counts"]
                for r in nd.rows:
                    nc[
                        {"MODIFIED": "modified", "INSERTED": "inserted", "DELETED": "deleted"}[
                            r["kind"]
                        ]
                    ] += 1
                    nc["softened"] += 1 if r["softened"] else 0
                    nc["material"] += 1 if r["material"] else 0
                counts["material"] += nc["material"]
                for r in nd.rows:
                    if not r["material"]:
                        continue
                    deleted = r["kind"] == "DELETED"
                    src = p if deleted else c
                    ref = prior_ref if deleted else cur_ref
                    assert src is not None
                    span = r["prior_span"] if deleted else r["current_span"]
                    quote = r["prior"] if deleted else r["current"]
                    section = f"Note: {src.concept or src.title}"
                    cite = ref.cite(section, PARSER_VERSION, quote, int(span[0]), int(span[1]))
                    findings.append(
                        Finding(
                            id=f"B-{kind}-{len(findings) + 1:03d}",
                            module="B",
                            category=category_findings,
                            severity="WARNING",
                            title=f"Material edit ({r['kind'].lower()}): {seg.title}",
                            explanation=_explain(r, seg.title, category),
                            check=None,
                            claims=(),
                            contradiction=False,
                            citations=(cite,),
                        )
                    )
        payload["notes"].append(note)
    payload["counts"] = counts
    result["findings"] = findings
    result["hedge_prior"] = "\n".join(prior_corpus)
    result["hedge_current"] = "\n".join(cur_corpus)
    return result


def _explain(row: dict[str, Any], title: str, category: str | None) -> str:
    kind = row["kind"]
    where = f"in the {title} note ({category})"
    if kind == "INSERTED":
        base = f"A sentence was added {where} that cites a recognition or estimate term."
    elif kind == "DELETED":
        base = f"A sentence was removed {where} that cited a recognition or estimate term."
    elif row["softened"]:
        base = f"A sentence {where} was reworded with weaker modal language (e.g. 'will' to 'may')."
    else:
        base = f"A sentence {where} was substantially reworded."
    return base + " Flagged for review; compare the cited filings."


def run_footnotes(pair_10k: Pair | None, pair_10q: Pair | None) -> ModuleResult:
    pairs: dict[str, Any] = {}
    note_texts: dict[str, Any] = {}
    findings: list[Finding] = []
    assessed: dict[str, bool] = {}
    hedge_inputs: tuple[str, str] | None = None
    for kind, pair in (("10-K", pair_10k), ("10-Q", pair_10q)):
        if pair is None:
            continue
        out = _process_pair(kind, pair[0], pair[1], pair[2], pair[3])  # type: ignore[arg-type]
        pairs[kind] = out["payload"]
        note_texts[kind] = out["note_texts"]
        findings.extend(out["findings"])
        assessed[kind] = out["assessed"]
        if kind == "10-K" and out["assessed"]:
            hedge_inputs = (out["hedge_prior"], out["hedge_current"])
    if hedge_inputs is not None:
        hedging = hedging_drift(*hedge_inputs)
    else:
        hedging = {
            "status": "NOT_ASSESSED",
            "label": "Not assessed",
            "prior_rate": None,
            "current_rate": None,
            "prior_hits": None,
            "current_hits": None,
            "prior_tokens": None,
            "current_tokens": None,
        }
    if hedging["status"] == "FIRED" and pair_10k is not None:
        findings.append(
            Finding(
                id="B-HEDGE-001",
                module="B",
                category="hedging",
                severity="WARNING",
                title="Hedging language drift in category footnotes",
                explanation=(
                    f"Hedge terms per 1,000 tokens rose from {hedging['prior_rate']:.1f} to "
                    f"{hedging['current_rate']:.1f} ({hedging['prior_hits']} to "
                    f"{hedging['current_hits']} hits) across matched category notes. "
                    "Flagged for review."
                ),
                check=None,
                claims=(),
                contradiction=False,
                citations=(pair_10k[2].cite("Notes (category notes)", PARSER_VERSION),),
            )
        )
    extra: dict[str, Any] = {"pairs": pairs, "hedging": hedging, "note_texts": note_texts}
    if assessed.get("10-K"):
        status: Literal["ok", "partial", "unavailable"] = "ok"
        reason = None
    elif assessed.get("10-Q"):
        status = "partial"
        reason = "10-K footnote pair not assessed; only the 10-Q pair was compared"
    else:
        status = "unavailable"
        reason = (
            "No comparable filing pair available"
            if not pairs
            else "Footnotes could not be located in the filings provided"
        )
    return ModuleResult(status, tuple(findings), (), reason, extra)
