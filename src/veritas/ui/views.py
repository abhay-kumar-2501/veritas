"""The four views (PRD §8.4) plus the landing body. They consume only
`AnalysisResult` (PRD §5.1) and read every optional field defensively."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import streamlit as st

from veritas.config import SCORE_NAME, ti
from veritas.models import SEVERITY_RANK
from veritas.ui import charts, plain, story
from veritas.ui import components as c
from veritas.ui import simple as sv
from veritas.ui.commands import VIEW_OVERVIEW, VIEW_PROXY, VIEW_RATIOS, VIEW_REDLINE

SEVERITY_FILTERS: tuple[str, ...] = ("ALL", "CRITICAL", "WARNING", "NO FLAG")
_FILTER_TO_SEV = {"CRITICAL": "CRITICAL", "WARNING": "WARNING", "NO FLAG": "NO_FLAG"}
_HARD_OFF = ("error", "timeout", "unavailable")


def _md(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def is_simple() -> bool:
    """Simple view is the default; "Analyst detail" (session key `_detail`) turns on
    the technical view (docs/decisions/ui.md D-612)."""
    return not bool(st.session_state.get("_detail", False))


def _modules(result: Any) -> Mapping[str, Any]:
    mods = c.get(result, "modules", {})
    return mods if isinstance(mods, Mapping) else {}


def _status(mod: Any) -> str:
    return str(c.get(mod, "status", "unavailable")) if mod is not None else "unavailable"


def _reason(mod: Any, fallback: str) -> str:
    return c.human_reason(c.get(mod, "reason") or fallback)


def _extra(mod: Any) -> Mapping[str, Any]:
    extra = c.get(mod, "extra", {})
    return extra if isinstance(extra, Mapping) else {}


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------


def render_landing() -> None:
    simple = is_simple()
    _md(story.how_it_works(simple))
    if not simple:
        _md(story.pro_hints())


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def _caps() -> dict[str, int]:
    out: dict[str, int] = {}
    for cat in ("accounting", "footnotes", "incentives", "hedging"):
        try:
            out[cat] = ti(f"CAP_{cat.upper()}")
        except KeyError:
            continue
    return out


def _period_ord(f: Any) -> int:
    d = c.get(c.get(f, "check"), "period_end")
    if d is None:
        claims = c.as_list(c.get(f, "claims"))
        d = c.get(claims[0], "period_end") if claims else None
    return d.toordinal() if hasattr(d, "toordinal") else 0


def _module_a_findings(mods: Mapping[str, Any]) -> list[Any]:
    """Module A findings, latest period first, then severity, then id. Prior-period
    context cards therefore sort below the scored latest-period cards."""
    out = list(c.as_list(c.get(mods.get("A"), "findings")))
    out.sort(
        key=lambda f: (
            1 if c.is_context(f) else 0,
            -_period_ord(f),
            -SEVERITY_RANK.get(str(c.get(f, "severity", "")), -1),
            str(c.get(f, "id", "")),
        )
    )
    return out


def footnote_edits(mod_b: Any) -> list[dict[str, Any]]:
    """Material edits from Module B's redline payload, latest period first.
    10-K pair edits are scored; 10-Q pair edits are shown unscored (§7.5)."""
    pairs = _extra(mod_b).get("pairs")
    if not isinstance(pairs, Mapping):
        return []
    # The engine's explanation for each material edit, keyed by the cited sentence.
    # Keyed by (form, sentence): the same sentence can be added in the 10-K pair and
    # removed in the 10-Q pair, with different explanations.
    explain: dict[tuple[str, str], str] = {}
    for f in c.as_list(c.get(mod_b, "findings")):
        fid = str(c.get(f, "id", ""))
        fform = "10-Q" if "-10-Q-" in fid else "10-K" if "-10-K-" in fid else ""
        for cit in c.as_list(c.get(f, "citations")):
            q = c.get(cit, "quote_raw")
            if q and c.get(f, "explanation"):
                explain.setdefault((fform, str(q)), str(c.get(f, "explanation")))
    edits: list[dict[str, Any]] = []
    for form, pair in pairs.items():
        if not isinstance(pair, Mapping):
            continue
        current = pair.get("current")
        period = c.get(current, "report_date") or c.get(current, "filing_date")
        for note in c.as_list(pair.get("notes")):
            if not isinstance(note, Mapping):
                continue
            for row in c.as_list(note.get("rows")):
                if not isinstance(row, Mapping) or not row.get("material"):
                    continue
                edits.append(
                    {
                        "form": str(form),
                        "period": period,
                        "note": note.get("title") or note.get("key"),
                        "category": note.get("category"),
                        "kind": row.get("kind"),
                        "prior": row.get("prior"),
                        "current": row.get("current"),
                        "softened": row.get("softened"),
                        "explanation": explain.get((str(form), str(row.get("current") or "")))
                        or explain.get((str(form), str(row.get("prior") or ""))),
                    }
                )
    # Scored 10-K edits first, then 10-Q context; latest period first within each.
    edits.sort(key=lambda e: str(e.get("period") or ""), reverse=True)
    edits.sort(key=lambda e: 0 if e["form"] == "10-K" else 1)
    return edits


EDIT_LIMIT = 8


_SECTIONS_PRO = (
    (
        "Where a statement and the numbers sit uneasily",
        "A management statement paired with a flagged measure for the same period. "
        "Latest period first.",
        ("Nothing here", "No paired statement sits next to a flagged measure at this filter."),
    ),
    (
        "Flags with no paired statement",
        "Accounting flags with no matching management statement.",
        ("Nothing here", "Every flag at this filter has a paired statement."),
    ),
    (
        "Statements next to unflagged numbers",
        "Statements whose paired screens raised no flag. Absence of a flag is not verification.",
        ("Nothing to show here", "No paired claim sits on an unflagged screen at this filter."),
    ),
)
_SECTIONS_SIMPLE = (
    (
        "Where what they said and the numbers don't line up",
        "A statement from managers next to a number that raised a flag for the same period. "
        "Newest first.",
        ("Nothing here", "No statement from managers sits next to a flagged number."),
    ),
    (
        "Flags in the numbers",
        "Numbers that raised a flag where we found no matching statement from managers.",
        ("Nothing here", "Every flag shown here has a matching statement from managers."),
    ),
    (
        "Statements where the numbers raised no flag",
        "No flag doesn't mean we confirmed the statement, only that this check found no gap.",
        ("Nothing here", "No statement from managers sits next to an unflagged number."),
    ),
)


def render_overview(result: Any) -> None:
    simple = is_simple()
    mods = _modules(result)
    statuses = {k: _status(mods.get(k)) for k in ("A", "B", "C")}
    score = c.get(result, "score")
    company = plain.short_name(result)
    all_edits = footnote_edits(mods.get("B")) if statuses["B"] not in _HARD_OFF else []
    _md(
        story.verdict_panel(
            plain.verdict(result, all_edits),
            company,
            c.score_band(c.get(score, "score")),
            simple,
        )
    )
    with st.expander("New to this? The terms used below, in plain English"):
        _md(story.glossary_html(sv.GLOSSARY_SIMPLE if simple else None))
    sections_src = _extra(mods.get("A")).get("sections")
    has_sources = isinstance(sections_src, (list, tuple)) and bool(sections_src)
    if simple:
        n_checks = sum(
            1
            for k in c.as_list(c.get(mods.get("A"), "checks"))
            if str(c.get(k, "check_id", "")).startswith("A") and bool(c.get(k, "scored", True))
        )
        _md(sv.score_panel(score, _caps(), statuses, n_checks or None))
        if has_sources:
            _md(sv.sources(sections_src))  # type: ignore[arg-type]
        for key in ("A", "B", "C"):
            if statuses[key] not in c.ASSESSED_STATUSES:
                _md(sv.not_assessed(key, statuses[key]))
    else:
        _md(c.score_panel(score, SCORE_NAME, _caps(), statuses, plain.DEDUCTION_PLAIN))
        _md(c.module_chips(statuses, c.get(score, "score") is not None))
        if has_sources:
            _md(c.narrative_sources(sections_src))  # type: ignore[arg-type]
        for key in ("A", "B", "C"):
            if statuses[key] not in c.ASSESSED_STATUSES:
                mod = mods.get(key)
                fallback = "Proxy not available" if key == "C" else "module result missing"
                _md(c.not_assessed(key, _reason(mod, fallback), statuses[key]))

    st.markdown("")
    _md(story.view_intro(*story.VIEW_INTROS["overview_simple" if simple else "overview"]))
    with st.container(key="sevbar"):
        st.radio(
            "Show" if simple else "Severity filter",
            SEVERITY_FILTERS,
            key="severity_filter",
            horizontal=True,
            format_func=(lambda v: sv.FILTER_PLAIN.get(v, v)) if simple else str,
        )
    flt = str(st.session_state.get("severity_filter") or "ALL")
    findings = _module_a_findings(mods)
    if flt in _FILTER_TO_SEV:
        findings = [f for f in findings if str(c.get(f, "severity")) == _FILTER_TO_SEV[flt]]

    contra = [f for f in findings if c.get(f, "contradiction", False)]
    unaddressed = [
        f
        for f in findings
        if not c.get(f, "contradiction", False)
        and str(c.get(f, "severity")) in ("CRITICAL", "WARNING")
    ]
    consistent = [f for f in findings if str(c.get(f, "severity")) == "NO_FLAG"]
    if flt == "ALL":
        scope = ""
    elif simple:
        scope = f" Showing: {sv.FILTER_PLAIN.get(flt, flt)}."
    else:
        scope = f" Filter: {flt}."

    texts = _SECTIONS_SIMPLE if simple else _SECTIONS_PRO
    idx = 0
    for (title, desc, (etitle, ebody)), items in zip(
        texts, (contra, unaddressed, consistent), strict=True
    ):
        n_ctx = sum(1 for f in items if c.is_context(f))
        n_scored = len(items) - n_ctx
        if not n_ctx:
            count: int | str = n_scored
        elif simple:
            count = f"{n_scored} · +{n_ctx} earlier, for context"
        else:
            count = f"{n_scored} · +{n_ctx} prior-period context"
        _md(c.section_header(title, count, desc + scope))
        if not items:
            _md(c.empty_state(etitle, ebody, glyph="○"))
            continue
        for f in items:
            if simple:
                _md(sv.finding_card(f, idx, company))
            else:
                _md(c.finding_card(f, idx, plain.finding_plain(f, company)))
            idx += 1

    _render_footnote_summary(mods.get("B"), statuses["B"], flt, simple, company)


def _render_footnote_summary(
    mod_b: Any, status: str, flt: str, simple: bool = False, company: str = "This company"
) -> None:
    edits = footnote_edits(mod_b) if status not in _HARD_OFF else []
    hedge = [
        f for f in c.as_list(c.get(mod_b, "findings")) if str(c.get(f, "category")) == "hedging"
    ]
    if flt not in ("ALL", "WARNING"):
        edits, hedge = [], []
    if simple:
        _md(
            c.section_header(
                "Changes to the fine print",
                len(edits),
                "Sentences the company added, removed or reworded in the detailed notes to its "
                "accounts. Red crossed-out text was removed; green text was added. We show "
                "notable changes: sentences in key sections (such as revenue, debt, legal risks "
                "or dealings with insiders) that use one of the accounting terms we watch for "
                "and were added, removed, made less definite or heavily reworded. Changes in "
                "the annual report count toward the score; those in quarterly reports are "
                "shown for context.",
            )
        )
    else:
        _md(
            c.section_header(
                "Footnote edits",
                len(edits),
                "Material sentence changes in the financial-statement notes (Module B). "
                "10-K pair edits are scored; 10-Q pair edits are context only.",
            )
        )
    if status in _HARD_OFF:
        if simple:
            _md(sv.not_assessed("B", status))
        else:
            _md(
                c.empty_state(
                    "Footnote Redline not assessed",
                    "Module B did not run for this filer; see the panel above.",
                    glyph="B",
                    kind="is-na",
                )
            )
        return
    if not edits and not hedge:
        if simple:
            _md(
                c.empty_state(
                    "No notable changes",
                    "No changed sentence met our test for a notable change.",
                    glyph="¶",
                )
            )
        else:
            _md(
                c.empty_state(
                    "No material footnote edits",
                    "No changed note sentence met the material-edit test at this filter.",
                    glyph="¶",
                )
            )
        return
    for i, e in enumerate(edits[:EDIT_LIMIT]):
        if simple:
            _md(sv.edit_row(e, i))
        else:
            _md(c.footnote_edit_row(e, i, plain.edit_plain(e)))
    if len(edits) > EDIT_LIMIT:
        more = len(edits) - EDIT_LIMIT
        _md(
            c.banner(
                f"+{more} more notable changes on the Fine-print changes tab."
                if simple
                else f"+{more} more material edits in [2] Redline (type DIFF).",
                kind="info",
                title="More",
            )
        )
    for i, f in enumerate(hedge):
        _md(sv.flag_card(f, i, company) if simple else c.flag_card(f, i))


# ---------------------------------------------------------------------------
# Redline
# ---------------------------------------------------------------------------


def _note_sort_key(note: Mapping[str, Any]) -> tuple[int, int, str]:
    cat = str(note.get("category") or "").lower()
    is_cat = 0 if cat and cat not in ("other", "none", "display") else 1
    counts = note.get("counts") if isinstance(note.get("counts"), Mapping) else {}
    material = counts.get("material") if isinstance(counts, Mapping) else 0
    mat = int(material) if isinstance(material, (int, float)) else 0
    return (is_cat, -mat, str(note.get("title") or note.get("key") or ""))


def _note_label(note: Mapping[str, Any]) -> str:
    title = str(note.get("title") or note.get("key") or "Untitled note")
    cat = note.get("category")
    counts = note.get("counts") if isinstance(note.get("counts"), Mapping) else {}
    mat = counts.get("material") if isinstance(counts, Mapping) else None
    bits = [title]
    # Skip the category when the title already says it ("Contingencies", or
    # "Commitments and Contingencies" for category "contingencies").
    if cat and c.human_text(cat).strip().lower() not in title.lower():
        bits.append(c.human_text(cat))
    if mat:
        bits.append(f"{mat} material")
    status = str(note.get("status") or "")
    if status and status != "MATCHED":
        bits.append(status.lower())
    return " · ".join(bits)


def _derived_counts(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    out = {"inserted": 0, "deleted": 0, "modified": 0, "softened": 0, "material": 0}
    for r in rows:
        k = str(r.get("kind") or "").lower()
        if k in out:
            out[k] += 1
        if r.get("softened"):
            out["softened"] += 1
        if r.get("material"):
            out["material"] += 1
    return out


def render_redline(result: Any) -> None:
    simple = is_simple()
    mod = _modules(result).get("B")
    status = _status(mod)
    if simple:
        _md(
            c.section_header(
                "Changes to the fine print",
                None,
                "This year's fine print compared with last year's, sentence by sentence. The "
                "matching is automatic and can miss or mismatch sentences.",
            )
        )
    else:
        _md(
            c.section_header(
                "Footnote Redline",
                None,
                "Year-over-year changes in the financial-statement notes. Original filing "
                "text; comparison is heuristic.",
            )
        )
    _md(story.view_intro(*story.VIEW_INTROS["redline"]))
    if mod is None or status in _HARD_OFF:
        if simple:
            _md(sv.not_assessed("B", status))
        else:
            _md(c.not_assessed("B", _reason(mod, "no comparable filing pair"), status))
        return
    if status not in c.ASSESSED_STATUSES:
        if simple:
            _md(
                c.banner(
                    f"This part {sv.STATUS_PLAIN.get(status, 'was only partly checked')}. "
                    "Results are shown for reference and don't count toward the score.",
                    title="Partial",
                )
            )
        else:
            _md(
                c.banner(
                    f"Module B status: {c.STATUS_TEXT.get(status, status)}. "
                    + _reason(mod, "Results shown for reference; not scored."),
                    title="Partial",
                )
            )
    extra = _extra(mod)
    pairs = extra.get("pairs")
    if not isinstance(pairs, Mapping) or not pairs:
        _md(
            c.empty_state(
                "Nothing to compare",
                "We need two reports of the same kind a year apart (this year's annual report "
                "and last year's, or the same quarter a year apart).",
                glyph="⇄",
            )
            if simple
            else c.empty_state(
                "No comparable filing pair",
                "A redline needs two filings of the same form one year apart (10-K vs prior "
                "10-K, or the same fiscal quarter's 10-Q).",
                glyph="⇄",
            )
        )
        return
    order = {"10-K": 0, "10-Q": 1}
    keys = sorted((str(k) for k in pairs), key=lambda k: (order.get(k, 9), k))
    with st.container(key="seg"):
        pair_key = st.radio(
            "Reports to compare" if simple else "Filing pair",
            keys,
            format_func=(lambda k: sv.PAIR_PLAIN.get(k, k)) if simple else (lambda k: f"{k} pair"),
            key="_rl_pair",
            horizontal=True,
        )
    pair = pairs.get(pair_key) if pair_key is not None else None
    if not isinstance(pair, Mapping):
        _md(c.empty_state("Pair unavailable", "This filing pair has no data.", glyph="⇄"))
        return
    prior, current = pair.get("prior"), pair.get("current")
    _md(sv.pair_meta(prior, current) if simple else c.pair_meta(prior, current))

    notes = [n for n in c.as_list(pair.get("notes")) if isinstance(n, Mapping)]
    if not notes:
        _md(
            c.empty_state(
                "No sections to compare",
                "We couldn't match up any sections of the fine print between the two reports.",
                glyph="¶",
            )
            if simple
            else c.empty_state(
                "No notes segmented",
                "No financial-statement notes could be matched for this pair.",
                glyph="¶",
            )
        )
    else:
        notes.sort(key=_note_sort_key)
        col_sel, col_tog = st.columns([3, 1], vertical_alignment="bottom")
        with col_sel:
            note_idx = st.selectbox(
                "Section of the fine print" if simple else "Note · category notes first",
                list(range(len(notes))),
                format_func=(
                    (lambda i: sv.note_label(notes[i]))
                    if simple
                    else (lambda i: _note_label(notes[i]))
                ),
                key=f"_rl_note_{pair_key}",
            )
        with col_tog:
            only_material = st.toggle(
                "Notable changes only" if simple else "Material edits only",
                value=True,
                key=f"_rl_mat_{pair_key}",
            )
        note = notes[note_idx or 0]
        rows = [r for r in c.as_list(note.get("rows")) if isinstance(r, Mapping)]
        counts = note.get("counts")
        cnt = counts if isinstance(counts, Mapping) else _derived_counts(rows)
        _md(sv.redline_counts(cnt) if simple else c.redline_counts(cnt))
        nstatus = str(note.get("status") or "")
        if nstatus == "TRUNCATED":
            _md(
                c.banner(
                    "This section is too long to compare fully. It's shown, but doesn't count "
                    "toward the score."
                    if simple
                    else "This note exceeded the 400-sentence residue cap. It is displayed but "
                    "not scored.",
                    title="Too long" if simple else "Truncated",
                )
            )
        elif nstatus in ("NEW NOTE", "REMOVED NOTE"):
            if simple:
                what = "new this year" if nstatus == "NEW NOTE" else "dropped this year"
                _md(
                    c.banner(
                        f"This section is {what}, so there's nothing to compare it with. It "
                        "doesn't count toward the score.",
                        title="Unmatched",
                    )
                )
            else:
                _md(c.banner(f"{nstatus}: shown for reference, unscored.", title="Unmatched"))
        if only_material:
            rows = [r for r in rows if r.get("material")]
        if not rows:
            if simple:
                _md(
                    c.empty_state(
                        "No changed sentences" + (" marked notable" if only_material else ""),
                        "Sentences that are the same in both years are hidden (changed dates "
                        "and amounts are ignored).",
                        glyph="=",
                    )
                )
            else:
                _md(
                    c.empty_state(
                        "No changed sentences" + (" marked material" if only_material else ""),
                        "Sentences that appear on both sides (after normalizing dates and "
                        "amounts) are hidden.",
                        glyph="=",
                    )
                )
        else:
            if simple:
                pl = (
                    f"Before · {sv.form_plain(c.get(prior, 'form', '')).lower()} filed "
                    f"{sv.short_date(c.get(prior, 'filing_date'))}"
                )
                cl = (
                    f"After · {sv.form_plain(c.get(current, 'form', '')).lower()} filed "
                    f"{sv.short_date(c.get(current, 'filing_date'))}"
                )
            else:
                pl = (
                    f"Prior · {c.get(prior, 'form', '')} filed "
                    f"{c.fmt_date(c.get(prior, 'filing_date'))}"
                )
                cl = (
                    f"Current · {c.get(current, 'form', '')} filed "
                    f"{c.fmt_date(c.get(current, 'filing_date'))}"
                )
            _md(c.diff_head(pl, cl))
            for i, r in enumerate(rows):
                _md(sv.redline_row(r, i) if simple else c.redline_row(r, i, pl, cl))

    hedging = extra.get("hedging")
    if simple:
        _md(
            c.section_header(
                "Cautious wording",
                None,
                "How often the fine print uses cautious words like “may” or “could”, per "
                "1,000 words, this year compared with last year (annual reports only).",
            )
        )
    else:
        _md(
            c.section_header(
                "Hedging drift",
                None,
                "Hedge words per 1,000 tokens across matched category notes (10-K pair only).",
            )
        )
    if isinstance(hedging, Mapping) and hedging:
        _md(sv.hedging_panel(hedging) if simple else c.hedging_panel(hedging))
    else:
        _md(
            c.empty_state(
                "Not enough text to compare",
                "We need at least 1,500 words of comparable fine print in both annual reports.",
                glyph="~",
            )
            if simple
            else c.empty_state(
                "Hedging drift not computed",
                "Needs a 10-K pair with at least 1,500 tokens of category-note text per side.",
                glyph="~",
            )
        )


# ---------------------------------------------------------------------------
# Ratios
# ---------------------------------------------------------------------------


def _history_rows(check: Any) -> list[dict[str, Any]]:
    cits = c.as_list(c.get(check, "citations"))
    key = c.PRIMARY_KEY.get(str(c.get(check, "check_id", "")), "")
    rows: list[dict[str, Any]] = []
    for h in c.as_list(c.get(check, "history")):
        if not isinstance(h, Mapping):
            continue
        period = str(h.get("period_end") or "")
        # Only a citation filed for this very period may stand next to its value.
        cit = next((x for x in cits if c.fmt_date(c.get(x, "report_date")) == period), None)
        url = h.get("url") or c.get(cit, "url")
        rows.append(
            {
                "Period end": period,
                "Value": c.fmt_value(key, h.get("value")),
                "Raw": h.get("value"),
                "XBRL tag": h.get("xbrl_tag") or h.get("tag") or c.get(cit, "xbrl_tag"),
                "Accession": h.get("accession") or c.get(cit, "accession"),
                "Filing": url if c.safe_url(url) else None,
                "Derived": h.get("derived"),
                "Restated": h.get("restated"),
            }
        )
    return rows


_COLS = {
    "Filing": st.column_config.LinkColumn("Filing", display_text="open ↗"),
    "Raw": st.column_config.NumberColumn("Raw", format="%.6g"),
    "Derived": st.column_config.CheckboxColumn("Derived"),
    "Restated": st.column_config.CheckboxColumn("Restated"),
}


def render_ratios(result: Any) -> None:
    simple = is_simple()
    company = plain.short_name(result)
    mod = _modules(result).get("A")
    status = _status(mod)
    if simple:
        _md(
            c.section_header(
                "Trends over time",
                None,
                "Each check over the last three years. Tap or hover over a line to see exact "
                "values.",
            )
        )
    else:
        _md(
            c.section_header(
                "Accounting checks",
                None,
                "Last 12 quarters per screen, with the threshold zone from config.py. Hover for "
                "exact values; raw XBRL values below each chart.",
            )
        )
    _md(story.view_intro(*story.VIEW_INTROS["ratios_simple" if simple else "ratios"]))
    if mod is None or status in _HARD_OFF:
        if simple:
            _md(sv.not_assessed("A", status))
        else:
            _md(c.not_assessed("A", _reason(mod, "module result missing"), status))
        return
    checks = c.as_list(c.get(mod, "checks"))
    scored = [k for k in checks if not str(c.get(k, "check_id", "")).startswith("X_")]
    display = [k for k in checks if str(c.get(k, "check_id", "")).startswith("X_")]
    scored.sort(key=lambda k: str(c.get(k, "check_id", "")))
    if not scored:
        _md(c.empty_state("No accounting checks", "Module A returned no checks.", glyph="∅"))
    for check in scored:
        cid = str(c.get(check, "check_id", "check"))
        with st.container(key=f"ratio_{cid}"):
            _md(story.check_explainer(cid))
            cp = plain.CHECK_PLAIN.get(cid)
            if simple:
                _md(sv.check_header(check, company))
                chart_title = cp.name if cp else str(c.get(check, "title", cid))
            else:
                _md(c.check_header(check))
                chart_title = str(c.get(check, "title", cid))
            fig = charts.check_figure(cid, chart_title, c.get(check, "history"), simple)
            if fig is not None:
                st.plotly_chart(
                    fig, theme=None, config=charts.PLOT_CONFIG, key=f"chart_{cid}", width="stretch"
                )
                st.caption(
                    sv.BAND_NOTE_PLAIN.get(cid, "No flag zone for this measure.")
                    if simple
                    else charts.band_note(cid)
                )
            elif simple:
                pass  # the header line already says why there is no chart
            else:
                state = str(c.get(check, "state", ""))
                reason = c.human_reason(c.get(check, "reason")) or None
                if state == "NOT_APPLICABLE":
                    title = f"Not applicable: {reason or 'gate not met'}"
                    body = "Gated out for this issuer, so it is excluded from coverage."
                elif state == "NOT_COMPUTABLE":
                    title = f"Not computable: {reason or 'inputs missing'}"
                    body = (
                        "Missing inputs stay missing: nothing is estimated or filled in. "
                        "This check counts against coverage."
                    )
                else:
                    title = "Not enough history to chart"
                    body = "Fewer than two quarters with a value; the latest result is above."
                _md(
                    c.empty_state(
                        title,
                        body,
                        glyph="—",
                        kind="is-na",
                    )
                )
            rows = _history_rows(check)
            if rows and not simple:
                with st.expander(f"Raw values · {len(rows)} points"):
                    st.dataframe(rows, hide_index=True, column_config=_COLS, key=f"raw_{cid}")

    if simple:
        _md(
            c.section_header(
                "Extra context",
                None,
                "A few more numbers that help read the checks above. They never affect the score.",
            )
        )
        if display:
            _md(sv.context_tiles(display))
        return
    _md(
        c.section_header(
            "Display-only metrics",
            len(display),
            "Context for the screens above. Shown, never scored.",
        )
    )
    if display:
        _md(c.display_metrics(display))
    else:
        _md(c.empty_state("No display-only metrics", "None were computed.", glyph="∅"))

    metrics = c.get(result, "metrics", {})
    if isinstance(metrics, Mapping) and metrics:
        series_rows: list[dict[str, Any]] = []
        cik = c.get(result, "cik")
        for key, series in sorted(metrics.items(), key=lambda kv: str(kv[0])):
            for p in c.as_list(c.get(series, "points")):
                acc = str(c.get(p, "accession", ""))
                url = (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
                    if cik and acc
                    else None
                )
                series_rows.append(
                    {
                        "Series": str(key),
                        "Period end": c.fmt_date(c.get(p, "end")),
                        "Value": (
                            c.fmt_money(float(c.get(p, "value")))
                            if str(c.get(p, "unit", "")) == "USD"
                            and isinstance(c.get(p, "value"), (int, float))
                            else c.fmt_num(c.get(p, "value"))
                        ),
                        "Raw": c.get(p, "value"),
                        "Unit": c.get(p, "unit"),
                        "XBRL tag": c.get(p, "xbrl_tag"),
                        "Accession": acc or None,
                        "Form": c.get(p, "form"),
                        "Filed": c.fmt_date(c.get(p, "filed")),
                        "Filing": url,
                        "Derived": bool(c.get(p, "derived", False)),
                        "Restated": bool(c.get(p, "restated", False)),
                    }
                )
        with st.expander(
            f"Underlying XBRL series · {len(metrics)} series, {len(series_rows)} points"
        ):
            st.dataframe(series_rows, hide_index=True, column_config=_COLS, key="raw_series")


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------


def render_proxy(result: Any) -> None:
    if is_simple():
        _render_proxy_simple(result)
        return
    mod = _modules(result).get("C")
    status = _status(mod)
    extra = _extra(mod)
    source = extra.get("source")
    _md(
        c.section_header(
            "Proxy Incentive X-Ray",
            None,
            "Which metrics drive executive pay"
            + (f" · source: {source}" if source else "")
            + ". Detection is heuristic; weights are parsed only from weight tables.",
        )
    )
    _md(story.view_intro(*story.VIEW_INTROS["proxy"]))
    if mod is None or status in _HARD_OFF:
        _md(c.not_assessed("C", _reason(mod, "Proxy not available"), status))
        return
    if status == "low_confidence":
        _md(
            c.empty_state(
                "LOW CONFIDENCE: " + _reason(mod, "fewer than 2 incentive metrics detected"),
                "Pay metrics may live in images or charts the parser cannot read. Extracted text "
                "is shown below; no flags are raised and nothing is deducted.",
                glyph="◐",
                kind="is-na",
            )
        )
    metrics = [m for m in c.as_list(extra.get("metrics")) if isinstance(m, Mapping)]
    _md(c.section_header("Incentive metrics", len(metrics), "Metric · plan context · weight."))
    if metrics:
        _md(c.proxy_table(metrics))
    else:
        _md(
            c.empty_state(
                "No incentive metrics detected",
                "No metric alias appeared in an incentive context in the CD&A span.",
                glyph="∅",
            )
        )
    excerpt = extra.get("cda_text") or extra.get("text") or extra.get("excerpt")
    if isinstance(excerpt, str) and excerpt.strip():
        words = len(excerpt.split())
        with st.expander(
            f"Extracted CD&A text · {words:,} words", expanded=status == "low_confidence"
        ):
            _md(c.text_block(excerpt))
    elif status == "low_confidence":
        _md(
            c.empty_state(
                "No CD&A text was extracted",
                "The compensation discussion could not be located as text in this filing.",
                glyph="¶",
            )
        )

    findings = c.as_list(c.get(mod, "findings"))
    _md(c.section_header("Flags", len(findings), "C1 SBC vs non-GAAP pay · C2 goodwill."))
    if status == "low_confidence":
        _md(c.empty_state("No flags at low confidence", "Flags are suppressed.", glyph="○"))
    elif findings:
        for i, f in enumerate(findings):
            _md(c.flag_card(f, i, plain.incentive_plain(f)))
    else:
        _md(c.empty_state("No incentive flags raised", "Neither C1 nor C2 fired.", glyph="○"))
    notes = c.as_list(extra.get("notes"))
    if status == "low_confidence":
        # The panel above already states the reason; do not repeat it as a note.
        shown = _reason(mod, "").strip().lower().rstrip(".")
        notes = [
            n
            for n in notes
            if not shown
            or (shown not in str(n).lower() and str(n).lower().rstrip(".") not in shown)
        ]
    if notes:
        _md(c.notes_list(notes))


def _pay_checks_plain(extra: Mapping[str, Any]) -> str:
    """Why no pay flag was raised, per check: ran and found nothing, didn't apply, or
    couldn't run (extra["checks"] from the engine)."""
    checks = extra.get("checks") if isinstance(extra.get("checks"), Mapping) else {}
    names = {
        "C1": "The check on custom “adjusted” pay targets",
        "C2": "The check on pay tied to sales or deals",
    }
    parts = []
    for cid, name in names.items():
        st_ = str((checks.get(cid) or {}).get("state", "")) if isinstance(checks, Mapping) else ""
        if st_ == "NOT_APPLICABLE":
            parts.append(f"{name} didn't apply (no such pay target found).")
        elif st_ == "NOT_COMPUTABLE":
            parts.append(f"{name} couldn't be run (figures missing).")
        elif st_ == "NO_FLAG":
            parts.append(f"{name} ran and raised no flag.")
    return " ".join(parts) or "Neither pay check raised a flag."


def _render_proxy_simple(result: Any) -> None:
    company = plain.short_name(result)
    mod = _modules(result).get("C")
    status = _status(mod)
    extra = _extra(mod)
    _md(
        c.section_header(
            "Executive pay",
            None,
            "What the top bosses' bonuses depend on, read from the company's yearly pay report. "
            "Reading is automatic and can miss or mismatch things, especially in charts or "
            "tables.",
        )
    )
    _md(story.view_intro(*story.VIEW_INTROS["proxy_simple"]))
    if mod is None or status in _HARD_OFF:
        _md(sv.not_assessed("C", status))
        return
    low = status == "low_confidence"
    if low:
        _md(
            c.empty_state(
                "We couldn't read the pay details reliably",
                "Pay targets are often shown in charts or images, which we can't read. What we "
                "could read is shown below. Nothing on this tab affects the score.",
                glyph="◐",
                kind="is-na",
            )
        )
    metrics = [m for m in c.as_list(extra.get("metrics")) if isinstance(m, Mapping)]
    _md(
        c.section_header(
            "What their pay depends on",
            len(metrics),
            "Each row is a place where the report ties pay to a result. “Weight” is how much "
            "of the plan depends on it, when the report states that in a table.",
        )
    )
    if metrics:
        _md(sv.proxy_table(metrics))
    else:
        _md(
            c.empty_state(
                "Nothing found",
                "We didn't find any of the pay targets we look for in the report's text.",
                glyph="∅",
            )
        )
    excerpt = extra.get("cda_text") or extra.get("text") or extra.get("excerpt")
    if isinstance(excerpt, str) and excerpt.strip():
        words = len(excerpt.split())
        with st.expander(f"What the pay report says · {words:,} words", expanded=low):
            _md(c.text_block(excerpt))
    findings = c.as_list(c.get(mod, "findings"))
    _md(
        c.section_header(
            "Flags about pay",
            0 if low else len(findings),
            "We flag pay targets that could be lifted by paying staff in company shares or by "
            "buying other businesses.",
        )
    )
    if low:
        _md(
            c.empty_state(
                "No flags",
                "We don't raise pay flags when we couldn't read the pay details reliably.",
                glyph="○",
            )
        )
    elif findings:
        for i, f in enumerate(findings):
            _md(sv.flag_card(f, i, company))
    else:
        _md(c.empty_state("No flags", _pay_checks_plain(extra), glyph="○"))


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_RENDERERS = {
    VIEW_OVERVIEW: render_overview,
    VIEW_REDLINE: render_redline,
    VIEW_RATIOS: render_ratios,
    VIEW_PROXY: render_proxy,
}


def render_view(view: str, result: Any) -> None:
    _RENDERERS.get(view, render_overview)(result)
