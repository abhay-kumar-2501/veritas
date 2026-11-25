"""Simple view (docs/decisions/ui.md D-612): the default, jargon-free rendering of a
result for readers with no finance background. "Analyst detail" switches the page
back to the technical view (components.py), which is unchanged.

Rules, as for plain.py:

* Only restate what the engine computed; never compute a new metric or change a
  severity. Every sentence here is either fixed copy or comes from plain.py.
* Quotes from filings (management statements, fine-print sentences, pay-report
  sentences) are shown verbatim: they are the evidence, not our wording.
* Same HTML rules as components.py: escape everything, single-line markup.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from veritas.ui import plain
from veritas.ui.components import (
    as_list,
    badge,
    esc,
    fmt_date,
    fmt_num,
    fmt_value,
    get,
    inline_diff,
    is_context,
    one_line,
    primary_value,
    safe_url,
    score_ring,
    sev_class,
    tag,
    word_diff,
)
from veritas.ui.plain import CHECK_PLAIN, SEVERITY_PLAIN

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

FORM_PLAIN: dict[str, str] = {
    "10-K": "Annual report",
    "10-K/A": "Amended annual report",
    "10-Q": "Quarterly report",
    "10-Q/A": "Amended quarterly report",
    "8-K": "Company announcement",
    "DEF 14A": "Executive pay report",
    "DEFA14A": "Extra shareholder meeting material",
}

AREA_PLAIN: dict[str, str] = {
    "A": "Words vs. numbers",
    "B": "Changes to the fine print",
    "C": "How executives are paid",
    "D": "The score",
}

VIEW_PLAIN: dict[str, str] = {
    "[1] Overview": "Summary",
    "[2] Redline": "Fine-print changes",
    "[3] Ratios": "Trends over time",
    "[4] Proxy": "Executive pay",
}

FILTER_PLAIN: dict[str, str] = {
    "ALL": "Everything",
    "CRITICAL": "Big gaps",
    "WARNING": "Worth a closer look",
    "NO FLAG": "No flag",
}

PAIR_PLAIN: dict[str, str] = {"10-K": "Annual reports", "10-Q": "Quarterly reports"}

STAGES_PLAIN: tuple[str, ...] = (
    "Finding the company",
    "Getting its list of reports",
    "Getting its numbers",
    "Getting its reports",
    "Checking words against numbers",
    "Checking the fine print",
    "Checking executive pay",
    "Working out the score",
)

METRIC_PLAIN: dict[str, str] = {
    "ADJ_EBITDA": "A custom profit figure (“adjusted EBITDA”)",
    "NONGAAP_EPS": "A custom profit-per-share figure (“adjusted EPS”)",
    "REVENUE": "Sales",
    "TSR": "Shareholder return (share price plus dividends)",
    "FCF": "Spare cash (“free cash flow”)",
    "ROIC": "Profit on money invested",
    "M_AND_A": "Buying other businesses",
}

_CONTEXT_PLAIN: dict[str, str] = {
    "psu": "Share awards tied to results",
    "psus": "Share awards tied to results",
    "rsu": "Share awards",
    "rsus": "Share awards",
    "ltip": "Long-term bonus plan",
    "sti": "Yearly bonus",
    "aip": "Yearly bonus",
}

STATUS_PLAIN: dict[str, str] = {
    "ok": "checked",
    "partial": "partly checked",
    "unavailable": "not available",
    "low_confidence": "couldn't be read reliably",
    "timeout": "took too long",
    "error": "ran into a problem",
}

DISCLAIMER_PLAIN = (
    "VERITAS is an automated check of public company reports. A flag means “worth a closer "
    "look”, not that anyone did anything wrong, and automated checks can be mistaken, so "
    "confirm anything important in the original report. This is not investment advice. "
    "VERITAS isn't run or endorsed by the SEC."
)


def form_plain(form: Any) -> str:
    f = str(form or "").strip()
    return FORM_PLAIN.get(f.upper(), f or "Report")


def short_date(value: Any) -> str:
    """'2026-11-03' -> '3 Nov 2026'."""
    d = value
    if isinstance(d, str) and len(d) >= 10:
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return str(d)
    if isinstance(d, (date, datetime)):
        return f"{d.day} {d:%b %Y}"
    return "date unknown"


def month(value: Any) -> str:
    """'2026-11-03' -> 'Nov 2026'."""
    d = value
    if isinstance(d, str) and len(d) >= 10:
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return str(d)
    if isinstance(d, (date, datetime)):
        return f"{d:%b %Y}"
    return ""


def fy_end_plain(mmdd: Any) -> str:
    s = re.sub(r"\D", "", str(mmdd or ""))
    if len(s) != 4:
        return "—"
    try:
        return date(2001, int(s[:2]), 1).strftime("%B")
    except ValueError:
        return "—"


def metric_plain(key: Any) -> str:
    k = str(key or "")
    return METRIC_PLAIN.get(k, k.replace("_", " ").title() or "—")


def context_plain(text: Any) -> str:
    t = str(text or "").strip()
    return _CONTEXT_PLAIN.get(t.lower(), t[:1].upper() + t[1:] if t else "—")


def banner_plain(text: Any) -> str:
    """Plain version of a result banner; unknown text is passed through."""
    t = str(text or "")
    low = t.lower()
    if "10-k/a" in low or "amended content" in low:
        return (
            "After the annual report we read, the company filed an amendment to it (an "
            "amendment can add or change information). We read the original, not the amendment."
        )
    if "shell" in low or "pre-revenue" in low:
        return "This looks like a company with little or no sales yet, so most checks don't apply."
    if "financial issuer" in low:
        return (
            "This is a bank, insurer or other financial company. Our checks are built for "
            "other kinds of businesses, so fewer of them apply."
        )
    if low.startswith("truncated"):
        return "One of the reports was too large to read in full, so part of it was skipped."
    if "unsupported" in low or "no 10-k" in low:
        return (
            "This company doesn't file the standard yearly and quarterly reports our checks "
            "need, so most checks don't apply."
        )
    return (
        "There's a technical note about this company's reports. Switch on Analyst detail to "
        "read it."
    )


_PERIODIC = ("10-K", "10-Q", "10-K/A", "10-Q/A")


def doc_label(doc: Any) -> str:
    """'Annual report for Dec 2025' (periodic reports name the period they cover);
    other documents name the month they were filed: 'Executive pay report, filed Mar 2026'."""
    form = str(get(doc, "form", "") or "").upper()
    period = month(get(doc, "report_date"))
    if form in _PERIODIC and period:
        return f"{form_plain(form)} for {period}"
    filed = month(get(doc, "filing_date"))
    return form_plain(form) + (f", filed {filed}" if filed else "")


def _source_link(cit: Any) -> str:
    """'Annual report for Sep 2026 ↗' linked to the filing on sec.gov."""
    if cit is None:
        return ""
    url = safe_url(get(cit, "url"))
    text = doc_label(cit)
    title = esc(" · ".join(str(p) for p in (get(cit, "form"), get(cit, "accession")) if p))
    if not url:
        return (
            f'<span class="vx-cite" title="{title}">'
            f'<span class="vx-cite-text">{esc(text)}</span></span>'
        )
    return (
        f'<a class="vx-cite" href="{url}" target="_blank" rel="noopener noreferrer" '
        f'title="{title}"><span class="vx-cite-text">{esc(text)}</span>'
        '<span class="vx-cite-arrow">↗</span></a>'
    )


def _links(cits: Sequence[Any], limit: int = 2) -> str:
    seen: set[str] = set()
    out = []
    for cit in cits:
        html = _source_link(cit)
        key = str(get(cit, "url") or html)
        if not html or key in seen:
            continue
        seen.add(key)
        out.append(html)
        if len(out) >= limit:
            break
    if not out:
        return ""
    return (
        '<div class="vx-scard-links"><span class="vx-label">Check it yourself</span>'
        f"{''.join(out)}</div>"
    )


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def nearest_first(cits: Sequence[Any], period_end: Any) -> list[Any]:
    """Filings for the card's own period first, then those filed closest to it, so an
    earlier-period card links to the reports of that time, not the newest ones."""
    p = _as_date(period_end)
    if p is None:
        return list(cits)

    def key(ct: Any) -> tuple[int, int]:
        rd, fd = _as_date(get(ct, "report_date")), _as_date(get(ct, "filing_date"))
        exact = 0 if rd == p else 1
        dist = abs((fd - p).days) if fd else 10**6
        return exact, dist

    return sorted(cits, key=key)


def sev_pill(sev: Any, text: str | None = None) -> str:
    return badge(sev, text if text is not None else SEVERITY_PLAIN.get(str(sev), "Not checked"))


def context_pill(text: str = "For context") -> str:
    return (
        '<span class="vx-badge is-context warning" title="Not counted in the score">'
        f"{esc(text)}</span>"
    )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def source_chip(source: Any, as_of: Any) -> tuple[str, str]:
    src = str(source or "").lower()
    when = short_date(as_of)
    if src == "live":
        return (f"Live data · {when}", "is-accent")
    if src == "cache":
        return (f"Saved copy · {when}", "")
    if src == "seed":
        return (f"Built-in example · data as of {when}", "")
    return (f"Data as of {when}", "")


def identity(result: Any, synthetic: bool) -> str:
    name = plain.short_name(result)
    facts = [
        ("Official name", get(result, "company_name", "—")),
        ("Stock symbol", get(result, "ticker", "—")),
        ("Financial year ends", fy_end_plain(get(result, "fiscal_year_end"))),
    ]
    fact_html = "".join(
        f'<div class="vx-fact"><span class="vx-label">{esc(k)}</span><b>{esc(v)}</b></div>'
        for k, v in facts
    )
    filings = []
    for cit in as_list(get(result, "filings"))[:6]:
        label = doc_label(cit)
        if " for " in label:
            head, _, rest = label.partition(" for ")
            rest = "for " + rest
        else:
            head, _, rest = label.partition(", ")
        inner = f"<b>{esc(head)}</b>{esc(rest)}"
        url = safe_url(get(cit, "url"))
        tip = esc(f"{get(cit, 'form', '')} filed {fmt_date(get(cit, 'filing_date'))}")
        if url:
            filings.append(
                f'<a class="vx-filing" href="{url}" target="_blank" rel="noopener noreferrer" '
                f'title="{tip}">{inner}</a>'
            )
        else:
            filings.append(f'<span class="vx-filing" title="{tip}">{inner}</span>')
    kicker = "Made-up example company" if synthetic else "Company"
    return one_line(
        '<div class="vx vx-identity"><div>'
        f'<div class="vx-label">{esc(kicker)}</div><div class="vx-h1">{esc(name)}</div>'
        f'<div class="vx-facts">{fact_html}</div></div>'
        '<div><div class="vx-label" style="text-align:right;margin-bottom:8px">Reports we read'
        f'</div><div class="vx-filings">{"".join(filings) or tag("none listed")}</div></div>'
        "</div>"
    )


# ---------------------------------------------------------------------------
# Summary tab
# ---------------------------------------------------------------------------


def _unassessed_plain(text: str) -> str:
    m = re.match(r"\s*Module\s+([A-D])\b", text)
    if m:
        return AREA_PLAIN.get(m.group(1), text)
    return text


def score_panel(
    score_obj: Any,
    caps: Mapping[str, int],
    module_statuses: Mapping[str, Any],
    n_checks: int | None = None,
) -> str:
    score = get(score_obj, "score")
    num = get(score_obj, "coverage_num")
    den = get(score_obj, "coverage_den")
    total = n_checks if n_checks is not None else den
    coverage = f"{num} of {den}" if den else "—"
    tip = (
        f"Of the {total} number checks, {den} were used for this company and we ran {num}. A "
        "check that found nothing to examine still counts as run. Checks that were left out or "
        "couldn't run are listed, never counted as a pass."
    )
    skipped = int(total or 0) - int(den or 0)
    verb = "was" if skipped == 1 else "were"
    sub = (
        f'<div class="vx-score-note" style="margin-top:4px">{skipped} more {verb} left out for '
        "this company.</div>"
        if skipped > 0
        else ""
    )
    meta = (
        '<div class="vx-fact"><span class="vx-label vx-term" tabindex="0" '
        f'data-tip="{esc(tip)}">Number checks run</span>'
        f"<b>{esc(coverage)}</b>{sub}</div>"
    )
    notes = []
    if str(get(score_obj, "label", "")).upper() == "NOT SCORED":
        notes.append("<b>Not scored.</b> We don't score this kind of company.")
    elif score is None:
        notes.append(
            "<b>Not enough data.</b> We could run fewer than 60% of the number checks, so we "
            "don't give a score. Missing data never counts as a pass."
        )
    scope = get(score_obj, "scope_note")
    if scope:
        notes.append(esc(banner_plain(scope)))
    unassessed = [_unassessed_plain(str(u)) for u in as_list(get(score_obj, "unassessed"))]
    if not unassessed:
        unassessed = [
            AREA_PLAIN.get(k, k)
            for k, st in module_statuses.items()
            if str(st) not in ("ok", "partial")
        ]
    if unassessed:
        notes.append(
            f"<b>Not checked:</b> {esc(', '.join(unassessed))}. It adds or takes away no points."
        )
    notes.append(
        "A rough guide, not a grade. It isn't adjusted for industry, so it isn't meant for "
        "comparing very different kinds of businesses. The cut-offs are rules of thumb, not "
        "tested against what later happened to companies."
    )
    note_html = "".join(f'<div class="vx-score-note">{n}</div>' for n in notes)
    not_scored = str(get(score_obj, "label", "")).upper() == "NOT SCORED"
    ring = score_ring(score, "NOT<br>SCORED" if not_scored else "NOT ENOUGH<br>DATA")

    rows = []
    for item in as_list(get(score_obj, "deductions")):
        try:
            cat, pts = item[0], item[1]
        except (TypeError, IndexError, KeyError):
            continue
        cap = caps.get(str(cat)) or 0
        w = 0.0 if not cap else min(100.0, float(pts) / cap * 100)
        zero = not pts
        amount = "0" if zero else f"−{esc(pts)}"
        name = plain.DEDUCTION_PLAIN.get(str(cat)) or str(cat)
        rows.append(
            f'<div class="vx-ded-row {"is-zero" if zero else ""}">'
            f'<span>{esc(name)}</span><div class="vx-ded-bar"><i style="--w:{w:.0f}%"></i></div>'
            f"<span>{amount}"
            f'<span class="vx-ded-cap"> of {esc(cap or "?")} max</span></span></div>'
        )
    ded = '<div class="vx-label" style="margin-bottom:10px">Points taken off</div>' + (
        f'<div class="vx-deductions">{"".join(rows)}</div>'
        if rows
        else '<div class="vx-score-note">None.</div>'
    )
    return one_line(
        f'<div class="vx vx-panel vx-score"><div class="vx-ring-wrap">{ring}'
        '<div class="vx-ring-caption">Starts at 100. Points come off for each flag found.'
        "</div></div>"
        '<div><div class="vx-label">Our score</div><div class="vx-score-title">'
        "Fewer flags, higher score</div>"
        f'<div class="vx-score-meta">{meta}</div>{note_html}</div><div>{ded}</div></div>'
    )


def not_assessed(module_key: str, status: Any) -> str:
    area = AREA_PLAIN.get(module_key, module_key)
    st = STATUS_PLAIN.get(str(status), "not available")
    return one_line(
        f'<div class="vx vx-empty is-na"><div class="vx-empty-glyph">–</div>'
        f"<div><div class='vx-h4'>{esc(area)}: not checked ({esc(st)})</div>"
        "<div class='vx-p'>We couldn't run this part for this company. It adds or takes away "
        "no points. Switch on Analyst detail for the technical reason.</div></div></div>"
    )


def _section_plain(label: Any, form: Any) -> str:
    low = str(label or "").lower()
    if "ex-99" in low or "ex99" in low or str(form).upper() == "8-K":
        return "Results press release"
    if "item 7" in low or "item 2" in low or "md&a" in low:
        return "Managers' discussion of results"
    return "Report text"


def sources(sections: Sequence[Any]) -> str:
    chips = []
    for i, sec in enumerate(sections):
        if not isinstance(sec, Mapping):
            continue
        form = sec.get("form")
        when = month(sec.get("report_date"))
        url = safe_url(sec.get("url"))
        if str(sec.get("status") or "") in ("OK", "TRUNCATED"):
            wc = sec.get("word_count")
            words = f"{wc:,} words" if isinstance(wc, int) else ""
            if str(sec.get("status")) == "TRUNCATED":
                words += " · read in part"
            body = (
                f'<span class="vx-src-label">{esc(_section_plain(sec.get("label"), form))}</span>'
                f'<span class="vx-src-meta">{esc(words)}</span>'
            )
            cls = ""
        else:
            body = (
                '<span class="vx-src-label">Couldn\'t read this one</span>'
                '<span class="vx-src-meta"></span>'
            )
            cls = "is-off"
        head = (
            f'<span class="vx-src-form">{esc(form_plain(form))}</span>'
            f'<span class="vx-src-meta">{esc(when)}</span>'
        )
        inner = f'<div class="vx-src-head">{head}</div>{body}'
        if url:
            chips.append(
                f'<a class="vx-src {cls}" style="--i:{i}" href="{url}" target="_blank" '
                f'rel="noopener noreferrer">{inner}</a>'
            )
        else:
            chips.append(f'<div class="vx-src {cls}" style="--i:{i}">{inner}</div>')
    if not chips:
        return ""
    return one_line(
        '<div class="vx"><div class="vx-label" style="margin:18px 0 8px">What we read: '
        f'managers\' own words</div><div class="vx-srcrow">{"".join(chips)}</div></div>'
    )


def finding_card(finding: Any, index: int, company: str) -> str:
    """One Module A finding in plain English: severity in words, the plain sentence,
    the managers' quote (verbatim) if one was paired, and links to the filings."""
    sev = get(finding, "severity")
    check = get(finding, "check")
    cid = str(get(check, "check_id", "")) if check is not None else ""
    cp = CHECK_PLAIN.get(cid)
    title = cp.name if cp else str(get(finding, "title", "Finding"))
    text = plain.finding_plain(finding, company) or (
        "We couldn't put this one into a plain sentence. Switch on Analyst detail to see the "
        "figures."
    )
    context = is_context(finding)
    sev_txt = SEVERITY_PLAIN.get(str(sev), "")
    when = plain.month_year(get(check, "period_end")) if check is not None else ""
    extra = ""
    for a in as_list(get(check, "annotations")) if check is not None else []:
        p = plain.annotation_plain(a)
        if p:
            extra = f'<div class="vx-scard-note">{esc(p)}</div>'
            break
    said = []
    for claim in as_list(get(finding, "claims"))[:2]:
        cit = get(claim, "citation")
        quote = get(cit, "quote_raw")
        if quote:
            said.append(
                f'<div class="vx-quote">{esc(quote)}</div>'
                f'<div class="vx-cites" style="margin:6px 0 10px">{_source_link(cit)}</div>'
            )
    said_html = (
        '<div class="vx-scard-said"><span class="vx-label">What managers said</span>'
        f"{''.join(said)}</div>"
        if said
        else ""
    )
    cits = (
        nearest_first(as_list(get(check, "citations")), get(check, "period_end"))
        if check is not None
        else as_list(get(finding, "citations"))
    )
    analogy = f'<div class="vx-point-analogy">{esc(cp.analogy)}</div>' if cp else ""
    head_right = "Earlier period · not scored" if context else when
    return one_line(
        f'<div class="vx vx-point vx-scard {sev_class(sev)}{" is-context" if context else ""}" '
        f'style="--i:{min(index, 12)}"><div class="vx-point-head">'
        f'<span class="vx-point-sev"><i></i>{esc(sev_txt)}</span>'
        f'<span class="vx-point-view">{esc(head_right)}</span></div>'
        f'<div class="vx-point-title">{esc(title)}</div>'
        f'<div class="vx-point-text">{esc(text)}</div>{extra}{said_html}{analogy}'
        f"{_links(cits)}</div>"
    )


def edit_row(edit: Mapping[str, Any], index: int = 0) -> str:
    form = str(edit.get("form") or "")
    scored = form == "10-K"
    when = month(edit.get("period"))
    scope = (
        f"{form_plain(form)} · {when} · counts toward the score"
        if scored
        else f"{form_plain(form)} · {when} · for context, not scored"
    )
    pill = sev_pill("WARNING") if scored else context_pill()
    kind = str(edit.get("kind") or "CHANGE").upper()
    return one_line(
        f'<div class="vx vx-edit {"is-context" if not scored else ""}" '
        f'style="--i:{min(index, 12)}"><div class="vx-edit-head"><div>'
        f'<div class="vx-label">{esc(scope)}</div>'
        f'<div class="vx-edit-title">{esc(edit.get("note") or "Untitled section")}</div></div>'
        f"{pill}</div>"
        f'<div class="vx-plain is-inline"><span class="vx-plain-tag">What changed</span>'
        f"<span>{esc(plain.edit_plain(edit))}</span></div>"
        f'<div class="vx-edit-text">{inline_diff(edit.get("prior"), edit.get("current"), kind)}'
        "</div></div>"
    )


def hedging_text(company: str) -> str:
    return (
        f"{company}'s fine print uses noticeably more cautious words (such as “may” or "
        "“could”) than a year earlier. The counts are on the Fine-print changes tab."
    )


def flag_card(finding: Any, index: int, company: str) -> str:
    """A Module B hedging flag or a Module C pay flag, in plain English."""
    sev = get(finding, "severity")
    cat = str(get(finding, "category", ""))
    if cat == "hedging":
        title, text = "More cautious wording", hedging_text(company)
    else:
        title = "How executives are paid"
        text = plain.incentive_plain(finding) or (
            "This pay check raised a flag. Switch on Analyst detail to see the figures."
        )
    return one_line(
        f'<div class="vx vx-point vx-scard {sev_class(sev)}" style="--i:{min(index, 12)}">'
        f'<div class="vx-point-head"><span class="vx-point-sev"><i></i>'
        f"{esc(SEVERITY_PLAIN.get(str(sev), ''))}</span></div>"
        f'<div class="vx-point-title">{esc(title)}</div>'
        f'<div class="vx-point-text">{esc(text)}</div>'
        f"{_links(as_list(get(finding, 'citations')))}</div>"
    )


# ---------------------------------------------------------------------------
# Fine-print changes tab
# ---------------------------------------------------------------------------


def _filing_panel(doc: Any, role: str) -> str:
    url = safe_url(get(doc, "url"))
    form = form_plain(get(doc, "form"))
    period = month(get(doc, "report_date"))
    filed = short_date(get(doc, "filing_date"))
    link = (
        f'<a class="vx-cite" href="{url}" target="_blank" rel="noopener noreferrer">'
        "Open the report ↗</a>"
        if url
        else ""
    )
    return (
        f'<div class="vx-panel"><div class="vx-label">{esc(role)}</div>'
        f'<div style="display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap">'
        f'<span style="font-weight:600">{esc(form)}</span>'
        f'<span style="color:var(--text-3);font-size:13px">for {esc(period)} · filed '
        f"{esc(filed)}</span>{link}</div></div>"
    )


def pair_meta(prior: Any, current: Any) -> str:
    return one_line(
        f'<div class="vx vx-pairmeta">{_filing_panel(prior, "Before")}'
        f'<div class="vx-pairmeta-arrow">→</div>{_filing_panel(current, "After")}</div>'
    )


def note_label(note: Mapping[str, Any]) -> str:
    title = str(note.get("title") or note.get("key") or "Untitled section")
    counts = note.get("counts") if isinstance(note.get("counts"), Mapping) else {}
    mat = counts.get("material") if isinstance(counts, Mapping) else None
    bits = [title]
    if isinstance(mat, (int, float)) and mat:
        bits.append(f"{int(mat)} notable change" + ("" if mat == 1 else "s"))
    status = str(note.get("status") or "")
    bits.append(
        {
            "NEW NOTE": "new this year",
            "REMOVED NOTE": "dropped this year",
            "TRUNCATED": "too long to compare fully",
        }.get(status, "")
    )
    return " · ".join(b for b in bits if b)


def redline_counts(counts: Mapping[str, Any]) -> str:
    spec = [
        ("inserted", "Added", "is-ins"),
        ("deleted", "Removed", "is-del"),
        ("modified", "Reworded", ""),
        ("softened", "Softer wording", ""),
        ("material", "Notable", "is-mat"),
    ]
    cells = "".join(
        f'<div class="vx-stat {mod}" style="--i:{i}"><span class="vx-label">{esc(lbl)}</span>'
        f"<b>{esc(counts.get(key, '—'))}</b></div>"
        for i, (key, lbl, mod) in enumerate(spec)
    )
    return one_line(f'<div class="vx vx-stats">{cells}</div>')


def redline_row(row: Mapping[str, Any], index: int) -> str:
    kind = str(row.get("kind") or "").upper()
    prior, current = row.get("prior"), row.get("current")
    absent = '<div class="vx-diffcell is-absent">— not there —</div>'
    if kind == "MODIFIED" and isinstance(prior, str) and isinstance(current, str):
        left_html, right_html = word_diff(prior, current)
        left = f'<div class="vx-diffcell">{left_html}</div>'
        right = f'<div class="vx-diffcell">{right_html}</div>'
    else:
        left = f'<div class="vx-diffcell"><del>{esc(prior)}</del></div>' if prior else absent
        right = f'<div class="vx-diffcell"><ins>{esc(current)}</ins></div>' if current else absent
    material = bool(row.get("material"))
    meta = tag(
        {"INSERTED": "added", "DELETED": "removed", "MODIFIED": "reworded"}.get(kind, "changed")
    )
    if material:
        meta += badge("WARNING", "Notable")
    if row.get("softened"):
        meta += tag("softer wording")
    return one_line(
        f'<div class="vx vx-diffrow {"is-material" if material else ""}" '
        f'style="--i:{min(index, 12)}">{left}{right}<div class="vx-diffmeta">{meta}</div></div>'
    )


def hedging_panel(h: Mapping[str, Any]) -> str:
    status = str(h.get("status") or "").upper()
    status_text = {
        "FIRED": "Noticeably more cautious than last year",
        "NO_DRIFT": "Not enough of a change to flag",
        "INSUFFICIENT": "Not enough text to compare",
        "INSUFFICIENT_TEXT": "Not enough text to compare",
    }.get(status, "Not compared")
    pr, cr = h.get("prior_rate"), h.get("current_rate")
    rows = [
        ("Cautious words per 1,000 words", pr, cr),
        ("Cautious words found", h.get("prior_hits"), h.get("current_hits")),
        ("Words read", h.get("prior_tokens"), h.get("current_tokens")),
    ]
    body = "".join(
        f"<tr><td>{esc(name)}</td><td class='num'>{fmt_num(a)}</td>"
        f"<td class='num'>{fmt_num(b)}</td></tr>"
        for name, a, b in rows
    )
    table = (
        '<table class="vx-table"><thead><tr><th>Measure</th><th class="num">Last year</th>'
        f'<th class="num">This year</th></tr></thead><tbody>{body}</tbody></table>'
    )
    pill = sev_pill("WARNING") if status == "FIRED" else tag("no flag")
    head = (
        '<div class="vx-checkhead"><div><div class="vx-label">Cautious wording · annual '
        f"reports</div><div class='vx-h3'>{esc(status_text)}</div></div>{pill}</div>"
    )
    rule = (
        '<div class="vx-score-note" style="margin-top:10px">A flag needs the rate to rise by '
        "more than 15% and by at least 1.5 per 1,000 words, with at least 5 more cautious "
        "words than last year.</div>"
    )
    return one_line(
        f'<div class="vx vx-panel">{head}<div class="vx-hedge" style="margin-top:12px">'
        f"{table}</div>{rule}</div>"
    )


# ---------------------------------------------------------------------------
# Trends tab
# ---------------------------------------------------------------------------


def check_header(check: Any, company: str) -> str:
    cid = str(get(check, "check_id", ""))
    cp = CHECK_PLAIN.get(cid)
    name = cp.name if cp else str(get(check, "title", "Check"))
    state = str(get(check, "state", ""))
    if state == "COMPUTED" and not plain._no_data(check):
        pill = sev_pill(get(check, "severity"))
        line = plain.numbers_sentence(check, company) or ""
    elif state == "COMPUTED":
        pill = badge(None, "Nothing to check")
        line = "We found no such figures in the company's data, so there was nothing to flag."
    elif state == "NOT_APPLICABLE":
        pill = badge(None, "Left out")
        line = f"This check was left out: {plain._reason_plain(get(check, 'reason'))}."
    else:
        pill = badge(None, "Couldn't check")
        line = f"We couldn't run this check: {plain._reason_plain(get(check, 'reason'))}."
    annot = ""
    for a in as_list(get(check, "annotations")):
        p = plain.annotation_plain(a)
        if p:
            annot = f'<div class="vx-scard-note">{esc(p)}</div>'
            break
    # The explainer right above already shows the name; it stays in the tooltip only.
    return one_line(
        f'<div class="vx vx-checkhead" title="{esc(name)}"><div>'
        + (f'<div class="vx-point-text">{esc(line)}</div>' if line else "")
        + f"{annot}</div>"
        f'<div style="display:flex;gap:6px;align-items:center">{pill}</div></div>'
    )


def _tile_value(key: str, value: Any) -> str:
    shown = fmt_value(key, value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if shown.endswith("×"):
            return f"{float(value):.1f} times"
        if shown.endswith(" d"):
            return f"{round(float(value)):,} days"
    return shown


_RESTATED_PLAIN: dict[str, str] = {
    "NetIncome": "profit",
    "PreTaxIncome": "profit before tax",
    "Tax": "tax",
    "Revenue": "sales",
    "OCF": "cash from the day-to-day business",
    "COGS": "cost of goods sold",
    "AR": "money owed by customers",
    "Inventory": "unsold goods",
    "Assets": "total assets",
}


def context_tiles(checks: Sequence[Any]) -> str:
    tiles = []
    for i, k in enumerate(checks):
        cid = str(get(k, "check_id", ""))
        cp = CHECK_PLAIN.get(cid)
        if cp is None:
            continue
        annots = [str(a) for a in as_list(get(k, "annotations"))]
        state = str(get(k, "state", ""))
        note = ""
        if cid == "X_RESTATED":
            n = sum(1 for a in annots if a.lower().startswith("restated:"))
            computed = state == "COMPUTED"
            shown = (f"{n} revised" if n else "None") if computed else "Not available"
            label = cp.name
            if n:
                names = [
                    _RESTATED_PLAIN.get(a.split(":", 1)[1].strip(), a.split(":", 1)[1].strip())
                    for a in annots
                    if a.lower().startswith("restated:")
                ]
                listed = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
                note = f"Revised after first being reported: {listed}. The revised values are used."
        else:
            key, value = primary_value(k)
            computed = state == "COMPUTED" and value is not None
            shown = _tile_value(key, value) if computed else "Not available"
            approx = any("approximate" in a.lower() for a in annots)
            label = cp.name + (" (approximate)" if approx and computed else "")
            note = next((p for a in annots if (p := plain.annotation_plain(a))), "")
        note_html = f'<div class="vx-scard-note">{esc(note)}</div>' if note else ""
        tiles.append(
            f'<div class="vx-metric-tile" style="--i:{i}"><div class="vx-label">For context · '
            f"not scored</div><b>{esc(shown)}</b>"
            f'<div style="font-size:14px;color:var(--text);margin-bottom:6px">{esc(label)}</div>'
            f"<div class='vx-p'>{esc(cp.question)}</div>{note_html}</div>"
        )
    return one_line(f'<div class="vx vx-metrics">{"".join(tiles)}</div>')


BAND_NOTE_PLAIN: dict[str, str] = {
    "A1_DSO": "Shaded areas show where the value would be flagged, compared with the same "
    "quarter a year earlier. A “big gap” also needs the rise to last (not drawn).",
    "A2_DIO": "Shaded areas show where the value would be flagged, compared with the same "
    "quarter a year earlier. A “big gap” also needs the rise to last (not drawn).",
    "A3_ACCRUALS": "The line shows the gap between profit and cash, as a share of everything "
    "the company owns. Shaded areas show where it would be flagged. The cash-per-$1-of-profit "
    "test isn't drawn here.",
    "A4_CAPRATE": "Shaded areas show where the share of software and research spending "
    "counted as an investment would be flagged: 5 or 10 percentage points above a year "
    "earlier.",
    "A5_RESTRUCT": "No shaded area: this check counts quarters with reorganization costs of at "
    "least 0.5% of that quarter's sales.",
}


# ---------------------------------------------------------------------------
# Executive pay tab
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
            weight_html = '<span class="vx-muted vx-nowrap">not stated</span>'
        cit = m.get("citation")
        rows.append(
            f'<tr><td><span class="vx-metric">{esc(metric_plain(m.get("metric")))}</span></td>'
            f"<td>{esc(context_plain(m.get('context')))}</td><td>{weight_html}</td>"
            f'<td><div style="color:var(--text);margin-bottom:8px">{esc(m.get("sentence") or "")}'
            f"</div>{_source_link(cit)}</td></tr>"
        )
    return one_line(
        '<div class="vx vx-panel" style="padding:8px 10px"><table class="vx-table"><thead><tr>'
        "<th>What bosses are paid for</th><th>Part of the pay plan</th>"
        "<th class='vx-col-weight'>Weight</th><th>Where the report says so</th></tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


def footer(version: str) -> str:
    return one_line(
        '<div class="vx vx-footer"><span class="vx-brand-mark">VERITAS<i>.</i></span>'
        f"<div><div class='vx-p'>{esc(DISCLAIMER_PLAIN)}</div>"
        f'<div style="margin-top:8px" class="vx-p vx-mono">v{esc(version)} · data: the SEC\'s '
        "public database of company reports · rule-based, no AI · every figure links to its "
        "source</div></div></div>"
    )


# ---------------------------------------------------------------------------
# Glossary for the simple view: only terms that appear on it
# ---------------------------------------------------------------------------

GLOSSARY_SIMPLE: dict[str, str] = {
    "SEC": "The U.S. Securities and Exchange Commission: the government agency that public "
    "companies must file their reports with.",
    "Annual report": "The yearly report every U.S. public company files with the SEC. Its "
    "numbers are checked by outside auditors.",
    "Quarterly report": "The report filed after each of the first three quarters of the "
    "year. Its numbers are reviewed by auditors, not fully audited.",
    "Executive pay report": "The document sent to shareholders before the yearly meeting. "
    "It explains how the top executives are paid.",
    "Fine print": "The detailed notes printed under a company's financial statements. They "
    "explain how the numbers were produced and disclose risks such as debts and lawsuits.",
    "Custom “adjusted” figure": "A version of profit or cash that the company defines "
    "itself, leaving out some costs, instead of following the standard accounting rules.",
    "Spare cash (“free cash flow”)": "Cash from running the business, minus what the "
    "company spends on new equipment and buildings.",
    "Pay in company shares": "Paying employees with company stock instead of cash. It "
    "uses no cash today, but it shrinks existing shareholders' slice of the company.",
    "Goodwill": "The extra a company paid when buying another business, above the value of "
    "what it got.",
    "Reorganization costs": "Costs of reshaping the business, such as layoffs, closing sites "
    "or ending contracts. Companies often call them one-time.",
    "Score": "Starts at 100; points come off for each flag. It is a screening aid, not a "
    "rating of the company.",
}
