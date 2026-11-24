"""Module C: Proxy Incentive X-Ray (PRD §7.4).

Pure: no I/O, no network. Detects which performance metrics drive incentive pay in the
CD&A and raises two scored WARNING flags (C1, C2) plus one unscored neutral note.

Ratios (`goodwill_assets`, `goodwill_assets_prior`) are fractions (0.40 = 40%).
Every metric citation quote is a verbatim slice of `extra["cda_text"]`.
"""

from __future__ import annotations

import re
from typing import Any

from veritas.config import PARSER_VERSION, t, ti
from veritas.engine.footnote_diff import longest_span, split_sentences
from veritas.engine.html_text import html_to_text
from veritas.lexicons import (
    CDA_END,
    CDA_FALLBACK_START,
    CDA_SELF_REFERENCE_PREFIX,
    CDA_START,
    IMG_TAG,
    INCENTIVE_CONTEXT,
    PART_III_ITEM11_END,
    PART_III_ITEM11_START,
    PROXY_ALIASES,
    PROXY_EXCLUDE_HEADING,
    PROXY_EXCLUDE_SENTENCE,
    WEIGHT_HEADER,
    WEIGHT_PERCENT,
)
from veritas.models import DocRef, Finding, ModuleResult

ROIC_FCF_NOTE = "ROIC/FCF not identified among incentive metrics"
NON_GAAP = ("ADJ_EBITDA", "NONGAAP_EPS")
GROWTH_METRICS = ("REVENUE", "M_AND_A")

# footnotes/proxy: structural helpers (not policy thresholds)
_HEADING_MAX_WORDS = 15
_FALLBACK_HEADING_MAX_WORDS = 8
_SUBHEAD_MAX_WORDS = 12
_HEADER_ROWS_SCANNED = 3
_MIN_CDA_WORDS = 1


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", pos)
    return a, (len(text) if b < 0 else b)


def _heading_starts(
    text: str, rx: re.Pattern[str], max_words: int, self_reference: bool = False
) -> list[int]:
    plain: list[int] = []
    rows: list[int] = []
    for m in rx.finditer(text):
        a, b = _line_bounds(text, m.start())
        line = text[a:b]
        if len(line.split()) <= max_words or (
            self_reference and CDA_SELF_REFERENCE_PREFIX.match(text[a : m.start()])
        ):
            # Table rows ("CD&A | Compensation ...", TOC page numbers) only count when no
            # plain heading line exists.
            (rows if " | " in line else plain).append(a)
    return sorted(set(plain or rows))


def _span_from(starts: list[int], ends: list[int], size: int) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    for s in starts:
        nxt = next((e for e in ends if e > s), size)
        if best is None or nxt - s > best[1] - best[0]:
            best = (s, nxt)
    return best


def cda_span(text: str) -> tuple[int, int, str] | None:
    """Longest-span CD&A (§7.4.2) with the executive-compensation fallback.

    Matches are restricted to heading-like lines (<= 15 words) so that in-sentence
    cross-references do not stretch the span; if no heading-like match exists, any match
    is used. Returns (start, end, how) with how in {"cda", "exec_comp"}.
    """
    ends = _heading_starts(text, CDA_END, _HEADING_MAX_WORDS) or sorted(
        {_line_bounds(text, m.start())[0] for m in CDA_END.finditer(text)}
    )
    starts = _heading_starts(text, CDA_START, _HEADING_MAX_WORDS, self_reference=True)
    if starts:
        span = _span_from(starts, ends, len(text))
        if span is not None and len(text[span[0] : span[1]].split()) > _MIN_CDA_WORDS:
            return span[0], span[1], "cda"
    starts = _heading_starts(text, CDA_FALLBACK_START, _FALLBACK_HEADING_MAX_WORDS)
    if starts:
        span = _span_from(starts, ends, len(text))
        if span is not None:
            return span[0], span[1], "exec_comp"
    return None


def _is_heading(line: str) -> bool:
    words = line.split()
    if not words or len(words) > _SUBHEAD_MAX_WORDS or " | " in line:
        return False
    if line.rstrip().endswith((".", ";", ",")):
        return False
    if not any(ch.isalpha() for ch in line):
        return False
    return not any(rx.search(line) for rx in PROXY_ALIASES.values())


def _table_blocks(lines: list[str]) -> dict[int, list[str]]:
    """Map each table-row line index to the header rows (first rows) of its table."""
    out: dict[int, list[str]] = {}
    i = 0
    while i < len(lines):
        if " | " not in lines[i]:
            i += 1
            continue
        j = i
        while j < len(lines) and " | " in lines[j]:
            j += 1
        header = lines[i : min(j, i + _HEADER_ROWS_SCANNED)]
        for k in range(i, j):
            out[k] = header
        i = j
    return out


def _parse_weight(row: str, header_rows: list[str]) -> float | None:
    """Weight from the row only when the table header contains 'weight' (§7.4.3)."""
    hdr = next((h for h in header_rows if WEIGHT_HEADER.search(h)), None)
    if hdr is None:
        return None
    cells = row.split(" | ")
    hcells = hdr.split(" | ")
    idx = next((i for i, c in enumerate(hcells) if WEIGHT_HEADER.search(c)), None)
    if idx is not None and len(cells) == len(hcells):
        m = WEIGHT_PERCENT.search(cells[idx])
        if m and int(m.group(1)) <= 100:
            return float(m.group(1))
    found = {int(m.group(1)) for m in WEIGHT_PERCENT.finditer(row) if int(m.group(1)) <= 100}
    return float(next(iter(found))) if len(found) == 1 else None


def detect_metrics(cda_text: str) -> list[dict[str, Any]]:
    """Metric hits in the CD&A text; each carries its sentence/row span in `cda_text`."""
    lines = cda_text.split("\n")
    tables = _table_blocks(lines)
    hits: list[dict[str, Any]] = []
    excluded = False
    offset = 0
    for li, line in enumerate(lines):
        base = offset
        offset += len(line) + 1
        if not line.strip():
            continue
        is_row = li in tables
        if not is_row and _is_heading(line):
            excluded = bool(PROXY_EXCLUDE_HEADING.search(line))
            continue
        if excluded:
            continue
        if is_row:
            lead = len(line) - len(line.lstrip())
            units = [(base + lead, base + len(line.rstrip()), True)]
        else:
            units = [(base + sp.start, base + sp.end, False) for sp in split_sentences(line)]
        for a, b, row in units:
            unit = cda_text[a:b]
            if PROXY_EXCLUDE_SENTENCE.search(unit):
                continue
            for name, rx in PROXY_ALIASES.items():
                if not rx.search(unit):
                    continue
                ctx = INCENTIVE_CONTEXT.search(unit)
                if ctx is None and row:
                    for h in tables[li]:
                        ctx = INCENTIVE_CONTEXT.search(h)
                        if ctx:
                            break
                if ctx is None:
                    continue
                weight = _parse_weight(unit, tables[li]) if row else None
                hits.append(
                    {
                        "metric": name,
                        "context": ctx.group(0),
                        "weight": weight,
                        "sentence": unit,
                        "start": a,
                        "end": b,
                    }
                )
    hits.sort(key=lambda h: (h["start"], h["metric"]))
    return hits


def _result(
    status: Any,
    findings: tuple[Finding, ...],
    reason: str | None,
    extra: dict[str, Any],
) -> ModuleResult:
    return ModuleResult(status, findings, (), reason, extra)


def _empty_extra(source: str | None) -> dict[str, Any]:
    return {"source": source, "metrics": [], "notes": [], "cda_text": "", "checks": {}}


def _pick_source(
    proxy: tuple[DocRef, str] | None, item11: tuple[DocRef, str] | None
) -> tuple[DocRef, str, str, str, str] | None:
    """Return (ref, cda_text, source_label, how, raw_html) or None (§7.4.1-2)."""
    if proxy is not None:
        text = html_to_text(proxy[1])
        span = cda_span(text)
        if span is not None:
            return proxy[0], text[span[0] : span[1]], "DEF 14A CD&A", span[2], proxy[1]
    if item11 is not None:
        text = html_to_text(item11[1])
        sec = longest_span(text, PART_III_ITEM11_START, PART_III_ITEM11_END, require_end=True)
        if sec is not None:
            section = text[sec[0] : sec[1]]
            if len(section.split()) >= ti("PROXY_ITEM11_MIN_WORDS"):
                span = cda_span(section)
                body = section if span is None else section[span[0] : span[1]]
                how = "item11" if span is None else span[2]
                return item11[0], body, "10-K Item 11", how, item11[1]
    return None


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def run_proxy(
    proxy: tuple[DocRef, str] | None,
    item11: tuple[DocRef, str] | None,
    sbc_ttm: float | None,
    revenue_ttm: float | None,
    fcf_ttm: float | None,
    goodwill_assets: float | None,
    goodwill_assets_prior: float | None,
    acquisition_guard: bool,
) -> ModuleResult:
    picked = _pick_source(proxy, item11)
    if picked is None:
        reason = (
            "No DEF 14A with a locatable CD&A, and Item 11 has fewer than "
            f"{ti('PROXY_ITEM11_MIN_WORDS'):,} words"
            if proxy is not None or item11 is not None
            else "No DEF 14A or Item 11 text available"
        )
        return _result("unavailable", (), reason, _empty_extra(None))
    ref, cda_text, label, how, raw_html = picked
    hits = detect_metrics(cda_text)
    metrics: list[dict[str, Any]] = []
    for h in hits:
        cite = ref.cite(label, PARSER_VERSION, h["sentence"], h["start"], h["end"])
        metrics.append(
            {
                "metric": h["metric"],
                "context": h["context"],
                "weight": h["weight"],
                "sentence": h["sentence"],
                "citation": cite.to_dict(),
            }
        )
    distinct = sorted({h["metric"] for h in hits})
    extra: dict[str, Any] = {
        "source": label,
        "metrics": metrics,
        "notes": [],
        "cda_text": cda_text,
        "cda_how": how,
        "checks": {},
    }
    if len(distinct) < ti("PROXY_MIN_METRICS"):
        images = len(IMG_TAG.findall(raw_html))
        why = (
            f"Fewer than {ti('PROXY_MIN_METRICS')} incentive metrics could be detected in the text"
        )
        if images >= ti("PROXY_IMG_LOWCONF"):
            why += "; the document contains images that may hold the metric tables"
        extra["notes"] = [why + ". Extracted text is shown; no flags were raised."]
        return _result("low_confidence", (), why, extra)

    findings: list[Finding] = []
    first: dict[str, dict[str, Any]] = {}
    for h in hits:
        first.setdefault(h["metric"], h)

    def cite_for(metric_names: tuple[str, ...]) -> Any:
        h = next(first[m] for m in metric_names if m in first)
        return ref.cite(label, PARSER_VERSION, h["sentence"], h["start"], h["end"])

    # C1: SBC vs non-GAAP pay metric
    c1: dict[str, Any] = {"state": "NO_FLAG", "reason": None, "reasons": []}
    if not any(m in first for m in NON_GAAP):
        c1 = {"state": "NOT_APPLICABLE", "reason": "No non-GAAP incentive metric detected"}
    elif sbc_ttm is None or (revenue_ttm is None and fcf_ttm is None):
        c1 = {"state": "NOT_COMPUTABLE", "reason": "SBC, revenue or free cash flow unavailable"}
    else:
        reasons: list[str] = []
        if revenue_ttm is not None and revenue_ttm > 0:
            ratio = sbc_ttm / revenue_ttm
            if ratio >= t("C1_SBC_REV"):
                reasons.append(f"SBC is {_fmt_pct(ratio)} of revenue (TTM)")
        if fcf_ttm is not None and fcf_ttm > 0:
            if sbc_ttm >= t("C1_SBC_FCF") * fcf_ttm:
                reasons.append(f"SBC is {_fmt_pct(sbc_ttm / fcf_ttm)} of free cash flow (TTM)")
            if fcf_ttm - sbc_ttm < 0:
                reasons.append("Free cash flow less SBC is negative (TTM)")
        if reasons:
            c1 = {"state": "FIRED", "reason": None, "reasons": reasons}
            findings.append(
                Finding(
                    id="C1",
                    module="C",
                    category="incentives",
                    severity="WARNING",
                    title="SBC vs non-GAAP pay metric",
                    explanation=(
                        "A non-GAAP measure is used in incentive pay while "
                        + "; ".join(reasons)
                        + ". Flagged for review."
                    ),
                    check=None,
                    claims=(),
                    contradiction=False,
                    citations=(cite_for(NON_GAAP),),
                )
            )
    extra["checks"]["C1"] = c1

    # C2: acquisition-heavy goodwill
    c2: dict[str, Any] = {"state": "NO_FLAG", "reason": None, "reasons": []}
    if not any(m in first for m in GROWTH_METRICS):
        c2 = {"state": "NOT_APPLICABLE", "reason": "No revenue or M&A incentive metric detected"}
    elif goodwill_assets is None:
        c2 = {"state": "NOT_COMPUTABLE", "reason": "Goodwill/assets unavailable"}
    elif goodwill_assets >= t("C2_GW_ASSETS"):
        reasons = []
        if goodwill_assets_prior is not None:
            delta = goodwill_assets - goodwill_assets_prior
            if delta >= t("C2_GW_PP"):
                reasons.append(f"goodwill/assets rose {delta * 100:.1f} pp year over year")
        if acquisition_guard:
            reasons.append("the acquisition guard is tripped")
        if reasons:
            c2 = {"state": "FIRED", "reason": None, "reasons": reasons}
            findings.append(
                Finding(
                    id="C2",
                    module="C",
                    category="incentives",
                    severity="WARNING",
                    title="Acquisition-heavy goodwill with growth-linked pay",
                    explanation=(
                        f"A revenue or M&A measure is used in incentive pay while goodwill is "
                        f"{_fmt_pct(goodwill_assets)} of assets and "
                        + " and ".join(reasons)
                        + ". Flagged for review."
                    ),
                    check=None,
                    claims=(),
                    contradiction=False,
                    citations=(cite_for(GROWTH_METRICS),),
                )
            )
    extra["checks"]["C2"] = c2

    # Unscored neutral note (never claims zero weighting)
    others = [m for m in distinct if m not in ("ROIC", "FCF")]
    if (
        len(others) >= ti("PROXY_MIN_METRICS")
        and not PROXY_ALIASES["ROIC"].search(cda_text)
        and not PROXY_ALIASES["FCF"].search(cda_text)
    ):
        extra["notes"].append(ROIC_FCF_NOTE)
    return _result("ok", tuple(findings), None, extra)
