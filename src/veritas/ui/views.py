"""The four views (PRD §8.4) plus the landing body. They consume only
`AnalysisResult` (PRD §5.1) and read every optional field defensively."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import streamlit as st

from veritas.config import SCORE_NAME, ti
from veritas.models import SEVERITY_RANK
from veritas.ui import charts
from veritas.ui import components as c
from veritas.ui.commands import VIEW_OVERVIEW, VIEW_PROXY, VIEW_RATIOS, VIEW_REDLINE

SEVERITY_FILTERS: tuple[str, ...] = ("ALL", "CRITICAL", "WARNING", "NO FLAG")
_FILTER_TO_SEV = {"CRITICAL": "CRITICAL", "WARNING": "WARNING", "NO FLAG": "NO_FLAG"}
_HARD_OFF = ("error", "timeout", "unavailable")


def _md(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


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
    _md(c.how_it_works())


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
    explain: dict[str, str] = {}
    for f in c.as_list(c.get(mod_b, "findings")):
        for cit in c.as_list(c.get(f, "citations")):
            q = c.get(cit, "quote_raw")
            if q and c.get(f, "explanation"):
                explain.setdefault(str(q), str(c.get(f, "explanation")))
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
                        "explanation": explain.get(str(row.get("current") or ""))
                        or explain.get(str(row.get("prior") or "")),
                    }
                )
    # Scored 10-K edits first, then 10-Q context; latest period first within each.
    edits.sort(key=lambda e: str(e.get("period") or ""), reverse=True)
    edits.sort(key=lambda e: 0 if e["form"] == "10-K" else 1)
    return edits


EDIT_LIMIT = 8


def render_overview(result: Any) -> None:
    mods = _modules(result)
    statuses = {k: _status(mods.get(k)) for k in ("A", "B", "C")}
    score = c.get(result, "score")
    _md(c.score_panel(score, SCORE_NAME, _caps(), statuses))
    _md(c.module_chips(statuses, c.get(score, "score") is not None))
    sections_src = _extra(mods.get("A")).get("sections")
    if isinstance(sections_src, (list, tuple)) and sections_src:
        _md(c.narrative_sources(sections_src))
    for key in ("A", "B", "C"):
        if statuses[key] not in c.ASSESSED_STATUSES:
            mod = mods.get(key)
            fallback = "Proxy not available" if key == "C" else "module result missing"
            _md(c.not_assessed(key, _reason(mod, fallback), statuses[key]))

    st.markdown("")
    with st.container(key="sevbar"):
        st.radio(
            "Severity filter",
            SEVERITY_FILTERS,
            key="severity_filter",
            horizontal=True,
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
    scope = "" if flt == "ALL" else f" Filter: {flt}."

    sections = (
        (
            "Contradictions",
            contra,
            "Management's statement and the numbers for the same period point in different "
            "directions. Latest period first." + scope,
            ("No contradictions", "No paired statement diverges from the numbers at this filter."),
        ),
        (
            "Unaddressed divergences",
            unaddressed,
            "Accounting flags with no matching management statement." + scope,
            ("No unaddressed divergences", "Every flag at this filter has a paired statement."),
        ),
        (
            "Claims consistent with numbers",
            consistent,
            "Statements whose paired screens raised no flag. Absence of a flag is not "
            "verification." + scope,
            (
                "Nothing to show here",
                "No paired claim sits on an unflagged screen at this filter.",
            ),
        ),
    )
    idx = 0
    for title, items, desc, (etitle, ebody) in sections:
        n_ctx = sum(1 for f in items if c.is_context(f))
        n_scored = len(items) - n_ctx
        count: int | str = n_scored if not n_ctx else f"{n_scored} · +{n_ctx} prior-period context"
        _md(c.section_header(title, count, desc))
        if not items:
            _md(c.empty_state(etitle, ebody, glyph="○"))
            continue
        for f in items:
            _md(c.finding_card(f, idx))
            idx += 1

    _render_footnote_summary(mods.get("B"), statuses["B"], flt)


def _render_footnote_summary(mod_b: Any, status: str, flt: str) -> None:
    edits = footnote_edits(mod_b) if status not in _HARD_OFF else []
    hedge = [
        f for f in c.as_list(c.get(mod_b, "findings")) if str(c.get(f, "category")) == "hedging"
    ]
    if flt not in ("ALL", "WARNING"):
        edits, hedge = [], []
    _md(
        c.section_header(
            "Footnote edits",
            len(edits),
            "Material sentence changes in the financial-statement notes (Module B). "
            "10-K pair edits are scored; 10-Q pair edits are context only.",
        )
    )
    if status in _HARD_OFF:
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
        _md(
            c.empty_state(
                "No material footnote edits",
                "No changed note sentence met the material-edit test at this filter.",
                glyph="¶",
            )
        )
        return
    for i, e in enumerate(edits[:EDIT_LIMIT]):
        _md(c.footnote_edit_row(e, i))
    if len(edits) > EDIT_LIMIT:
        _md(
            c.banner(
                f"+{len(edits) - EDIT_LIMIT} more material edits in [2] Redline (type DIFF).",
                kind="info",
                title="More",
            )
        )
    for i, f in enumerate(hedge):
        _md(c.flag_card(f, i))


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
    mod = _modules(result).get("B")
    status = _status(mod)
    _md(
        c.section_header(
            "Footnote Redline",
            None,
            "Year-over-year changes in the financial-statement notes. Original filing text; "
            "comparison is heuristic.",
        )
    )
    if mod is None or status in _HARD_OFF:
        _md(c.not_assessed("B", _reason(mod, "no comparable filing pair"), status))
        return
    if status not in c.ASSESSED_STATUSES:
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
            "Filing pair",
            keys,
            format_func=lambda k: f"{k} pair",
            key="_rl_pair",
            horizontal=True,
        )
    pair = pairs.get(pair_key) if pair_key is not None else None
    if not isinstance(pair, Mapping):
        _md(c.empty_state("Pair unavailable", "This filing pair has no data.", glyph="⇄"))
        return
    prior, current = pair.get("prior"), pair.get("current")
    _md(c.pair_meta(prior, current))

    notes = [n for n in c.as_list(pair.get("notes")) if isinstance(n, Mapping)]
    if not notes:
        _md(
            c.empty_state(
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
                "Note · category notes first",
                list(range(len(notes))),
                format_func=lambda i: _note_label(notes[i]),
                key=f"_rl_note_{pair_key}",
            )
        with col_tog:
            only_material = st.toggle("Material edits only", key=f"_rl_mat_{pair_key}")
        note = notes[note_idx or 0]
        rows = [r for r in c.as_list(note.get("rows")) if isinstance(r, Mapping)]
        counts = note.get("counts")
        _md(c.redline_counts(counts if isinstance(counts, Mapping) else _derived_counts(rows)))
        nstatus = str(note.get("status") or "")
        if nstatus == "TRUNCATED":
            _md(
                c.banner(
                    "This note exceeded the 400-sentence residue cap. It is displayed but not "
                    "scored.",
                    title="Truncated",
                )
            )
        elif nstatus in ("NEW NOTE", "REMOVED NOTE"):
            _md(c.banner(f"{nstatus}: shown for reference, unscored.", title="Unmatched"))
        if only_material:
            rows = [r for r in rows if r.get("material")]
        if not rows:
            _md(
                c.empty_state(
                    "No changed sentences" + (" marked material" if only_material else ""),
                    "Sentences that appear on both sides (after normalizing dates and amounts) "
                    "are hidden.",
                    glyph="=",
                )
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
                _md(c.redline_row(r, i, pl, cl))

    hedging = extra.get("hedging")
    _md(
        c.section_header(
            "Hedging drift",
            None,
            "Hedge words per 1,000 tokens across matched category notes (10-K pair only).",
        )
    )
    if isinstance(hedging, Mapping) and hedging:
        _md(c.hedging_panel(hedging))
    else:
        _md(
            c.empty_state(
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
    mod = _modules(result).get("A")
    status = _status(mod)
    _md(
        c.section_header(
            "Accounting checks",
            None,
            "Last 12 quarters per screen, with the threshold zone from config.py. Hover for exact "
            "values; raw XBRL values below each chart.",
        )
    )
    if mod is None or status in _HARD_OFF:
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
            _md(c.check_header(check))
            fig = charts.check_figure(cid, str(c.get(check, "title", cid)), c.get(check, "history"))
            if fig is not None:
                st.plotly_chart(
                    fig, theme=None, config=charts.PLOT_CONFIG, key=f"chart_{cid}", width="stretch"
                )
                st.caption(charts.band_note(cid))
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
            if rows:
                with st.expander(f"Raw values · {len(rows)} points"):
                    st.dataframe(rows, hide_index=True, column_config=_COLS, key=f"raw_{cid}")

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
            _md(c.flag_card(f, i))
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
