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


# ---------------------------------------------------------------------------
# Humanising: engine identifiers -> reader-facing labels. Formula blocks keep
# the raw symbols; tables, chips and tooltips use these labels.
# ---------------------------------------------------------------------------

_BASE_LABELS: dict[str, str] = {
    "ar": "Receivables",
    "accounts_receivable": "Receivables",
    "revenue": "Revenue",
    "rev": "Revenue",
    "cogs": "COGS",
    "inventory": "Inventory",
    "inv": "Inventory",
    "ap": "Payables",
    "ni": "Net income",
    "net_income": "Net income",
    "ocf": "Operating cash flow",
    "capex": "CapEx",
    "da": "D&A",
    "fcf": "Free cash flow",
    "sbc": "Stock-based comp",
    "assets": "Assets",
    "avg_assets": "Average assets",
    "goodwill": "Goodwill",
    "acquisitions": "Acquisitions",
    "acquisitions_pct": "Acquisitions % of",
    "capsw": "Capitalized software",
    "rd": "R&D",
    "r&d": "R&D",
    "days": "Days",
    "dso": "DSO",
    "dio": "DIO",
    "dpo": "DPO",
    "accrual_ratio": "Accrual ratio",
    "ccr": "Cash conversion (OCF/NI)",
    "caprate": "Capitalization rate",
    "rel": "Relative change",
    "count": "Quarters counted",
    "quarters_counted": "Quarters counted",
    "roic": "ROIC",
    "nopat": "NOPAT",
    "ic": "Invested capital",
    "invested_capital": "Invested capital",
    "cap_rate": "Capitalization rate",
    "capex_da": "CapEx / D&A",
    "operatingincome": "Operating income",
    "pretaxincome": "Pre-tax income",
    "rnd": "R&D",
    "tax": "Tax",
    "tax_rate": "Tax rate",
    "restructuring": "Restructuring",
    "fcf_sbc": "FCF after SBC",
    "material_quarters_last4": "Material quarters (last 4)",
    "material_quarters_last8": "Material quarters (last 8)",
    "restated_metric_count": "Restated metrics",
}
_SUFFIX_LABELS: tuple[tuple[str, str], ...] = (
    ("_change_pp", "change, pp"),
    ("_prior_year", "prior year"),
    ("_pct_prior_assets", "% of prior-year assets"),
    ("_rel_change", "relative change"),
    ("_growth", "growth"),
    ("_prior", "prior year"),
    ("_ttm", "TTM"),
    ("_end", "end"),
    ("_q", "quarter"),
    ("_days", "days"),
)
_TOKEN_TEXT: dict[str, str] = {
    "related_party": "related party",
    "commitments_contingencies": "commitments & contingencies",
    "NO_DRIFT": "no significant drift",
    "INSUFFICIENT_TEXT": "insufficient text",
    "accounting_prior_period": "prior period",
    "claims_consistent": "claims consistent",
    "footnotes_10q": "footnotes (10-Q)",
}
_REASON_TEXT: dict[str, str] = {
    "no inventory tag reported": "InventoryNet not tagged (issuer may use another concept)",
}


def human_key(key: Any) -> str:
    """'COGS_q' -> 'COGS (quarter)', 'Inventory_end_prior_year' -> 'Inventory (end, prior year)'."""
    raw = str(key)
    low = raw.lower()
    delta = False
    for pre in ("delta_", "d_"):
        if low.startswith(pre):
            delta, low = True, low[len(pre) :]
            break
    quals: list[str] = []
    changed = True
    while changed:
        changed = False
        for suf, text in _SUFFIX_LABELS:
            if low.endswith(suf) and len(low) > len(suf):
                quals.insert(0, text)
                low = low[: -len(suf)]
                changed = True
                break
    base = _BASE_LABELS.get(low)
    if base is None:
        base = low.replace("_", " ").strip()
        base = base[:1].upper() + base[1:] if base else raw
    label = f"Δ {base}" if delta else base
    return f"{label} ({', '.join(quals)})" if quals else label


def human_text(text: Any) -> str:
    """Tidy engine prose for display: arrows, snake-case identifiers, known
    engine reasons. Quoted words (e.g. a matched lexicon term) pass unchanged."""
    out = str(text or "").replace(" -> ", " → ").replace("->", "→")
    for k, v in _TOKEN_TEXT.items():
        out = out.replace(k, v)
    for k, v in _REASON_TEXT.items():
        out = re.sub(re.escape(k), v, out, flags=re.IGNORECASE)
    return out


def human_reason(reason: Any) -> str:
    return human_text(str(reason or "").strip())


CHECK_LABELS: dict[str, str] = {
    "A1_DSO": "A1 · Receivables (DSO)",
    "A2_DIO": "A2 · Inventory (DIO)",
    "A3_ACCRUALS": "A3 · Accruals / cash conversion",
    "A4_CAPRATE": "A4 · Capitalization intensity",
    "A5_RESTRUCT": "A5 · Recurring restructuring",
    "X_CAPEX_DA": "CapEx / D&A",
    "X_DPO": "Payables (DPO)",
    "X_FCF": "Free cash flow",
    "X_FCF_SBC": "FCF after SBC",
    "X_ROIC": "ROIC (approx.)",
    "X_RESTATED": "Restated values",
}
PRIMARY_KEY: dict[str, str] = {
    "A1_DSO": "dso",
    "A2_DIO": "dio",
    "A3_ACCRUALS": "accrual_ratio",
    "A4_CAPRATE": "cap_rate",
    "A5_RESTRUCT": "material_quarters_last4",
    "X_CAPEX_DA": "capex_da",
    "X_DPO": "dpo",
    "X_FCF": "fcf",
    "X_FCF_SBC": "fcf_sbc",
    "X_ROIC": "roic",
    "X_RESTATED": "restated_metric_count",
}


def check_label(check_id: Any) -> str:
    cid = str(check_id or "")
    return CHECK_LABELS.get(cid, cid.replace("_", " "))


def primary_value(check: Any) -> tuple[str, Any]:
    """(output key, value) of a check's headline metric."""
    outputs = get(check, "outputs", {}) or {}
    if not isinstance(outputs, Mapping) or not outputs:
        return ("", None)
    key = PRIMARY_KEY.get(str(get(check, "check_id", "")))
    if key and key in outputs:
        return (key, outputs[key])
    for k, v in outputs.items():
        if v is not None:
            return (str(k), v)
    k0 = next(iter(outputs))
    return (str(k0), outputs[k0])


_MONEY_HINTS = (
    "revenue",
    "rev_",
    "cogs",
    "inventory",
    "ar_",
    "ar",
    "ni_",
    "ni",
    "ocf",
    "assets",
    "goodwill",
    "capex",
    "fcf",
    "sbc",
    "acquisitions",
    "capsw",
    "r&d",
    "rd_",
    "nopat",
    "da_",
    "ap_",
    "income",
    "debt",
    "equity",
    "cash",
    "operatingincome",
    "pretaxincome",
    "tax_ttm",
    "tax",
    "invested_capital",
    "restructuring",
    "rnd",
    "fcf_sbc",
    "x_fcf",
)


def metric_kind(key: Any) -> str:
    """Classify an input/output key.

    pp: percentage-point change | spct: signed percent change | pct: percent level |
    multiple: x-times ratio | days | count | money | plain. Order matters."""
    k = str(key).lower()
    if k.endswith("_pp") or "change_pp" in k:
        return "pp"
    if "growth" in k or "rel_change" in k or k.endswith("_rel") or k == "rel":
        return "spct"
    if k in ("capex_da", "capex_da_prior_year", "ccr", "ccr_prior_year") or "multiple" in k:
        return "multiple"
    if (
        "pct" in k
        or "ratio" in k
        or "cap_rate" in k
        or k in ("caprate", "roic", "margin")
        or k.endswith("_rate")
        or k.startswith(("roic", "tax_rate"))
    ):
        return "pct"
    if "days" in k or k.startswith(("dso", "dio", "dpo", "delta_dso", "delta_dio", "delta_dpo")):
        return "days"
    if "count" in k or "quarters" in k:
        return "count"
    if any(k == h.rstrip("_") or k.startswith(h) for h in _MONEY_HINTS):
        return "money"
    return "plain"


def fmt_money(v: float) -> str:
    a = abs(v)
    sign = "−" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.1f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.1f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:,.1f}K"
    return f"{sign}${a:,.0f}"


def fmt_value(key: Any, value: Any) -> str:
    """One formatter for every surface: days 1 dp, percentages 1 dp (signed for
    changes), multiples 2 dp with ×, counts as integers, money as $X.XB/$X.XM."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return fmt_num(value)
    v = float(value)
    if math.isnan(v) or math.isinf(v):
        return "n/m"
    kind = metric_kind(key)
    if kind == "days":
        return f"{v:,.1f} d"
    if kind == "pp":
        return f"{v * 100:+.1f} pp".replace("-", "−")
    if kind == "spct":
        return f"{v * 100:+,.1f}%".replace("-", "−")
    if kind == "pct":
        return f"{v * 100:,.1f}%".replace("-", "−")
    if kind == "multiple":
        return f"{v:,.2f}×"
    if kind == "count":
        return f"{v:,.0f}"
    if kind == "money":
        return fmt_money(v)
    return fmt_num(v)


def sev_class(sev: Any) -> str:
    return {"CRITICAL": "critical", "WARNING": "warning", "NO_FLAG": "noflag"}.get(str(sev), "na")


def sev_label(sev: Any) -> str:
    """Exact PRD labels: CRITICAL, WARNING, NO FLAG, N/A."""
    return {"CRITICAL": "CRITICAL", "WARNING": "WARNING", "NO_FLAG": "NO FLAG"}.get(str(sev), "N/A")


def badge(sev: Any, label: str | None = None) -> str:
    text = label if label is not None else sev_label(sev)
    return f'<span class="vx-badge {sev_class(sev)}">{esc(text)}</span>'


def context_badge(sev: Any, note: str = "context") -> str:
    """Subdued outline badge for unscored, context-only flags (prior periods,
    10-Q footnote edits): grey dashed outline, severity only as a small dot."""
    return (
        f'<span class="vx-badge is-context {sev_class(sev)}" '
        f'title="Shown for context, not scored">{esc(sev_label(sev))} · {esc(note)}</span>'
    )


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
    title = esc(" · ".join(str(p) for p in parts) or shown)
    if url:
        return (
            f'<a class="vx-cite" href="{url}" target="_blank" rel="noopener noreferrer" '
            f'title="{title}"><span class="vx-cite-text">{esc(shown)}</span>'
            '<span class="vx-cite-arrow">↗</span></a>'
        )
    return (
        f'<span class="vx-cite" title="{title}"><span class="vx-cite-text">{esc(shown)}</span>'
        "</span>"
    )


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


def section_header(title: str, count: int | str | None, desc: str) -> str:
    cnt = f'<span class="vx-count">{esc(count)}</span>' if count is not None else ""
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
    from veritas.ui.plain import GLOSSARY  # lazy: plain imports this module

    def _lab(k: str) -> str:
        tip = GLOSSARY.get(k)
        if not tip:
            return f'<span class="vx-label">{esc(k)}</span>'
        return (
            f'<span class="vx-label vx-term" tabindex="0" aria-label="{esc(k)}: {esc(tip)}" '
            f'data-tip="{esc(tip)}">{esc(k)}</span>'
        )

    fact_html = "".join(f'<div class="vx-fact">{_lab(k)}<b>{esc(v)}</b></div>' for k, v in facts)
    filings = []
    for cit in as_list(get(result, "filings"))[:6]:
        form = get(cit, "form", "")
        fdate = fmt_date(get(cit, "filing_date"))
        url = safe_url(get(cit, "url"))
        inner = f"<b>{esc(form)}</b>{esc(fdate)}"
        if url:
            filings.append(
                f'<a class="vx-filing" href="{url}" target="_blank" rel="noopener noreferrer" '
                f'title="{esc(GLOSSARY.get(str(form)) or get(cit, "accession", ""))}">{inner}</a>'
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
    plain_names: Mapping[str, str] | None = None,
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
        '<div class="vx-fact"><span class="vx-label vx-term" tabindex="0" data-tip="How many '
        "of the scored checks could actually be run for this company. Checks that could not "
        'run are listed, never counted as a pass.">Coverage</span>'
        f"<b>{esc(coverage)}</b></div>"
        f'<div class="vx-fact"><span class="vx-label">Modules</span><b>{esc(k)} of 3 modules '
        "assessed</b></div>"
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
        zero = not pts
        amount = "0" if zero else f"−{esc(pts)}"
        name = (plain_names or {}).get(str(cat)) or human_text(cat)
        rows.append(
            f'<div class="vx-ded-row {"is-zero" if zero else ""}" title="{esc(human_text(cat))}">'
            f'<span>{esc(name)}</span><div class="vx-ded-bar"><i style="--w:{w:.0f}%"></i></div>'
            f"<span>{amount}"
            f'<span class="vx-ded-cap"> / {esc(cap or "?")}</span></span></div>'
        )
    if rows:
        ded = (
            '<div class="vx-label" style="margin-bottom:10px">Points taken off · '
            "points / maximum</div>"
            f'<div class="vx-deductions">{"".join(rows)}</div>'
        )
    else:
        ded = (
            '<div class="vx-label" style="margin-bottom:10px">Deductions</div>'
            '<div class="vx-score-note">No deductions recorded.</div>'
        )
    return one_line(
        f'<div class="vx vx-panel vx-score"><div class="vx-ring-wrap">{score_ring(score)}'
        '<div class="vx-ring-caption">Starts at 100. Points come off for each flag found.'
        "</div></div>"
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
            f"<span class='vx-k' title='{esc(k)}'>{esc(human_key(k))}</span>"
            f"<span class='vx-v'>{fmt_value(k, v)}</span>"
        )
    if inputs and outputs:
        rows.append('<div class="vx-kv-sep"></div>')
    for k, v in outputs.items():
        rows.append(
            f"<span class='vx-k' title='{esc(k)}'>{esc(human_key(k))}</span>"
            f"<span class='vx-v is-out'>{fmt_value(k, v)}</span>"
        )
    return f'<div class="vx-kv">{"".join(rows)}</div>' if rows else ""


PRIOR_CATEGORY = "accounting_prior_period"


def _ord(value: Any) -> int:
    if isinstance(value, (date, datetime)):
        return value.toordinal()
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10]).toordinal()
        except ValueError:
            return 0
    return 0


def current_citations_first(cits: Sequence[Any], period_end: Any) -> list[Any]:
    """Citations for the check's own period first (report_date == period_end),
    then the rest by filing date, newest first."""
    p = _ord(period_end)
    return sorted(
        cits,
        key=lambda ct: (
            0 if p and _ord(get(ct, "report_date")) == p else 1,
            -_ord(get(ct, "filing_date")),
        ),
    )


def is_context(finding: Any) -> bool:
    """Prior-period findings are context only and never scored."""
    return str(get(finding, "category", "")) == PRIOR_CATEGORY


def finding_card(finding: Any, index: int = 0, plain: str | None = None) -> str:
    sev = get(finding, "severity")
    context = is_context(finding)
    cls = sev_class(sev) + (" is-context" if context else "")
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
        cits = current_citations_first(as_list(get(check, "citations")), get(check, "period_end"))
        shows_body = (
            (
                '<details class="vx-math"><summary>Show the formula</summary>'
                f'<div class="vx-formula">{esc(formula)}</div></details>'
                if formula
                else ""
            )
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
                f'<div class="vx-verdict-expl" style="margin-top:10px">'
                f"{esc(human_reason(reason))}</div>"
            )
    else:
        cits = [c for c in as_list(get(finding, "citations"))]
        shows_body = (
            f'<div class="vx-verdict-expl">{esc(human_text(get(finding, "explanation", "")))}'
            f'</div><div class="vx-cites">{"".join(citation_link(c) for c in cits[:3])}</div>'
        )
    shows = (
        '<div class="vx-col vx-col-shows"><div class="vx-col-head">'
        '<span class="vx-label">What the numbers show</span>'
        f'<span class="vx-label">Module {esc(module)}</span></div>{shows_body}</div>'
    )

    # Column 3: verdict
    annots = as_list(get(check, "annotations")) if check is not None else []
    if context:
        annots = ["prior period · context · unscored", *annots]
    annot_html = (
        f'<div class="vx-annots">{"".join(tag(human_text(a)) for a in annots)}</div>'
        if annots
        else ""
    )
    expl = human_text(get(finding, "explanation", "")) if check is not None else ""
    head_badge = context_badge(sev) if context else badge(sev)
    verdict = (
        '<div class="vx-col"><div class="vx-col-head">'
        f'<span class="vx-label">{"Prior period" if context else "Screen result"}</span>'
        f"{head_badge}</div>"
        f'<div class="vx-verdict-title">{esc(get(finding, "title", "Untitled finding"))}</div>'
        + (f'<div class="vx-verdict-expl">{esc(expl)}</div>' if expl else "")
        + annot_html
        + "</div>"
    )
    plain_html = (
        f'<div class="vx-plain"><span class="vx-plain-tag">In plain English</span>'
        f"<span>{esc(plain)}</span></div>"
        if plain
        else ""
    )
    return one_line(
        f'<div class="vx vx-card {cls}" style="--i:{min(index, 12)}">'
        f"{plain_html}{said}{connector}{shows}{verdict}</div>"
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
    status_tag = "not flagged"
    head = (
        '<div class="vx-checkhead"><div><div class="vx-label">Hedging drift · 10-K pair</div>'
        f"<div class='vx-h3'>{esc(status_text)}"
        f"</div></div>{badge(sev) if sev else tag(status_tag)}</div>"
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
    annots = "".join(tag(human_text(a)) for a in as_list(get(check, "annotations")))
    key, value = primary_value(check)
    latest = (
        f" · latest {fmt_value(key, value)}" if state == "COMPUTED" and value is not None else ""
    )
    return one_line(
        f'<div class="vx vx-checkhead"><div><div class="vx-label">'
        f"{esc(check_label(get(check, 'check_id')))} · "
        f"{'scored' if scored else 'display only'} · {esc(state_txt)} · period "
        f"{esc(fmt_date(get(check, 'period_end')))}{esc(latest)}</div>"
        f"<div class='vx-h3'>{esc(get(check, 'title', 'Untitled check'))}</div>"
        + (f'<div class="vx-formula">{esc(formula)}</div>' if formula else "")
        + f'</div><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">{annots}'
        f"{right}</div></div>"
    )


def display_metrics(checks: Sequence[Any]) -> str:
    tiles = []
    for i, c in enumerate(checks):
        key, value = primary_value(c)
        state = str(get(c, "state", ""))
        shown = fmt_value(key, value) if state == "COMPUTED" else "n/m"
        reason = get(c, "reason")
        annots = " · ".join(human_text(a) for a in as_list(get(c, "annotations")))
        sub = esc(get(c, "formula", ""))
        foot = esc(
            human_reason(reason) if reason else (annots or "shown for context, never scored")
        )
        tiles.append(
            f'<div class="vx-metric-tile" style="--i:{i}"><div class="vx-label">'
            f"{esc(check_label(get(c, 'check_id')))} · display only</div><b>{shown}</b>"
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
            weight_html = (
                '<span class="vx-muted vx-nowrap" title="No weight table row was parsed for '
                'this metric">not parsed</span>'
            )
        cit = m.get("citation")
        rows.append(
            f'<tr><td><span class="vx-metric">{esc(m.get("metric") or "—")}</span></td>'
            f"<td>{esc(m.get('context') or '—')}</td><td>{weight_html}</td>"
            f'<td><div style="color:var(--text);margin-bottom:8px">{esc(m.get("sentence") or "")}'
            f"</div>{citation_link(cit)}</td></tr>"
        )
    return one_line(
        '<div class="vx vx-panel" style="padding:8px 10px"><table class="vx-table"><thead><tr>'
        "<th>Metric</th><th>Plan context</th><th class='vx-col-weight'>Weight</th>"
        "<th>Source sentence</th></tr>"
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


def flag_card(finding: Any, index: int = 0, plain: str | None = None) -> str:
    """Compact flag panel (Proxy view): badge, title, explanation, citations."""
    sev = get(finding, "severity")
    cits = "".join(citation_link(c) for c in as_list(get(finding, "citations"))[:3])
    plain_html = (
        f'<div class="vx-plain is-inline"><span class="vx-plain-tag">In plain English</span>'
        f"<span>{esc(plain)}</span></div>"
        if plain
        else ""
    )
    return one_line(
        f'<div class="vx vx-card {sev_class(sev)}" style="--i:{min(index, 12)};'
        'grid-template-columns:1fr"><div class="vx-col vx-col-said">'
        f'<div class="vx-col-head"><span class="vx-label">{esc(get(finding, "id", ""))} · '
        f"{esc(get(finding, 'category', ''))}</span>{badge(sev)}</div>"
        f'<div class="vx-verdict-title" style="margin-top:0">{esc(get(finding, "title", ""))}'
        f"</div>{plain_html}"
        f'<div class="vx-verdict-expl">{esc(get(finding, "explanation", ""))}</div>'
        f'<div class="vx-cites">{cits}</div></div></div>'
    )


# ---------------------------------------------------------------------------
# Overview: Module B footnote edits, narrative sources; Proxy: CD&A text
# ---------------------------------------------------------------------------


def inline_diff(prior: Any, current: Any, kind: str) -> str:
    """One-column redline of the edited sentence (escaped token by token)."""
    k = kind.upper()
    if k == "MODIFIED" and isinstance(prior, str) and isinstance(current, str):
        a, b = prior.split(), current.split()
        out: list[str] = []
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                out.append(esc(" ".join(a[i1:i2])))
                continue
            if i2 > i1:
                out.append(f"<del>{esc(' '.join(a[i1:i2]))}</del>")
            if j2 > j1:
                out.append(f"<ins>{esc(' '.join(b[j1:j2]))}</ins>")
        return " ".join(out)
    if k == "DELETED" or (prior and not current):
        return f"<del>{esc(prior)}</del>"
    return f"<ins>{esc(current)}</ins>"


def footnote_edit_row(edit: Mapping[str, Any], index: int = 0, plain: str | None = None) -> str:
    """A compact material footnote edit: note title, form/scoring tag, redline."""
    form = str(edit.get("form") or "")
    scored = form == "10-K"
    scope = f"{form} · scored" if scored else f"{form} · unscored"
    kind = str(edit.get("kind") or "CHANGE").upper()
    meta = tag(scope) + tag(kind.lower())
    if edit.get("softened"):
        meta += tag("modal softening")
    sev_html = badge("WARNING") if scored else context_badge("WARNING", "unscored")
    expl = edit.get("explanation")
    expl_html = f'<div class="vx-edit-expl">{esc(expl)}</div>' if expl else ""
    if plain:
        expl_html = (
            f'<div class="vx-plain is-inline"><span class="vx-plain-tag">In plain English</span>'
            f"<span>{esc(plain)}</span></div>" + expl_html
        )
    period = fmt_date(edit.get("period"))
    return one_line(
        f'<div class="vx vx-edit {"is-context" if not scored else ""}" '
        f'style="--i:{min(index, 12)}"><div class="vx-edit-head"><div>'
        f'<div class="vx-label">Note · {esc(human_text(edit.get("category") or "other"))}'
        f" · period {esc(period)}</div>"
        f'<div class="vx-edit-title">{esc(edit.get("note") or "Untitled note")}</div></div>'
        f"{sev_html}</div>"
        f'<div class="vx-edit-text">{inline_diff(edit.get("prior"), edit.get("current"), kind)}'
        f'</div>{expl_html}<div class="vx-annots">{meta}</div></div>'
    )


_SECTION_OK = ("OK", "TRUNCATED")


def narrative_sources(sections: Sequence[Any]) -> str:
    """Per-filing narrative status row (§7.2.2): form, period, label, word count."""
    chips = []
    for i, sec in enumerate(sections):
        if not isinstance(sec, Mapping):
            continue
        form = esc(sec.get("form") or "—")
        period = esc(fmt_date(sec.get("report_date")))
        status = str(sec.get("status") or "")
        url = safe_url(sec.get("url"))
        if status in _SECTION_OK:
            wc = sec.get("word_count")
            words = f"{wc:,} words" if isinstance(wc, int) else "—"
            trunc = " · truncated" if status == "TRUNCATED" else ""
            body = (
                f'<span class="vx-src-label">{esc(human_text(sec.get("label") or "section"))}'
                f'</span><span class="vx-src-meta">{words}{trunc}</span>'
            )
            cls = ""
        else:
            reason = human_reason(sec.get("reason")) if sec.get("reason") else ""
            body = (
                '<span class="vx-src-label">Narrative unavailable for this filing</span>'
                f'<span class="vx-src-meta">{esc(reason)}</span>'
            )
            cls = "is-off"
        head = f'<span class="vx-src-form">{form}</span><span class="vx-src-meta">{period}</span>'
        inner = f'<div class="vx-src-head">{head}</div>{body}'
        title = esc(sec.get("accession") or "")
        if url:
            chips.append(
                f'<a class="vx-src {cls}" style="--i:{i}" href="{url}" target="_blank" '
                f'rel="noopener noreferrer" title="{title}">{inner}</a>'
            )
        else:
            chips.append(f'<div class="vx-src {cls}" style="--i:{i}" title="{title}">{inner}</div>')
    if not chips:
        return ""
    return one_line(
        '<div class="vx"><div class="vx-label" style="margin:18px 0 8px">Narrative sources · '
        f'Module A</div><div class="vx-srcrow">{"".join(chips)}</div></div>'
    )


def text_block(text: str) -> str:
    """Escaped, scrollable block of extracted filing text."""
    body = re.sub(r"\n\s*\n+", "<br><br>", esc(text.strip())).replace("\n", "<br>")
    return one_line(f'<div class="vx vx-textblock">{body}</div>')
