"""End-to-end UI rendering with streamlit.testing.v1.AppTest (synthetic sample)."""

from __future__ import annotations

import dataclasses
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from tests.fixtures.sample_result import sample_result
from veritas.config import FINANCIAL_SCOPE_NOTE
from veritas.models import ModuleResult, ScoreBreakdown
from veritas.ui import layout
from veritas.ui.commands import VIEW_LABELS, VIEW_OVERVIEW, VIEW_REDLINE
from veritas.ui.layout import classify_error

APP = str(Path(__file__).resolve().parents[2] / "app.py")
EVIL = '<script>alert("x")</script>'


_REAL_SOCKET = socket.socket


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Overrides the conftest guard for this module only: AppTest's asyncio loop
    needs a local AF_UNIX socketpair, so Unix-domain sockets are allowed while
    every Internet socket (AF_INET/AF_INET6) is still refused (PRD §11.1)."""

    def guarded(family: Any = socket.AF_INET, *args: Any, **kwargs: Any) -> socket.socket:
        if family != socket.AF_UNIX:
            raise RuntimeError("Network access is disabled in unit tests (PRD §11.1)")
        return _REAL_SOCKET(family, *args, **kwargs)

    def no_connect(*_args: object, **_kwargs: object) -> socket.socket:
        raise RuntimeError("Network access is disabled in unit tests (PRD §11.1)")

    monkeypatch.setattr(socket, "socket", guarded)
    monkeypatch.setattr(socket, "create_connection", no_connect)
    yield


@pytest.fixture(autouse=True)
def _fresh_store() -> Iterator[None]:
    layout.clear_store()
    yield
    layout.clear_store()


def test_internet_still_blocked() -> None:
    with pytest.raises(RuntimeError):
        socket.create_connection(("www.sec.gov", 443))
    with pytest.raises(RuntimeError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


@pytest.fixture
def sample_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERITAS_UI_SAMPLE", "1")


def _app(detail: bool = True) -> AppTest:
    """Most tests here check the analyst (technical) view; the simple view, which is the
    default for visitors, has its own tests in test_simple_view.py."""
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_detail"] = detail
    return at


def _text(at: AppTest) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def _submit(at: AppTest, raw: str) -> AppTest:
    at.text_input(key="_cmd").input(raw)
    return at.button(key="_cmd_submit").click().run()


def test_landing_state(sample_env: None) -> None:
    at = _app().run()
    assert not at.exception
    text = _text(at)
    assert "What companies say." in text
    assert "Four questions we ask every company" in text
    assert "Synthetic sample data" in text  # clearly labelled
    keys = {b.key for b in at.button}
    assert {"seedcard_NVDA", "seedcard_WMT", "seedcard_TSLA", "seedcard_BA"} <= keys
    assert at.session_state["result"] is None
    for key in ("ticker", "view", "result", "severity_filter", "pdf_bytes", "cmd_error"):
        assert key in at.session_state


def test_seed_click_then_every_view(sample_env: None) -> None:
    at = _app().run()
    at.button(key="seedcard_NVDA").click().run()
    assert not at.exception
    assert at.session_state["result"] is not None
    assert at.session_state["view"] == VIEW_OVERVIEW
    for label in VIEW_LABELS:
        at.radio(key="view").set_value(label).run()
        assert not at.exception, label
        assert at.session_state["view"] == label
    assert "Not investment advice" in _text(at)


def test_unknown_command_shows_error(sample_env: None) -> None:
    at = _app().run()
    _submit(at, "what is this")
    assert not at.exception
    assert at.session_state["cmd_error"]
    text = _text(at)
    assert "Unknown command" in text
    assert "CONTRA" in text and "EXPORT" in text  # lists the valid commands


def test_view_command_without_result_is_designed_error(sample_env: None) -> None:
    at = _app().run()
    _submit(at, "DIFF")
    assert "No analysis loaded yet" in str(at.session_state["cmd_error"])


def test_commands_drive_views(sample_env: None) -> None:
    at = _app().run()
    _submit(at, "nvda")
    assert at.session_state["result"] is not None
    _submit(at, "DIFF")
    assert at.session_state["view"] == VIEW_REDLINE
    _submit(at, "contra")
    assert at.session_state["view"] == VIEW_OVERVIEW
    assert at.session_state["severity_filter"] == "CRITICAL"
    assert at.radio(key="severity_filter").value == "CRITICAL"
    assert not at.exception


def test_export_command(sample_env: None) -> None:
    at = _app().run()
    _submit(at, "WMT")
    _submit(at, "EXPORT")
    assert not at.exception
    pdf = at.session_state["pdf_bytes"]
    err = at.session_state["cmd_error"]
    # Either the PDF builder exists and produced bytes, or a designed message is shown.
    assert (pdf and bytes(pdf).startswith(b"%PDF")) or (err and "PDF export" in err)


def _degraded() -> object:
    res = sample_result()
    a = res.modules["A"]
    f = a.findings[0]
    claim = f.claims[0]
    evil_claim = dataclasses.replace(
        claim, citation=dataclasses.replace(claim.citation, quote_raw=EVIL + " demand")
    )
    f_evil = dataclasses.replace(f, claims=(evil_claim,), title="Title " + EVIL)
    a2 = dataclasses.replace(a, findings=(f_evil, *a.findings[1:]))
    return dataclasses.replace(
        res,
        company_name="Evil " + EVIL,
        modules={
            "A": a2,
            "B": ModuleResult("error", (), (), "parser crashed " + EVIL, {}),
            "C": ModuleResult("unavailable", (), (), None, {}),
        },
        score=ScoreBreakdown(
            score=None,
            coverage_num=1,
            coverage_den=5,
            deductions=(),
            label="INSUFFICIENT DATA",
            scope_note=FINANCIAL_SCOPE_NOTE,
            modules_assessed=1,
            unassessed=("B · Footnote Redline", "C · Proxy X-Ray"),
        ),
        banners=("Banner " + EVIL,),
    )


def test_filing_strings_escaped_and_degraded_states(sample_env: None) -> None:
    at = _app()
    at.session_state["result"] = _degraded()
    at.run()
    assert not at.exception
    for label in VIEW_LABELS:
        at.radio(key="view").set_value(label).run()
        assert not at.exception, label
        text = _text(at)
        assert "<script>" not in text, label
        if label == VIEW_OVERVIEW:
            assert "&lt;script&gt;" in text
            assert "INSUFFICIENT" in text
            assert "1 of 3 modules assessed" in text
            assert "Not assessed: parser crashed" in text
            assert "Not assessed: Proxy not available" in text
        elif label == VIEW_REDLINE:
            assert "Not assessed: parser crashed" in text
        else:
            assert "Not assessed" in text or "Accounting checks" in text


def test_live_off_blocks_non_seed_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERITAS_UI_SAMPLE", raising=False)
    monkeypatch.setenv("VERITAS_SEC_USER_AGENT", "")
    at = _app().run()
    assert not at.exception
    _submit(at, "ZZZZ")
    assert not at.exception
    err = at.session_state["_run_error"]
    assert err and "example companies only" in err["title"]
    assert {"_sugg_NVDA", "_dismiss"} <= {b.key for b in at.button}


def test_classify_error_messages() -> None:
    class TickerNotFound(Exception):
        def __init__(self, suggestions: list[str]) -> None:
            super().__init__("nope")
            self.suggestions = suggestions

    class UnsupportedFiler(Exception):
        reason = "Files 20-F."

    class NoXBRL(Exception):
        pass

    e = classify_error(TickerNotFound(["AAPL", "AAP", "A", "AA", "AAL", "AAON"]), "AAPX")
    assert "couldn't find" in e["title"] and len(e["suggestions"]) == 5
    assert "can't check this kind" in classify_error(UnsupportedFiler(), "X")["title"]
    assert "in a form we can read" in classify_error(NoXBRL(), "X")["title"]
    assert classify_error(ImportError("x"), "X")["title"].startswith("Analysis engine")
    assert classify_error(TimeoutError("SEC 503"), "X")["retry"] is True


# ---------------------------------------------------------------------------
# Re-analysis through the result store (regression: CacheReplayClosureError)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Real (non-sample) path with a stub analyze() that drives the progress
    callback, exactly like the pipeline does."""
    import veritas.pipeline as pipeline

    calls: list[str] = []

    def fake_analyze(target: str, *, offline: bool = False, progress: Any = None, **_: Any) -> Any:
        calls.append(target)
        for stage in layout.STAGES:
            if progress is not None:
                progress(stage)
        return dataclasses.replace(sample_result(), ticker=target)

    monkeypatch.delenv("VERITAS_UI_SAMPLE", raising=False)
    monkeypatch.setattr(pipeline, "analyze", fake_analyze)
    return calls


def test_same_ticker_twice_and_back(fake_pipeline: list[str]) -> None:
    at = _app().run()
    for raw in ("NVDA", "WMT", "NVDA", "nvda"):
        _submit(at, raw)
        assert not at.exception, raw
        assert at.session_state["_run_error"] is None, raw
        assert at.session_state["ticker"] == raw.upper()
    assert fake_pipeline == ["NVDA", "WMT"]  # repeats are served from the store


def test_seed_card_then_typed_command(fake_pipeline: list[str]) -> None:
    at = _app().run()
    at.button(key="seedcard_WMT").click().run()
    assert not at.exception
    _submit(at, "WMT")
    assert not at.exception
    assert at.session_state["_run_error"] is None
    assert at.session_state["ticker"] == "WMT"
    at.button(key="seedpill_WMT").click().run()
    assert not at.exception
    assert fake_pipeline == ["WMT"]


def test_sample_mode_reanalysis(sample_env: None) -> None:
    at = _app().run()
    at.button(key="seedcard_NVDA").click().run()
    _submit(at, "NVDA")
    _submit(at, "NVDA")
    assert not at.exception
    assert at.session_state["result"] is not None


def test_store_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    layout.store_put(("X", True, False), "r")
    assert layout.store_get(("X", True, False)) == "r"
    real = layout.time.monotonic
    monkeypatch.setattr(layout.time, "monotonic", lambda: real() + layout.RESULT_TTL_S + 1)
    assert layout.store_get(("X", True, False)) is None


def test_ticker_not_found_single_panel_cleared_by_next_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import veritas.pipeline as pipeline
    from veritas.edgar.resolve import TickerNotFound

    def boom(target: str, **_: Any) -> Any:
        raise TickerNotFound(target, ["NVDA", "NVDS"])

    monkeypatch.delenv("VERITAS_UI_SAMPLE", raising=False)
    monkeypatch.setattr(pipeline, "analyze", boom)
    at = _app().run()
    _submit(at, "NVD")
    assert not at.exception
    text = _text(at)
    assert text.count("find “NVD”") == 1
    assert {"_sugg_NVDA", "_sugg_NVDS"} <= {b.key for b in at.button}
    _submit(at, "what is this")
    assert at.session_state["_run_error"] is None
    assert "find “NVD”" not in _text(at)


def test_classify_error_network_vs_generic() -> None:
    from veritas.edgar.client import SecBlocked, SecUnavailable

    for exc in (SecBlocked("403"), SecUnavailable("503")):
        assert "briefly unavailable" in classify_error(exc, "X")["message"]
    generic = classify_error(ValueError("KeyError: 'foo' at engine/x.py:12"), "X")
    assert "briefly unavailable" not in generic["message"]
    assert "foo" not in generic["message"] and "engine" not in generic["message"]


# ---------------------------------------------------------------------------
# Audit round 2: narrative sources, footnote edits, prior-period cards, CD&A text
# ---------------------------------------------------------------------------


def _rich() -> Any:
    res = sample_result()
    a = res.modules["A"]
    prior = dataclasses.replace(a.findings[0], id="A:prior", category="accounting_prior_period")
    sections = [
        {
            "form": "10-K",
            "accession": "0009999999-25-000010",
            "url": "https://www.sec.gov/Archives/x.htm",
            "report_date": "2024-12-31",
            "label": "10-K Item 7",
            "status": "OK",
            "word_count": 12345,
            "reason": None,
        },
        {
            "form": "8-K",
            "accession": "0009999999-25-000011",
            "url": "https://www.sec.gov/Archives/y.htm",
            "report_date": "2024-12-31",
            "label": None,
            "status": "NOT_FOUND",
            "word_count": 0,
            "reason": "document unavailable",
        },
    ]
    a2 = dataclasses.replace(
        a, findings=(*a.findings, prior), extra={**a.extra, "sections": sections}
    )
    c_mod = dataclasses.replace(
        res.modules["C"],
        status="low_confidence",
        reason="Fewer than 2 incentive metrics",
        extra={**res.modules["C"].extra, "cda_text": "Compensation Discussion " + EVIL},
    )
    return dataclasses.replace(res, modules={**res.modules, "A": a2, "C": c_mod})


def test_overview_sources_edits_and_prior_period(sample_env: None) -> None:
    at = _app()
    at.session_state["result"] = _rich()
    at.run()
    assert not at.exception
    text = _text(at)
    assert "Narrative sources" in text
    assert "12,345 words" in text
    assert "Narrative unavailable for this filing" in text
    assert "Footnote edits" in text
    assert "10-K · scored" in text
    assert "<ins>" in text or "<del>" in text
    assert "prior period · context · unscored" in text
    assert "COGS" in text or "Receivables (end)" in text  # humanised input labels


def test_low_confidence_proxy_shows_escaped_cda_text(sample_env: None) -> None:
    at = _app()
    at.session_state["result"] = _rich()
    at.run()
    at.radio(key="view").set_value(VIEW_LABELS[3]).run()
    assert not at.exception
    text = _text(at)
    assert "LOW CONFIDENCE" in text
    assert "Compensation Discussion" in text
    assert "<script>" not in text and "&lt;script&gt;" in text


def test_footnote_edits_scored_first_and_counts(sample_env: None) -> None:
    from veritas.ui import views

    res = _rich()
    b = res.modules["B"]
    pairs = dict(b.extra["pairs"])
    tenq = {
        "prior": pairs["10-K"]["prior"],
        "current": {**pairs["10-K"]["current"], "form": "10-Q", "report_date": "2025-09-30"},
        "notes": [
            {
                "title": "Commitments",
                "category": "commitments",
                "rows": [{"kind": "INSERTED", "current": "New 10-Q sentence.", "material": True}]
                * 9,
            }
        ],
    }
    pairs["10-Q"] = tenq
    b2 = dataclasses.replace(b, extra={**b.extra, "pairs": pairs})
    edits = views.footnote_edits(b2)
    assert edits[0]["form"] == "10-K" and edits[-1]["form"] == "10-Q"
    res2 = dataclasses.replace(res, modules={**res.modules, "B": b2})
    at = _app()
    at.session_state["result"] = res2
    at.run()
    assert not at.exception
    text = _text(at)
    assert text.index("10-K · scored") < text.index("10-Q · unscored")
    assert "prior-period context" in text  # context card counted separately


def test_note_label_drops_duplicate_category() -> None:
    from veritas.ui.views import _note_label

    assert _note_label({"title": "Contingencies", "category": "contingencies"}) == "Contingencies"
    assert "debt" in _note_label({"title": "Borrowings", "category": "debt"})
    label = _note_label({"title": "Commitments and Contingencies", "category": "contingencies"})
    assert label == "Commitments and Contingencies"


def test_proxy_low_confidence_reason_once(sample_env: None) -> None:
    res = _rich()
    reason = "Fewer than 2 incentive metrics could be detected"
    c_mod = dataclasses.replace(
        res.modules["C"],
        reason=reason,
        extra={**res.modules["C"].extra, "notes": [reason + ". Extracted text is shown."]},
    )
    at = _app()
    at.session_state["result"] = dataclasses.replace(res, modules={**res.modules, "C": c_mod})
    at.run()
    at.radio(key="view").set_value(VIEW_LABELS[3]).run()
    assert not at.exception
    assert _text(at).count(reason) == 1


def test_cache_hit_renders_in_same_run(fake_pipeline: list[str]) -> None:
    at = _app().run()
    _submit(at, "NVDA")
    _submit(at, "WMT")
    _submit(at, "NVDA")
    assert not at.exception
    assert at.session_state["ticker"] == "NVDA"
    assert "SampleCo" in _text(at)  # identity rendered in the run that served the hit
    assert not at.status  # no transient progress panel on a store hit
