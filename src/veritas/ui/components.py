"""Pure HTML/SVG builders for the VERITAS UI.

Every function returns a markup string and has no Streamlit dependency, so it
can be unit-tested directly. Rules:

* Every string that could come from a filing (quotes, titles, explanations,
  note text, tags, company names, reasons) goes through `esc()`.
* Only SEC Archives URLs become links (`safe_url`).
* Output is a single line (see `one_line`) because Streamlit renders it through
  st.markdown(unsafe_allow_html=True), where a blank line would end the HTML
  block and indentation could turn it into a code block.
"""

from __future__ import annotations

import difflib
import html
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

MODULE_NAMES: dict[str, str] = {
    "A": "Narrative vs Numbers",
    "B": "Footnote Redline",
    "C": "Proxy X-Ray",
    "D": "Consistency Score",
}
MODULE_VIEWS: dict[str, str] = {
    "A": "[1] Overview · [3] Ratios",
    "B": "[2] Redline",
    "C": "[4] Proxy",
    "D": "[1] Overview",
}
ASSESSED_STATUSES = ("ok", "partial")
STATUS_TEXT: dict[str, str] = {
    "ok": "assessed",
    "partial": "partially assessed",
    "unavailable": "not available",
    "low_confidence": "low confidence",
    "timeout": "timed out",
    "error": "error",
}

_ALLOWED_URL = re.compile(r"^https://(www|data)\.sec\.gov/[^\s\"'<>]*$")
_NEWLINES = re.compile(r"\s*\n\s*")


def esc(value: Any) -> str:
    """HTML-escape any value for safe interpolation into markup.

    `$` is also encoded because Streamlit's Markdown renderer treats `$…$` as
    LaTeX; filing text is full of dollar amounts.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=True).replace("$", "&#36;")


def one_line(markup: str) -> str:
    """Collapse newlines so st.markdown keeps the markup as one raw HTML block."""
    return _NEWLINES.sub(" ", markup).strip()


def safe_url(url: Any) -> str | None:
    """Return an escaped URL only for SEC hosts; anything else is not linked."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not _ALLOWED_URL.match(u):
        return None
    return esc(u)


def get(obj: Any, key: str, default: Any = None) -> Any:
    """Read `key` from a dataclass instance or a mapping, defensively."""
    if obj is None:
        return default
    value = obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)
    return default if value is None else value


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value[:16].replace("T", " ")
    return "—"


def fmt_num(value: Any) -> str:
    """Format a number for display. Missing stays visibly missing (never 0)."""
    if value is None or isinstance(value, bool):
        return "—"
    if not isinstance(value, (int, float)):
        return esc(value)
    v = float(value)
    if math.isnan(v) or math.isinf(v):
        return "n/m"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 100:
        return f"{v:,.1f}".rstrip("0").rstrip(".")
    if a >= 1:
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if a == 0:
        return "0"
    return f"{v:.3f}"


def sev_class(sev: Any) -> str:
    return {"CRITICAL": "critical", "WARNING": "warning", "NO_FLAG": "noflag"}.get(str(sev), "na")


def sev_label(sev: Any) -> str:
    """Exact PRD labels: CRITICAL, WARNING, NO FLAG, N/A."""
    return {"CRITICAL": "CRITICAL", "WARNING": "WARNING", "NO_FLAG": "NO FLAG"}.get(str(sev), "N/A")


def badge(sev: Any, label: str | None = None) -> str:
    text = label if label is not None else sev_label(sev)
    return f'<span class="vx-badge {sev_class(sev)}">{esc(text)}</span>'


def tag(text: Any) -> str:
    return f'<span class="vx-tag">{esc(text)}</span>'


def label(text: str) -> str:
    return f'<div class="vx-label">{esc(text)}</div>'


def citation_link(cit: Any, text: str | None = None) -> str:
    """A compact citation pill: form · accession · section, linked to EDGAR."""
    if cit is None:
        return ""
    form = get(cit, "form", "")
    acc = get(cit, "accession", "")
    section = get(cit, "section", "")
    xbrl = get(cit, "xbrl_tag")
    parts = [p for p in (form, acc, section if section != form else "") if p]
    if xbrl and section == "XBRL":
        parts.append(str(xbrl))
    shown = text or " · ".join(str(p) for p in parts) or "source"
    url = safe_url(get(cit, "url"))
    title = esc(" · ".join(str(p) for p in parts))
    if url:
        return (
            f'<a class="vx-cite" href="{url}" target="_blank" rel="noopener noreferrer" '
            f'title="{title}">{esc(shown)} ↗</a>'
        )
    return f'<span class="vx-cite" title="{title}">{esc(shown)}</span>'


def empty_state(title: str, body: str, glyph: str = "∅", kind: str = "") -> str:
    cls = f"vx-empty {kind}".strip()
    return one_line(
        f'<div class="vx {cls}"><div class="vx-empty-glyph">{esc(glyph)}</div>'
        f"<div><div class='vx-h4'>{esc(title)}</div><div class='vx-p'>{esc(body)}</div></div></div>"
    )


def not_assessed(module_key: str, reason: Any, status: Any = None) -> str:
    """Designed grey panel: 'Not assessed: {reason}' (PRD §2.3)."""
    name = MODULE_NAMES.get(module_key, module_key)
    status_txt = STATUS_TEXT.get(str(status), str(status or "not assessed"))
    why = str(reason) if reason else status_txt
    body = (
        f"Module {module_key} · {name} · status: {status_txt}. "
        "It deducts nothing from the score and is listed as unassessed."
    )
    return one_line(
        f'<div class="vx vx-empty is-na"><div class="vx-empty-glyph">{esc(module_key)}</div>'
        f"<div><div class='vx-h4'>Not assessed: {esc(why)}</div>"
        f"<div class='vx-p'>{esc(body)}</div></div></div>"
    )


def section_header(title: str, count: int | None, desc: str) -> str:
    cnt = f'<span class="vx-count">{count}</span>' if count is not None else ""
    return one_line(
        f'<div class="vx"><div class="vx-section"><div class="vx-h2">{esc(title)}</div>{cnt}</div>'
        f'<div class="vx-section-desc">{esc(desc)}</div></div>'
    )


def banner(text: str, kind: str = "notice", title: str = "Notice", raw_html: bool = False) -> str:
    body = text if raw_html else esc(text)
    return one_line(
        f'<div class="vx vx-banner is-{esc(kind)}"><span class="vx-label">{esc(title)}</span>'
        f"<div>{body}</div></div>"
    )


# ---------------------------------------------------------------------------
# Header / landing
# ---------------------------------------------------------------------------


def topbar(chips: Sequence[tuple[str, str]]) -> str:
    """Brand row. `chips` are (text, css-modifier) pairs; text is app-defined."""
    chip_html = "".join(
        f'<span class="vx-chip {esc(mod)}"><span class="vx-dot"></span>{esc(text)}</span>'
        for text, mod in chips
    )
    return one_line(
        '<div class="vx vx-topbar"><div class="vx-brand">'
        '<span class="vx-brand-mark">VERITAS<i>.</i></span>'
        '<span class="vx-brand-sub">Narrative–Numbers screening terminal · SEC EDGAR</span>'
        f'</div><div class="vx-topbar-right">{chip_html}</div></div>'
    )


def hero() -> str:
    hints = "".join(tag(h) for h in ("NVDA", "CIK 320193", "DIFF", "CONTRA", "EXPORT"))
    return one_line(
        '<div class="vx vx-hero">'
        '<div class="vx-label vx-hero-kicker">Forensic screening · public SEC filings</div>'
        '<div class="vx-wordmark">VERITAS<span>.</span></div>'
        '<div class="vx-tagline"><em>What management said.</em> <b>What the numbers show.</b></div>'
        '<div class="vx-lede">VERITAS reads a company\'s MD&amp;A, footnotes and proxy, lines '
        "each claim up against the XBRL figures for the same period, and cites every quote "
        "and number back to the filing it came from.</div>"
        f'<div class="vx-hints"><span class="vx-label" style="padding-top:4px">Try</span>{hints}'
        "</div></div>"
    )


SEED_META: dict[str, tuple[str, str]] = {
    "NVDA": ("NVIDIA", "Semiconductors"),
    "WMT": ("Walmart", "Retail"),
    "TSLA": ("Tesla", "Autos & energy"),
    "BA": ("Boeing", "Aerospace"),
}


def seed_meta(ticker: str) -> str:
    name, sector = SEED_META.get(ticker, (ticker, ""))
    return one_line(f'<div class="vx seed-meta"><b>{esc(name)}</b><span>{esc(sector)}</span></div>')


def how_it_works() -> str:
    items = [
        (
            "A",
            "Narrative vs Numbers",
            "Five accounting screens from XBRL, each paired with what management said "
            "about the same quarter.",
        ),
        (
            "B",
            "Footnote Redline",
            "Year-over-year redline of the financial-statement notes, with softened "
            "language and hedging drift.",
        ),
        (
            "C",
            "Proxy X-Ray",
            "Which metrics drive executive pay, checked against SBC, free cash flow and "
            "goodwill trends.",
        ),
        (
            "D",
            "Consistency Score",
            "A capped, coverage-aware 0–100 screening score. Missing data is listed, "
            "never rewarded.",
        ),
    ]
    cells = "".join(
        f'<div class="vx-module" style="--i:{i}"><div class="vx-module-letter">{k}</div>'
        f"<div class='vx-h4'>{esc(t)}</div>"
        f"<div class='vx-p'>{esc(d)}</div>{tag(MODULE_VIEWS[k])}</div>"
        for i, (k, t, d) in enumerate(items)
    )
    return one_line(
        '<div class="vx"><div class="vx-section-kicker"><span class="vx-label">How it works'
        f'</span></div><div class="vx-modules">{cells}</div></div>'
    )


def source_chip(source: Any, as_of: Any) -> tuple[str, str]:
    src = str(source or "").lower()
    when = fmt_date(as_of)
    if src == "live":
        return (f"LIVE · {when}", "is-accent")
    if src == "cache":
        return (f"CACHE · {when}", "")
    if src == "seed":
        return (f"SEED as of {when}", "")
    return (f"SOURCE UNKNOWN · {when}", "")


def identity(result: Any, synthetic: bool) -> str:
    name = get(result, "company_name", "Unknown issuer")
    facts = [
        ("Ticker", get(result, "ticker", "—")),
        ("CIK", get(result, "cik", "—")),
        ("SIC", get(result, "sic", "—")),
        ("FY end", get(result, "fiscal_year_end", "—")),
        ("Parser", get(result, "parser_version", "—")),
    ]
    fact_html = "".join(
        f'<div class="vx-fact"><span class="vx-label">{esc(k)}</span><b>{esc(v)}</b></div>'
        for k, v in facts
    )
    filings = []
    for cit in as_list(get(result, "filings"))[:6]:
        form = get(cit, "form", "")
        fdate = fmt_date(get(cit, "filing_date"))
        url = safe_url(get(cit, "url"))
        inner = f"<b>{esc(form)}</b>{esc(fdate)}"
        if url:
            filings.append(
                f'<a class="vx-filing" href="{url}" target="_blank" rel="noopener noreferrer" '
                f'title="{esc(get(cit, "accession", ""))}">{inner}</a>'
            )
        else:
            filings.append(f'<span class="vx-filing">{inner}</span>')
    kicker = "Synthetic sample · not a real issuer" if synthetic else "Issuer under review"
    return one_line(
        '<div class="vx vx-identity"><div>'
        f'<div class="vx-label">{esc(kicker)}</div><div class="vx-h1">{esc(name)}</div>'
        f'<div class="vx-facts">{fact_html}</div></div>'
        f'<div><div class="vx-label" style="text-align:right;margin-bottom:8px">Filings used'
        f'</div><div class="vx-filings">{"".join(filings) or tag("none listed")}</div></div>'
        "</div>"
    )


def command_error(message: str, valid: Iterable[str]) -> str:
    chips = "".join(tag(v) for v in valid)
    return one_line(
        f'<div class="vx vx-cmd-error"><strong>{esc(message)}</strong>'
        f'<div class="vx-cmd-list">{chips}</div></div>'
    )


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def progress_panel(stages: Sequence[str], active: int, detail: str = "") -> str:
    """Staged progress (PRD §2.2). `active` = index of the running stage;
    len(stages) means everything finished."""
    n = max(len(stages), 1)
    pct = min(100.0, max(4.0, (active + 0.5) / n * 100 if active < n else 100.0))
    cells = []
    for i, s in enumerate(stages):
        cls = "is-done" if i < active else ("is-active" if i == active else "")
        cells.append(f'<div class="vx-stage {cls}"><i></i>{i + 1:02d} {esc(s)}</div>')
    det = f'<div class="vx-progress-detail">› {esc(detail)}</div>' if detail else ""
    return one_line(
        f'<div class="vx vx-progress"><div class="vx-progress-bar"><i style="--w:{pct:.0f}%">'
        f'</i></div><div class="vx-stages">{"".join(cells)}</div>{det}</div>'
    )


# ---------------------------------------------------------------------------
# Overview: score + module chips
# ---------------------------------------------------------------------------

RING_R = 80.0
RING_LEN = 2 * math.pi * RING_R


def score_band(score: int | None) -> str:
    """Display band for the ring colour (documented in docs/decisions/ui.md)."""
    if score is None:
        return "na"
    if score >= 85:
        return "noflag"
    if score >= 60:
        return "warning"
    return "critical"


def score_ring(score: int | None) -> str:
    band = score_band(score)
    colour = {
        "noflag": "var(--sev-noflag)",
        "warning": "var(--sev-warning)",
        "critical": "var(--sev-critical)",
        "na": "var(--sev-na)",
    }[band]
    glow = {
        "noflag": "rgba(0,230,118,0.45)",
        "warning": "rgba(255,179,0,0.45)",
        "critical": "rgba(255,59,48,0.45)",
        "na": "transparent",
    }[band]
    track = (
        f'<circle class="vx-ring-track" cx="100" cy="100" r="{RING_R:.0f}" fill="none" '
        'stroke-width="12"/>'
        f'<circle class="vx-ring-ticks" cx="100" cy="100" r="{RING_R + 14:.0f}" fill="none" '
        'stroke-width="5"/>'
    )
    if score is None:
        return one_line(
            '<div class="vx-ring is-insufficient"><svg viewBox="0 0 200 200" aria-hidden="true">'
            f"{track}</svg>"
            '<div class="vx-ring-center"><div class="vx-score-insufficient">INSUFFICIENT<br>DATA'
            "</div></div></div>"
        )
    s = max(0, min(100, int(score)))
    off = RING_LEN * (1 - s / 100)
    arc = (
        f'<circle class="vx-ring-arc" cx="100" cy="100" r="{RING_R:.0f}" fill="none" '
        f'stroke="{colour}" stroke-width="12" '
        f'style="--len:{RING_LEN:.2f};--off:{off:.2f};--glow:{glow}"/>'
    )
    # Typed custom property so the number can count up in pure CSS; browsers
    # without @property just show the final value.
    prop = (
        '<style>@property --n { syntax: "<integer>"; initial-value: 0; inherits: false; }</style>'
    )
    return one_line(
        f'{prop}<div class="vx-ring"><svg viewBox="0 0 200 200" aria-hidden="true">'
        f"{track}{arc}</svg>"
        f'<div class="vx-ring-center"><div class="vx-score-num" style="--n:{s}">'
        f'<span class="vx-sr">{s}</span></div><div class="vx-score-of">/ 100</div></div></div>'
    )


def score_panel(
    score_obj: Any,
    score_name: str,
    caps: Mapping[str, int],
    module_statuses: Mapping[str, Any],
) -> str:
    score = get(score_obj, "score")
    num = get(score_obj, "coverage_num")
    den = get(score_obj, "coverage_den")
    k = get(score_obj, "modules_assessed")
    if not k:
        k = sum(1 for st in module_statuses.values() if str(st) in ASSESSED_STATUSES)
    unassessed = [str(u) for u in as_list(get(score_obj, "unassessed"))]
    if not unassessed:
        unassessed = [
            f"{key} · {MODULE_NAMES.get(key, key)}"
            for key, st in module_statuses.items()
            if str(st) not in ASSESSED_STATUSES
        ]
    coverage = f"{num}/{den}" if den else "—"
    meta = (
        f'<div class="vx-fact"><span class="vx-label">Coverage</span><b>{esc(coverage)}</b></div>'
        f'<div class="vx-fact"><span class="vx-label">Modules</span><b>{esc(k)} of 3 modules '
        "assessed</b></div>"
        f'<div class="vx-fact"><span class="vx-label">Label</span>'
        f"<b>{esc(get(score_obj, 'label', '—'))}</b></div>"
    )
    notes = []
    if score is None:
        notes.append(
            "<b>INSUFFICIENT DATA.</b> Fewer than 60% of the scored accounting checks could be "
            "computed, so no score is shown. Missing data is never rewarded."
        )
    scope = get(score_obj, "scope_note")
    if scope:
        notes.append(f"<b>Scope.</b> {esc(scope)}")
    if unassessed:
        notes.append(f"<b>Unassessed.</b> {esc(', '.join(unassessed))}")
    notes.append("Uncalibrated screening score; not comparable across sectors.")
    note_html = "".join(f'<div class="vx-score-note">{n}</div>' for n in notes)

    rows = []
    deductions = as_list(get(score_obj, "deductions"))
    for item in deductions:
        try:
            cat, pts = item[0], item[1]
        except (TypeError, IndexError, KeyError):
            continue
        cap = caps.get(str(cat)) or 0
        w = 0.0 if not cap else min(100.0, float(pts) / cap * 100)
        rows.append(
            f'<div class="vx-ded-row"><span>{esc(cat)}</span><div class="vx-ded-bar">'
            f'<i style="--w:{w:.0f}%"></i></div><span>−{esc(pts)}'
            f'<span style="color:var(--text-3)">/{esc(cap or "?")}</span></span></div>'
        )
    if rows:
        ded = (
            '<div class="vx-label" style="margin-bottom:10px">Deductions · points / cap</div>'
            f'<div class="vx-deductions">{"".join(rows)}</div>'
        )
    else:
        ded = (
            '<div class="vx-label" style="margin-bottom:10px">Deductions</div>'
            '<div class="vx-score-note">No deductions recorded.</div>'
        )
    return one_line(
        f'<div class="vx vx-panel vx-score">{score_ring(score)}'
        f'<div><div class="vx-label">Module D</div><div class="vx-score-title">'
        f"{esc(score_name)}</div>"
        f'<div class="vx-score-meta">{meta}</div>{note_html}</div><div>{ded}</div></div>'
    )


def module_chips(module_statuses: Mapping[str, Any], score_assessed: bool) -> str:
    chips = []
    for i, key in enumerate(("A", "B", "C")):
        st = module_statuses.get(key)
        st_s = str(st) if st is not None else "unavailable"
        off = st_s not in ASSESSED_STATUSES
        chips.append(
            f'<div class="vx-modchip {"is-off" if off else ""}" style="--i:{i}">'
            f'<div class="vx-module-letter">{key}</div><div>'
            f'<div class="vx-modchip-name">{esc(MODULE_NAMES[key])}</div>'
            f'<div class="vx-modchip-status">{esc(STATUS_TEXT.get(st_s, st_s))}</div></div></div>'
        )
    chips.append(
        f'<div class="vx-modchip {"" if score_assessed else "is-off"}" style="--i:3">'
        '<div class="vx-module-letter">D</div><div>'
        f'<div class="vx-modchip-name">{esc(MODULE_NAMES["D"])}</div>'
        f'<div class="vx-modchip-status">{"scored" if score_assessed else "insufficient data"}'
        "</div></div></div>"
    )
    return one_line(f'<div class="vx vx-modrow">{"".join(chips)}</div>')


# ---------------------------------------------------------------------------
# Overview: finding card
# ---------------------------------------------------------------------------


def _kv(inputs: Mapping[str, Any], outputs: Mapping[str, Any]) -> str:
    rows = []
    for k, v in inputs.items():
        rows.append(
            f"<span class='vx-k' title='{esc(k)}'>{esc(k)}</span>"
            f"<span class='vx-v'>{fmt_num(v)}</span>"
        )
    if inputs and outputs:
        rows.append('<div class="vx-kv-sep"></div>')
    for k, v in outputs.items():
        rows.append(
            f"<span class='vx-k' title='{esc(k)}'>{esc(k)}</span>"
            f"<span class='vx-v is-out'>{fmt_num(v)}</span>"
        )
    return f'<div class="vx-kv">{"".join(rows)}</div>' if rows else ""


def finding_card(finding: Any, index: int = 0) -> str:
    sev = get(finding, "severity")
    cls = sev_class(sev)
    contra = bool(get(finding, "contradiction", False))
    claims = as_list(get(finding, "claims"))
    check = get(finding, "check")
    module = get(finding, "module", "?")

    # Column 1: what management said
    said_parts = []
    for claim in claims[:2]:
        cit = get(claim, "citation")
        quote = get(cit, "quote_raw")
        if not quote:
            continue
        said_parts.append(
            f'<div class="vx-quote">{esc(quote)}</div>'
            f'<div class="vx-cites" style="margin-bottom:10px">'
            f"{tag(get(claim, 'cluster', 'CLAIM'))}{citation_link(cit)}</div>"
        )
    if not said_parts:
        said_parts.append(
            '<div class="vx-empty-said">No management statement was paired with this item for '
            "the same period.</div>"
        )
    said = (
        '<div class="vx-col vx-col-said"><div class="vx-col-head">'
        '<span class="vx-label">What management said</span></div>'
        f"{''.join(said_parts)}</div>"
    )

    # Connector
    glyph = "≠" if contra else "→"
    connector = (
        f'<div class="vx-connector {"is-contra" if contra else ""}" '
        f'title="{"Statement and numbers diverge" if contra else "Paired"}"><b>{glyph}</b></div>'
    )

    # Column 2: what the numbers show
    if check is not None:
        formula = get(check, "formula")
        inputs = get(check, "inputs", {}) or {}
        outputs = get(check, "outputs", {}) or {}
        period = fmt_date(get(check, "period_end"))
        cits = as_list(get(check, "citations"))
        shows_body = (
            (f'<div class="vx-formula">{esc(formula)}</div>' if formula else "")
            + _kv(
                inputs if isinstance(inputs, Mapping) else {},
                outputs if isinstance(outputs, Mapping) else {},
            )
            + f'<div class="vx-period"><span class="vx-label">Period ending</span>'
            f"{tag(period)}{''.join(citation_link(c) for c in cits[:2])}</div>"
        )
        reason = get(check, "reason")
        if reason:
            shows_body += (
                f'<div class="vx-verdict-expl" style="margin-top:10px">{esc(reason)}</div>'
            )
    else:
        cits = [c for c in as_list(get(finding, "citations"))]
        shows_body = (
            f'<div class="vx-verdict-expl">{esc(get(finding, "explanation", ""))}</div>'
            f'<div class="vx-cites">{"".join(citation_link(c) for c in cits[:3])}</div>'
        )
    shows = (
        '<div class="vx-col vx-col-shows"><div class="vx-col-head">'
        '<span class="vx-label">What the numbers show</span>'
        f'<span class="vx-label">Module {esc(module)}</span></div>{shows_body}</div>'
    )

    # Column 3: verdict
    annots = as_list(get(check, "annotations")) if check is not None else []
    annot_html = f'<div class="vx-annots">{"".join(tag(a) for a in annots)}</div>' if annots else ""
    expl = get(finding, "explanation", "") if check is not None else ""
    verdict = (
        '<div class="vx-col"><div class="vx-col-head">'
        f'<span class="vx-label">Screen result</span>{badge(sev)}</div>'
        f'<div class="vx-verdict-title">{esc(get(finding, "title", "Untitled finding"))}</div>'
        + (f'<div class="vx-verdict-expl">{esc(expl)}</div>' if expl else "")
        + annot_html
        + "</div>"
    )
    return one_line(
        f'<div class="vx vx-card {cls}" style="--i:{min(index, 12)}">'
        f"{said}{connector}{shows}{verdict}</div>"
    )


# ---------------------------------------------------------------------------
# Redline
# ---------------------------------------------------------------------------


def word_diff(prior: str, current: str) -> tuple[str, str]:
    """Word-level redline of a MODIFIED pair. Each token is escaped first."""
    a, b = prior.split(), current.split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    left: list[str] = []
    right: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        pa = esc(" ".join(a[i1:i2]))
        pb = esc(" ".join(b[j1:j2]))
        if op == "equal":
            left.append(pa)
            right.append(pb)
        else:
            if pa:
                left.append(f"<del>{pa}</del>")
            if pb:
                right.append(f"<ins>{pb}</ins>")
    return " ".join(left), " ".join(right)


def filing_meta(doc: Any, role: str) -> str:
    form = get(doc, "form", "—")
    acc = get(doc, "accession", "—")
    fdate = fmt_date(get(doc, "filing_date"))
    rdate = fmt_date(get(doc, "report_date"))
    url = safe_url(get(doc, "url"))
    link = (
        f'<a class="vx-cite" href="{url}" target="_blank" rel="noopener noreferrer">'
        f"{esc(acc)} ↗</a>"
        if url
        else tag(acc)
    )
    return (
        f'<div class="vx-panel"><div class="vx-label">{esc(role)}</div>'
        f'<div style="display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap">'
        f'<span class="vx-mono" style="font-weight:600">{esc(form)}</span>'
        f'<span class="vx-mono" style="color:var(--text-3);font-size:12px">period {esc(rdate)}'
        f" · filed {esc(fdate)}</span>{link}</div></div>"
    )


def pair_meta(prior: Any, current: Any) -> str:
    return one_line(
        f'<div class="vx vx-pairmeta">{filing_meta(prior, "Prior")}'
        f'<div class="vx-pairmeta-arrow">→</div>{filing_meta(current, "Current")}</div>'
    )


def redline_counts(counts: Mapping[str, Any]) -> str:
    spec = [
        ("inserted", "Inserted", "is-ins"),
        ("deleted", "Deleted", "is-del"),
        ("modified", "Modified", ""),
        ("softened", "Softened", ""),
        ("material", "Material", "is-mat"),
    ]
    cells = "".join(
        f'<div class="vx-stat {mod}" style="--i:{i}"><span class="vx-label">{esc(lbl)}</span>'
        f"<b>{esc(counts.get(key, '—'))}</b></div>"
        for i, (key, lbl, mod) in enumerate(spec)
    )
    return one_line(f'<div class="vx vx-stats">{cells}</div>')


def redline_row(row: Mapping[str, Any], index: int, prior_label: str, current_label: str) -> str:
    kind = str(row.get("kind") or "").upper()
    prior = row.get("prior")
    current = row.get("current")
    absent = '<div class="vx-diffcell is-absent">— not present —</div>'
    if kind == "MODIFIED" and isinstance(prior, str) and isinstance(current, str):
        left_html, right_html = word_diff(prior, current)
        left = f'<div class="vx-diffcell">{left_html}</div>'
        right = f'<div class="vx-diffcell">{right_html}</div>'
    else:
        left = f'<div class="vx-diffcell"><del>{esc(prior)}</del></div>' if prior else absent
        right = f'<div class="vx-diffcell"><ins>{esc(current)}</ins></div>' if current else absent
    material = bool(row.get("material"))
    softened = bool(row.get("softened"))
    meta = tag(kind or "CHANGE")
    if material:
        meta += badge("WARNING", "MATERIAL")
    if softened:
        meta += tag("modal softening")
    ratio = row.get("ratio")
    if isinstance(ratio, (int, float)):
        meta += tag(f"similarity {ratio:.2f}")
    return one_line(
        f'<div class="vx vx-diffrow {"is-material" if material else ""}" '
        f'style="--i:{min(index, 12)}">{left}{right}<div class="vx-diffmeta">{meta}</div></div>'
    )


def diff_head(prior_label: str, current_label: str) -> str:
    return one_line(
        f'<div class="vx vx-diffhead"><span class="vx-label">{esc(prior_label)}</span>'
        f'<span class="vx-label">{esc(current_label)}</span></div>'
    )


def hedging_panel(h: Mapping[str, Any]) -> str:
    status = str(h.get("status") or "UNKNOWN").upper()
    status_text = {
        "FIRED": "Hedging drift detected",
        "NO_DRIFT": "No significant drift",
        "INSUFFICIENT": "Insufficient text",
        "INSUFFICIENT_TEXT": "Insufficient text",
    }.get(status, status.replace("_", " ").title())
    sev = "WARNING" if status == "FIRED" else None
    pr, cr = h.get("prior_rate"), h.get("current_rate")
    rows = [
        ("Hedge hits per 1,000 tokens", pr, cr),
        ("Hedge hits", h.get("prior_hits"), h.get("current_hits")),
        ("Tokens (category notes)", h.get("prior_tokens"), h.get("current_tokens")),
    ]
    body = "".join(
        f"<tr><td>{esc(name)}</td><td class='num'>{fmt_num(a)}</td>"
        f"<td class='num'>{fmt_num(b)}</td></tr>"
        for name, a, b in rows
    )
    table = (
        '<table class="vx-table"><thead><tr><th>Measure</th><th class="num">Prior 10-K</th>'
        f'<th class="num">Current 10-K</th></tr></thead><tbody>{body}</tbody></table>'
    )
    bars = ""
    if isinstance(pr, (int, float)) and isinstance(cr, (int, float)) and max(pr, cr) > 0:
        top = max(pr, cr)
        bars = (
            '<div class="vx-hedge-bars">'
            f'<div class="vx-ded-row"><span>prior</span><div class="vx-ded-bar">'
            f'<i style="--w:{pr / top * 100:.0f}%"></i></div><span>{fmt_num(pr)}</span></div>'
            f'<div class="vx-ded-row"><span>current</span><div class="vx-ded-bar">'
            f'<i style="--w:{cr / top * 100:.0f}%"></i></div><span>{fmt_num(cr)}</span></div>'
            "</div>"
        )
    head = (
        '<div class="vx-checkhead"><div><div class="vx-label">Hedging drift · 10-K pair</div>'
        f"<div class='vx-h3'>{esc(status_text)}"
        f"</div></div>{badge(sev) if sev else tag(status)}</div>"
    )
    return one_line(
        f'<div class="vx vx-panel">{head}<div class="vx-hedge" style="margin-top:12px">'
        f"{table}{bars}</div></div>"
    )


# ---------------------------------------------------------------------------
# Ratios
# ---------------------------------------------------------------------------


def check_header(check: Any) -> str:
    state = str(get(check, "state", ""))
    sev = get(check, "severity") if state == "COMPUTED" else None
    scored = bool(get(check, "scored", False))
    right = badge(sev) if state == "COMPUTED" else badge(None, "N/A")
    state_txt = state.replace("_", " ").lower() or "unknown"
    formula = get(check, "formula")
    annots = "".join(tag(a) for a in as_list(get(check, "annotations")))
    return one_line(
        f'<div class="vx vx-checkhead"><div><div class="vx-label">{esc(get(check, "check_id"))} '
        f"· {'scored' if scored else 'display only'} · {esc(state_txt)} · period "
        f"{esc(fmt_date(get(check, 'period_end')))}</div>"
        f"<div class='vx-h3'>{esc(get(check, 'title', 'Untitled check'))}</div>"
        + (f'<div class="vx-formula">{esc(formula)}</div>' if formula else "")
        + f'</div><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">{annots}'
        f"{right}</div></div>"
    )


def display_metrics(checks: Sequence[Any]) -> str:
    tiles = []
    for i, c in enumerate(checks):
        outputs = get(c, "outputs", {}) or {}
        value: Any = None
        if isinstance(outputs, Mapping) and outputs:
            value = next(iter(outputs.values()))
        state = str(get(c, "state", ""))
        shown = fmt_num(value) if state == "COMPUTED" else "n/m"
        reason = get(c, "reason")
        annots = " · ".join(str(a) for a in as_list(get(c, "annotations")))
        sub = esc(get(c, "formula", ""))
        foot = esc(reason or annots or "display only · never scored")
        tiles.append(
            f'<div class="vx-metric-tile" style="--i:{i}"><div class="vx-label">'
            f"{esc(get(c, 'check_id'))}</div><b>{shown}</b>"
            f'<div style="font-size:14px;color:var(--text);margin-bottom:6px">'
            f"{esc(get(c, 'title', ''))}</div><div class='vx-p'>{sub}</div>"
            f'<div class="vx-p" style="margin-top:6px">{foot}</div></div>'
        )
    return one_line(f'<div class="vx vx-metrics">{"".join(tiles)}</div>')


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------


def proxy_table(metrics: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for m in metrics:
        if not isinstance(m, Mapping):
            continue
        weight = m.get("weight")
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            w = max(0.0, min(100.0, float(weight)))
            weight_html = (
                f'<div class="vx-weight"><div class="vx-ded-bar"><i style="--w:{w:.0f}%"></i>'
                f"</div><span>{fmt_num(weight)}%</span></div>"
            )
        else:
            weight_html = '<span class="vx-muted">weight not parsed</span>'
        cit = m.get("citation")
        rows.append(
            f'<tr><td><span class="vx-metric">{esc(m.get("metric") or "—")}</span></td>'
            f"<td>{esc(m.get('context') or '—')}</td><td>{weight_html}</td>"
            f'<td><div style="color:var(--text);margin-bottom:8px">{esc(m.get("sentence") or "")}'
            f"</div>{citation_link(cit)}</td></tr>"
        )
    return one_line(
        '<div class="vx vx-panel" style="padding:8px 10px"><table class="vx-table"><thead><tr>'
        "<th>Metric</th><th>Plan context</th><th>Weight</th><th>Source sentence</th></tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def notes_list(notes: Sequence[Any]) -> str:
    items = "".join(
        f'<div class="vx-banner is-notice"><span class="vx-label">Note</span><div>{esc(n)}'
        f'<div class="vx-label" style="margin-top:4px">unscored</div></div></div>'
        for n in notes
    )
    return one_line(f'<div class="vx">{items}</div>')


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


def footer(disclaimer: str, version: str) -> str:
    return one_line(
        '<div class="vx vx-footer"><span class="vx-brand-mark">VERITAS<i>.</i></span>'
        f"<div><div class='vx-p'>{esc(disclaimer)}</div>"
        f'<div style="margin-top:8px" class="vx-p vx-mono">v{esc(version)} · data: SEC EDGAR '
        "(www.sec.gov, data.sec.gov) · deterministic, cited, no LLM</div></div></div>"
    )


def flag_card(finding: Any, index: int = 0) -> str:
    """Compact flag panel (Proxy view): badge, title, explanation, citations."""
    sev = get(finding, "severity")
    cits = "".join(citation_link(c) for c in as_list(get(finding, "citations"))[:3])
    return one_line(
        f'<div class="vx vx-card {sev_class(sev)}" style="--i:{min(index, 12)};'
        'grid-template-columns:1fr"><div class="vx-col vx-col-said">'
        f'<div class="vx-col-head"><span class="vx-label">{esc(get(finding, "id", ""))} · '
        f"{esc(get(finding, 'category', ''))}</span>{badge(sev)}</div>"
        f'<div class="vx-verdict-title" style="margin-top:0">{esc(get(finding, "title", ""))}'
        f'</div><div class="vx-verdict-expl">{esc(get(finding, "explanation", ""))}</div>'
        f'<div class="vx-cites">{cits}</div></div></div>'
    )
