"""Page shell: state, header, command handling, analysis runner, footer.

Session-state keys required by PRD §8.1: ticker, view, result, severity_filter,
pdf_bytes, cmd_error. Internal helper keys are prefixed with "_" and never hold
raw HTML or filing text (only small dicts/strings).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import streamlit as st

from veritas.config import (
    APP_VERSION,
    DISCLAIMER,
    PROJECT_ROOT,
    SEED_TICKERS,
    Settings,
    get_settings,
)
from veritas.ui import components as c
from veritas.ui import simple as sv
from veritas.ui import story, views
from veritas.ui.commands import (
    VALID_COMMANDS,
    VIEW_LABELS,
    VIEW_OVERVIEW,
    Command,
    parse_command,
)

CSS_PATH = Path(__file__).with_name("styles.css")
METHODOLOGY_PATH = PROJECT_ROOT / "docs" / "METHODOLOGY.md"

STAGES: tuple[str, ...] = (
    "Resolve CIK",
    "Fetch submissions",
    "Fetch company facts",
    "Fetch filings",
    "Module A",
    "Module B",
    "Module C",
    "Score",
)
_STAGE_HINTS: tuple[tuple[str, int], ...] = (
    ("resolve", 0),
    ("cik", 0),
    ("submission", 1),
    ("companyfacts", 2),
    ("company facts", 2),
    ("facts", 2),
    ("filings", 3),
    ("fetch filing", 3),
    ("module a", 4),
    ("narrative", 4),
    ("module b", 5),
    ("footnote", 5),
    ("module c", 6),
    ("proxy", 6),
    ("score", 7),
)

STATE_DEFAULTS: dict[str, Any] = {
    "ticker": None,
    "view": VIEW_OVERVIEW,
    "result": None,
    "severity_filter": "ALL",
    "pdf_bytes": None,
    "cmd_error": None,
}

KNOWN_LIMITATIONS = (
    "Heuristic text matching: it misses claims phrased unusually and can mis-pair sentences.",
    "Not modelled: FX effects, LIFO, receivables factoring/securitization, segment mix, "
    "dimensional XBRL data, company-specific extension tags.",
    "53-week fiscal years make one quarter about 7% longer; DSO and DIO adjust via days(q), "
    "growth-based metrics do not.",
    "Restatements use as-restated values; original values are flagged, not used.",
    "Proxy metrics that appear only in images or charts are not detected (LOW_CONFIDENCE).",
    "Financial issuers, foreign private issuers, funds and shells are out of scope or limited.",
    "The score is uncalibrated and not comparable across sectors.",
)


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def sample_mode() -> bool:
    """Dev escape hatch: VERITAS_UI_SAMPLE=1 renders the synthetic fixture."""
    return os.environ.get("VERITAS_UI_SAMPLE", "0").strip() == "1"


def load_sample() -> Any:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from tests.fixtures.sample_result import sample_result

    return sample_result()


def inject_css() -> None:
    try:
        css = CSS_PATH.read_text(encoding="utf-8")
    except OSError:
        return
    st.html(f"<style>{css}</style>")


def init_state() -> None:
    for key, value in STATE_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if st.session_state.get("view") not in VIEW_LABELS:
        st.session_state["view"] = VIEW_OVERVIEW


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

RESULT_TTL_S = 3600.0
RESULT_STORE_MAX = 32
_NETWORK_ERRORS = frozenset(
    {"SecBlocked", "SecUnavailable", "SecHTTPError", "ConnectionError", "TimeoutError"}
)
_log = logging.getLogger(__name__)

StoreKey = tuple[str, bool, bool]


@st.cache_resource(show_spinner=False)
def _result_store() -> dict[StoreKey, tuple[float, Any]]:
    """Process-wide result store (docs/decisions/ui.md D-607).

    Deliberately NOT st.cache_data: the pipeline reports progress through a
    callback that updates Streamlit elements, and st.cache_data replays element
    calls on a cache hit, which raises CacheReplayClosureError. The store only
    holds finished AnalysisResult objects; analyze() always runs outside any
    cached function."""
    return {}


_STORE_LOCK = threading.Lock()


def store_get(key: StoreKey) -> Any | None:
    now = time.monotonic()
    with _STORE_LOCK:
        hit = _result_store().get(key)
        if hit is None:
            return None
        stamp, value = hit
        if now - stamp > RESULT_TTL_S:
            _result_store().pop(key, None)
            return None
        return value


def store_put(key: StoreKey, value: Any) -> None:
    with _STORE_LOCK:
        store = _result_store()
        store[key] = (time.monotonic(), value)
        while len(store) > RESULT_STORE_MAX:
            oldest = min(store, key=lambda k: store[k][0])
            store.pop(oldest, None)


def clear_store() -> None:
    with _STORE_LOCK:
        _result_store().clear()


def _call_pipeline(
    target: str, offline: bool, refresh: bool, progress: Callable[[str], None]
) -> Any:
    from veritas.pipeline import analyze  # lazy: imported only when needed

    if refresh:  # ask the pipeline to bypass its own HTTP cache too
        return analyze(target, offline=offline, refresh=True, progress=progress)
    return analyze(target, offline=offline, progress=progress)


def analyze_target(
    target: str,
    offline: bool,
    refresh: bool,
    progress: Callable[[str], None],
    sample: bool = False,
) -> tuple[Any, bool]:
    """Return (result, from_store). Keyed by (target, offline, refresh) with a
    1-hour TTL; a refresh also replaces the ordinary entry."""
    key: StoreKey = (target.upper(), offline, refresh)
    if not refresh:
        hit = store_get(key)
        if hit is not None:
            return hit, True
    if sample:
        for stage in STAGES:
            progress(stage)
        result = load_sample()
    else:
        result = _call_pipeline(target, offline, refresh, progress)
    store_put(key, result)
    if refresh:
        store_put((target.upper(), offline, False), result)
    return result, False


def _stage_index(message: str, current: int) -> int:
    low = message.strip().lower()
    for i, name in enumerate(STAGES):
        if low.startswith(name.lower()):
            return i
    for hint, idx in _STAGE_HINTS:
        if hint in low:
            return max(idx, current)  # progress never moves backwards
    return current


def _is_network_error(exc: BaseException) -> bool:
    for cls in type(exc).__mro__:
        if cls.__name__ in _NETWORK_ERRORS or cls.__module__.split(".")[0] == "requests":
            return True
    return False


def classify_error(exc: BaseException, display: str) -> dict[str, Any]:
    """Map pipeline exceptions to the §2.3 messages."""
    name = type(exc).__name__
    if name == "TickerNotFound":
        sugg = [str(s) for s in (getattr(exc, "suggestions", None) or [])][:5]
        return {
            "title": f"We couldn't find “{display}”",
            "message": "Check the stock symbol (for example AAPL for Apple). Companies that "
            "changed their symbol or stopped trading aren't supported."
            + (" Did you mean one of these?" if sugg else ""),
            "suggestions": sugg,
            "retry": False,
            "glyph": "?",
        }
    if name == "UnsupportedFiler":
        reason = str(getattr(exc, "reason", None) or "This filer type is not covered.")
        return {
            "title": "VERITAS can't check this kind of company yet",
            "message": "It works with U.S. companies that file standard annual and quarterly "
            f"reports. (Technical reason: {reason.rstrip('.')}.)",
            "suggestions": [],
            "retry": False,
            "glyph": "⊘",
        }
    if name == "NoXBRL":
        return {
            "title": "This company's numbers aren't available in a form we can read",
            "message": "The SEC has no machine-readable figures for this company, so the "
            "number checks can't run.",
            "suggestions": [],
            "retry": False,
            "glyph": "⊘",
        }
    if isinstance(exc, ImportError):
        return {
            "title": "Analysis engine not available in this build",
            "message": "The pipeline module (veritas.pipeline) could not be imported. To preview "
            "the interface with synthetic data, start the app with VERITAS_UI_SAMPLE=1.",
            "suggestions": [],
            "retry": False,
            "glyph": "⚙",
        }
    if _is_network_error(exc):
        return {
            "title": "We couldn't reach the SEC's website",
            "message": "It may be busy or briefly unavailable, and we don't have a saved copy of "
            "this company yet. Try again in a few minutes.",
            "suggestions": [],
            "retry": True,
            "glyph": "↻",
        }
    return {
        "title": "The analysis could not be completed",
        "message": f"Something went wrong while checking “{display}”. Try again, or start "
        "with one of the example companies.",
        "suggestions": [],
        "retry": True,
        "glyph": "↻",
    }


def queue_analysis(target: str, display: str | None = None, refresh: bool = False) -> None:
    st.session_state["_pending"] = {
        "target": target,
        "display": display or target,
        "refresh": refresh,
    }
    st.session_state["cmd_error"] = None
    st.session_state["_run_error"] = None


def _live_blocked(settings: Settings, target: str, sample: bool) -> bool:
    return (not sample) and settings.user_agent is None and target not in SEED_TICKERS


def pending_blocked(settings: Settings, sample: bool) -> bool:
    """Resolve the live-mode gate before anything renders, so a blocked request
    never hides the current page."""
    pending = st.session_state.get("_pending")
    if not pending:
        return False
    target = str(pending.get("target", ""))
    display = str(pending.get("display", target))
    if not _live_blocked(settings, target, sample):
        return False
    st.session_state.pop("_pending", None)
    st.session_state["_run_error"] = {
        "title": "Live lookups are off: example companies only",
        "message": "This copy of VERITAS isn't set up for live lookups, so only the built-in "
        f"examples ({', '.join(SEED_TICKERS)}) work. To look up “{display}”, the site owner "
        "needs to set VERITAS_SEC_USER_AGENT (a contact name and e-mail for the SEC).",
        "suggestions": list(SEED_TICKERS),
        "retry": False,
        "glyph": "⏻",
    }
    return True


def apply_result(result: Any, display: str) -> None:
    st.session_state["result"] = result
    st.session_state["ticker"] = c.get(result, "ticker", display)
    st.session_state["view"] = VIEW_OVERVIEW
    st.session_state["severity_filter"] = "ALL"
    st.session_state["pdf_bytes"] = None
    st.session_state["_pdf_name"] = None
    st.session_state["_run_error"] = None


_HIDE_BODY = "<style>.st-key-body, .st-key-landing { display: none !important; }</style>"


def run_pending(settings: Settings, sample: bool) -> None:
    """Run a queued analysis in this same script run (no st.rerun), so the page
    never shows an intermediate frame. A store hit renders nothing transient."""
    pending = st.session_state.pop("_pending", None)
    if not pending:
        return
    target = str(pending.get("target", ""))
    display = str(pending.get("display", target))
    refresh = bool(pending.get("refresh"))
    offline = False if refresh else (settings.offline or not settings.live_enabled)

    if not refresh:
        hit = store_get((target.upper(), offline, False))
        if hit is not None:
            apply_result(hit, display)
            return

    result: Any = None
    error: BaseException | None = None
    slot = st.empty()
    with slot.container():
        # Hide the previous page while the pipeline runs: Streamlit keeps stale
        # elements on screen until the run ends (they would show as ghost
        # fragments). Rendered via Markdown so emptying the slot removes it.
        st.markdown(_HIDE_BODY, unsafe_allow_html=True)
        simple = views.is_simple()
        shown = sv.STAGES_PLAIN if simple else STAGES
        verb = "Checking" if simple else "Analyzing"
        with st.status(f"{verb} {display}", expanded=True) as status:
            holder = st.empty()
            holder.markdown(
                c.progress_panel(shown, 0, "" if simple else "Starting"), unsafe_allow_html=True
            )
            state = {"idx": 0}

            def progress(message: str) -> None:
                msg = str(message)
                state["idx"] = _stage_index(msg, state["idx"])
                holder.markdown(
                    c.progress_panel(shown, state["idx"], "" if simple else msg),
                    unsafe_allow_html=True,
                )
                status.update(label=f"{verb} {display} · {shown[state['idx']]}")

            try:
                result, _hit = analyze_target(target, offline, refresh, progress, sample)
            except Exception as exc:  # every pipeline failure gets a designed panel
                _log.exception("analysis of %r failed", target)
                error = exc
    slot.empty()

    if error is not None:
        err = classify_error(error, display)
        err["target"] = target
        err["display"] = display
        st.session_state["_run_error"] = err
        return
    apply_result(result, display)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

NO_RESULT_MSG = "No analysis loaded yet. Run a ticker first, e.g. NVDA."


def build_pdf() -> None:
    result = st.session_state.get("result")
    if result is None:
        st.session_state["cmd_error"] = NO_RESULT_MSG
        return
    try:
        from veritas.export.pdf_tearsheet import build_pdf as _build
    except ImportError:
        st.session_state["cmd_error"] = (
            "PDF export is not available in this build yet (veritas.export.pdf_tearsheet)."
        )
        return
    try:
        data = _build(result)
    except Exception as exc:
        st.session_state["cmd_error"] = f"PDF export failed: {str(exc)[:200]}"
        return
    try:
        from veritas.export.pdf_tearsheet import pdf_filename

        name = pdf_filename(result)
    except Exception:
        as_of = c.get(result, "as_of")
        stamp = as_of.strftime("%Y%m%d") if hasattr(as_of, "strftime") else "undated"
        name = f"VERITAS_{c.get(result, 'ticker', 'UNKNOWN')}_{stamp}.pdf"
    st.session_state["pdf_bytes"] = bytes(data)
    st.session_state["_pdf_name"] = name
    st.session_state["cmd_error"] = None


def apply_command(cmd: Command) -> None:
    """Apply a parsed command to session state (PRD §8.3)."""
    st.session_state["_run_error"] = None  # every submission clears the last run error
    if cmd.kind == "empty":
        st.session_state["cmd_error"] = None
        return
    if cmd.kind == "invalid":
        st.session_state["cmd_error"] = cmd.error or "Unknown command."
        return
    if cmd.kind == "analyze" and cmd.arg:
        queue_analysis(cmd.arg)
        return
    if cmd.kind == "cik" and cmd.arg:
        queue_analysis(f"CIK {cmd.arg}")  # pipeline accepts "CIK <digits>"
        return
    if st.session_state.get("result") is None:
        st.session_state["cmd_error"] = NO_RESULT_MSG
        return
    st.session_state["cmd_error"] = None
    if cmd.kind == "view" and cmd.arg:
        st.session_state["view"] = cmd.arg
    elif cmd.kind == "contra":
        st.session_state["view"] = VIEW_OVERVIEW
        st.session_state["severity_filter"] = "CRITICAL"
    elif cmd.kind == "export":
        build_pdf()


def _on_export_click() -> None:
    build_pdf()


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def topbar_markup(settings: Settings, sample: bool, result: Any) -> str:
    simple = views.is_simple()
    chips: list[tuple[str, str]] = []
    if sample:
        chips.append(("Made-up sample data" if simple else "SYNTHETIC SAMPLE", "is-synthetic"))
    if result is not None:
        src, when = c.get(result, "source"), c.get(result, "as_of")
        chips.append(sv.source_chip(src, when) if simple else c.source_chip(src, when))
    if settings.live_enabled and not sample:
        chips.append(("Live lookups on" if simple else "SEC LIVE READY", "is-accent"))
    elif settings.offline:
        chips.append(("Offline: examples only" if simple else "OFFLINE MODE", ""))
    else:
        chips.append(("Examples only" if simple else "LIVE DISABLED · NO USER-AGENT", ""))
    if simple:
        return c.topbar(chips, "Checks what companies say against their own numbers")
    return c.topbar(chips)


def render_command_bar() -> None:
    with st.form("cmd_form", clear_on_submit=True, border=False):
        col_in, col_btn = st.columns([8, 1.3], vertical_alignment="center")
        raw = col_in.text_input(
            "Command",
            key="_cmd",
            placeholder="Type any U.S. company's stock symbol, like AAPL, NVDA or WMT",
            label_visibility="collapsed",
        )
        submitted = col_btn.form_submit_button(
            "Run  ⏎", type="primary", width="stretch", key="_cmd_submit"
        )
    if submitted:
        apply_command(parse_command(raw or ""))
    err = st.session_state.get("cmd_error")
    if err:
        if views.is_simple():
            st.markdown(
                c.command_error(
                    "That doesn't look like a stock symbol. Type one like AAPL, or tap one of "
                    "the examples.",
                    (),
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                c.command_error(f"{err} Valid commands:", VALID_COMMANDS), unsafe_allow_html=True
            )


def render_seed_cards() -> None:
    st.markdown(
        '<div class="vx vx-section-kicker"><span class="vx-label">Or tap a company to see it '
        "in action · works offline</span></div>",
        unsafe_allow_html=True,
    )
    cols = st.columns(len(SEED_TICKERS), gap="medium")
    for col, ticker in zip(cols, SEED_TICKERS, strict=True):
        with col:
            st.button(
                ticker,
                key=f"seedcard_{ticker}",
                on_click=queue_analysis,
                args=(ticker,),
                width="stretch",
            )
            st.markdown(
                story.seed_meta(ticker, c.SEED_META.get(ticker, (ticker, ""))[0]),
                unsafe_allow_html=True,
            )


def render_actions(settings: Settings, sample: bool) -> None:
    cols = st.columns([1, 1, 1, 1, 3.2, 1.5, 1.5], vertical_alignment="center")
    for col, ticker in zip(cols[:4], SEED_TICKERS, strict=True):
        col.button(
            ticker,
            key=f"seedpill_{ticker}",
            on_click=queue_analysis,
            args=(ticker,),
            width="stretch",
        )
    simple = views.is_simple()
    if sample:
        why = "Synthetic sample mode: live refresh is off."
    elif settings.offline:
        why = "Offline mode (VERITAS_OFFLINE=1): live refresh is off."
    elif settings.user_agent is None:
        why = (
            "Live lookups are off on this copy of the site."
            if simple
            else "Set VERITAS_SEC_USER_AGENT to enable live refresh."
        )
    else:
        why = (
            "Fetch this company's newest reports from the SEC again."
            if simple
            else "Re-fetch this issuer from SEC EDGAR, bypassing the 1-hour cache."
        )
    cur_ticker = st.session_state.get("ticker")
    cols[5].button(
        "↻ Latest data" if simple else "↻ Refresh live",
        key="_refresh",
        disabled=sample or not settings.live_enabled or not cur_ticker,
        help=why,
        on_click=queue_analysis,
        args=(str(cur_ticker or ""),),
        kwargs={"refresh": True},
        width="stretch",
    )
    cols[6].button(
        "⇩ PDF report" if simple else "⇩ Export PDF",
        key="_export",
        on_click=_on_export_click,
        width="stretch",
        help=(
            "A 2-page summary to save or share (it uses the detailed, technical wording)."
            if simple
            else "Build the 2-page tear-sheet (same as the EXPORT command)."
        ),
    )


def render_export_strip() -> None:
    data = st.session_state.get("pdf_bytes")
    if not data:
        return
    name = st.session_state.get("_pdf_name") or "VERITAS.pdf"
    with st.container(key="export_strip"):
        left, right = st.columns([4, 1.4], vertical_alignment="center")
        left.markdown(
            c.one_line(
                '<div class="vx"><span class="vx-label">PDF report ready</span>'
                f'<div class="vx-mono" style="margin-top:4px">{c.esc(name)} · '
                f"{len(data) / 1024:,.0f} KB · PDF</div></div>"
            ),
            unsafe_allow_html=True,
        )
        right.download_button(
            "Download PDF",
            data=data,
            file_name=name,
            mime="application/pdf",
            type="primary",
            key="_download",
            width="stretch",
        )


def render_banners(settings: Settings, sample: bool, result: Any) -> None:
    if sample:
        st.markdown(
            c.banner(
                "Synthetic sample data (VERITAS_UI_SAMPLE=1). The issuer, filings and figures are "
                "fictional and exist only to design and test the interface.",
                kind="synthetic",
                title="Synthetic",
            ),
            unsafe_allow_html=True,
        )
    elif settings.user_agent is None and views.is_simple():
        st.markdown(
            c.banner(
                "Live lookups are off on this copy of the site, so only the four example "
                "companies (NVDA, WMT, TSLA, BA) work.",
                kind="info",
                title="Examples only",
            ),
            unsafe_allow_html=True,
        )
    elif settings.user_agent is None:
        st.markdown(
            c.banner(
                "Live SEC access is disabled because <code>VERITAS_SEC_USER_AGENT</code> is unset "
                "or a placeholder. Seed tickers still work. Add "
                '<code>VERITAS_SEC_USER_AGENT="VERITAS/0.1 &lt;name&gt; &lt;email&gt;"</code> '
                "to <code>.env</code> to enable live lookups (SEC fair-access policy).",
                kind="info",
                title="Live off",
                raw_html=True,
            ),
            unsafe_allow_html=True,
        )
    if result is not None:
        simple = views.is_simple()
        for text in c.as_list(c.get(result, "banners")):
            shown = sv.banner_plain(text) if simple else str(text)
            st.markdown(c.banner(shown, kind="notice", title="Notice"), unsafe_allow_html=True)


def render_run_error() -> None:
    err = st.session_state.get("_run_error")
    if not err:
        return
    with st.container(key="run_error"):
        st.markdown(
            c.one_line(
                '<div class="vx" style="display:flex;gap:16px;align-items:flex-start">'
                f'<div class="vx-empty-glyph">{c.esc(err.get("glyph", "!"))}</div><div>'
                '<div class="vx-h4" style="margin:2px 0 4px;font-size:15px">'
                f"{c.esc(err.get('title', ''))}</div>"
                f'<div style="color:var(--text-3);font-size:14px;margin:0">'
                f"{c.esc(err.get('message', ''))}</div></div></div>"
            ),
            unsafe_allow_html=True,
        )
        sugg = [s for s in err.get("suggestions") or [] if s][:5]
        n_btn = len(sugg) + (1 if err.get("retry") else 0) + 1
        cols = st.columns([1] * n_btn + [max(1, 8 - n_btn)], vertical_alignment="center")
        i = 0
        for s in sugg:
            cols[i].button(s, key=f"_sugg_{s}", on_click=queue_analysis, args=(s,), width="stretch")
            i += 1
        if err.get("retry"):
            cols[i].button(
                "↻ Retry",
                key="_retry",
                on_click=queue_analysis,
                args=(str(err.get("target", "")), str(err.get("display", ""))),
                type="primary",
                width="stretch",
            )
            i += 1
        cols[i].button("Dismiss", key="_dismiss", on_click=_dismiss_error, width="stretch")


def _dismiss_error() -> None:
    st.session_state["_run_error"] = None


def render_detail_toggle() -> None:
    """Simple view is the default; this switch turns on the analyst view (D-612)."""
    with st.container(key="detailbar"):
        _, right = st.columns([5, 1.6], vertical_alignment="center")
        right.toggle(
            "Analyst detail",
            key="_detail",
            help="Show the technical view: formulas, raw figures, filing codes and check names.",
        )


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


def render_footer() -> None:
    if views.is_simple():
        st.markdown(sv.footer(APP_VERSION), unsafe_allow_html=True)
    else:
        st.markdown(c.footer(DISCLAIMER, APP_VERSION), unsafe_allow_html=True)
    simple = views.is_simple()
    # In the simple view the company page already has the glossary above the score.
    if not (simple and st.session_state.get("result") is not None):
        with st.expander(
            "Jargon buster: the terms on this page, in plain English",
            icon=":material/translate:",
        ):
            st.markdown(
                story.glossary_html(sv.GLOSSARY_SIMPLE if simple else None),
                unsafe_allow_html=True,
            )
    with st.expander(
        "How the checks work (technical)" if views.is_simple() else "Methodology",
        icon=":material/menu_book:",
    ):
        try:
            text = METHODOLOGY_PATH.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text.strip():
            st.markdown(text)
        else:
            st.markdown(
                c.empty_state(
                    "Methodology document not available yet",
                    "docs/METHODOLOGY.md is not in this build. Known limitations are listed below.",
                    glyph="§",
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                "**Known limitations**\n\n" + "\n".join(f"- {x}" for x in KNOWN_LIMITATIONS)
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    st.set_page_config(
        page_title="VERITAS · Narrative vs Numbers",
        page_icon="◎",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_css()
    init_state()
    settings = get_settings()
    sample = sample_mode()

    # Top bar and hero are placeholders filled after any queued analysis has run,
    # so the whole page reflects the new result in this same run.
    topbar_slot = st.empty()
    render_detail_toggle()
    hero_slot = st.empty()
    render_command_bar()  # may queue an analysis or set cmd_error
    pending_blocked(settings, sample)
    run_pending(settings, sample)  # staged progress under the command bar; no rerun
    render_run_error()  # the single error panel, directly under the command bar
    result = st.session_state.get("result")
    topbar_slot.markdown(topbar_markup(settings, sample, result), unsafe_allow_html=True)
    if result is None:
        hero_slot.markdown(story.hero(), unsafe_allow_html=True)
        render_seed_cards()
        st.markdown(story.story_steps(), unsafe_allow_html=True)
    else:
        render_actions(settings, sample)
    render_export_strip()

    if result is None:
        with st.container(key="landing"):
            render_banners(settings, sample, None)
            views.render_landing()
    else:
        with st.container(key="body"):
            simple = views.is_simple()
            st.markdown(
                sv.identity(result, sample) if simple else c.identity(result, sample),
                unsafe_allow_html=True,
            )
            render_banners(settings, sample, result)
            with st.container(key="viewbar"):
                st.radio(
                    "View",
                    VIEW_LABELS,
                    key="view",
                    horizontal=True,
                    label_visibility="collapsed",
                    format_func=(lambda v: sv.VIEW_PLAIN.get(v, v)) if simple else str,
                )
            views.render_view(str(st.session_state.get("view")), result)
    render_footer()
