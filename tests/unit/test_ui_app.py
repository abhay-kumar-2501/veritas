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


def test_internet_still_blocked() -> None:
    with pytest.raises(RuntimeError):
        socket.create_connection(("www.sec.gov", 443))
    with pytest.raises(RuntimeError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


@pytest.fixture
def sample_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERITAS_UI_SAMPLE", "1")


def _app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=60)


def _text(at: AppTest) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def _submit(at: AppTest, raw: str) -> AppTest:
    at.text_input(key="_cmd").input(raw)
    return at.button(key="_cmd_submit").click().run()


def test_landing_state(sample_env: None) -> None:
    at = _app().run()
    assert not at.exception
    text = _text(at)
    assert "What management said." in text
    assert "How it works" in text
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
    assert err and "seed tickers only" in err["title"]
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
    assert "Ticker not found" in e["title"] and len(e["suggestions"]) == 5
    assert "Unsupported filer type in v1" in classify_error(UnsupportedFiler(), "X")["title"]
    assert "No structured XBRL" in classify_error(NoXBRL(), "X")["title"]
    assert classify_error(ImportError("x"), "X")["title"].startswith("Analysis engine")
    assert classify_error(TimeoutError("SEC 503"), "X")["retry"] is True
