"""Simple view (docs/decisions/ui.md D-612): the default page must read without finance
jargon. Every tab is rendered for every seed company and scanned for technical terms.

Excluded from the scan, because they are not our wording:
* verbatim filing text (management quotes, fine-print sentences, pay-report text),
* anything inside curly quotes “…” (a plain explanation may name the term it explains),
* glossary rows and hover tips (they exist to define jargon),
* the methodology document, which is labelled technical.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from veritas.config import SEED_DIR, SEED_TICKERS
from veritas.ui.commands import VIEW_LABELS

APP = str(Path(__file__).resolve().parents[2] / "app.py")
_REAL_SOCKET = socket.socket

seeded = pytest.mark.skipif(
    not (SEED_DIR / "manifest.json").exists(), reason="seed cache not built"
)

JARGON = re.compile(
    r"\b(10-K|10-Q|8-K|DEF 14A|XBRL|CIK|SIC|TTM|GAAP|SBC|DSO|DIO|DPO|COGS|EBITDA|EPS|"
    r"CD&A|MD&A|LLM|ROIC|TSR|PSU|"
    r"accruals?|receivables?|inventor(y|ies)|capitali[sz]\w*|amorti[sz]\w*|"
    r"issuer|filer|modules?|narrative|heuristic\w*|deterministic|tear-sheet|tokens?|"
    r"footnotes?|redline|hedg\w*|proxy|ticker|fiscal|incentive|"
    r"CRITICAL|WARNING|NO FLAG|NO_FLAG|N/A|SEED|seed)\b"
    r"|Δ|≥"
)

# Elements whose inner text is verbatim filing text or a definition.
_EXCLUDE_BLOCKS = re.compile(
    r'<div class="(vx )?vx-(quote|edit-text|diffcell|textblock)[^"]*">.*?</div>'
    r'|<div class="vx vx-gloss">.*?</div></div>'
    r'|<td><div style="color:var\(--text\);margin-bottom:8px">.*?</div>'
    r'|<div class="vx-edit-title">.*?</div>',
    re.S,
)
_ATTRS = re.compile(r'\s(data-tip|aria-label|title|href|class|style)="[^"]*"')
_TAGS = re.compile(r"<[^>]+>")
_QUOTED = re.compile(r"“[^”]*”")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def guarded(family: Any = socket.AF_INET, *args: Any, **kwargs: Any) -> socket.socket:
        if family != socket.AF_UNIX:
            raise RuntimeError("Network access is disabled in unit tests (PRD §11.1)")
        return _REAL_SOCKET(family, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", guarded)
    monkeypatch.setenv("VERITAS_OFFLINE", "1")
    monkeypatch.delenv("VERITAS_UI_SAMPLE", raising=False)
    yield


def visible_text(at: AppTest) -> str:
    parts: list[str] = []
    for m in at.markdown:
        v = str(m.value)
        if not v.lstrip().startswith("<"):
            continue  # raw Markdown: the methodology document (labelled technical)
        v = _EXCLUDE_BLOCKS.sub(" ", v)
        v = _ATTRS.sub("", v)
        v = _TAGS.sub(" ", v)
        parts.append(v)
    parts += [str(c.value) for c in at.caption]
    for r in at.radio:
        parts += [str(r.format_func(o)) for o in r.options]
    for t in at.toggle:
        parts.append(str(t.label))
    for b in at.button:
        parts.append(str(b.label))
    text = "\n".join(parts)
    text = text.replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"')
    return _QUOTED.sub("“…”", text)


def _offenders(text: str) -> list[str]:
    out = []
    for m in JARGON.finditer(text):
        lo, hi = max(0, m.start() - 50), min(len(text), m.end() + 50)
        out.append(f"{m.group(0)!r} in …{text[lo:hi]!r}…")
    return out


def test_landing_is_plain() -> None:
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert at.toggle(key="_detail").value is False  # simple view is the default
    assert _offenders(visible_text(at)) == []


@seeded
@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_every_tab_is_plain(ticker: str) -> None:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key=f"seedcard_{ticker}").click().run()
    assert not at.exception
    assert at.session_state["result"] is not None
    for label in VIEW_LABELS:
        at.radio(key="view").set_value(label).run()
        assert not at.exception, label
        bad = _offenders(visible_text(at))
        assert bad == [], (ticker, label, bad[:8])


@seeded
def test_analyst_detail_restores_the_technical_view() -> None:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key="seedcard_NVDA").click().run()
    simple = visible_text(at)
    assert "Our score" in simple and "Module D" not in simple
    at.toggle(key="_detail").set_value(True).run()
    assert not at.exception
    detail = "\n".join(str(m.value) for m in at.markdown)
    assert "Module D" in detail and "Narrative sources" in detail
    assert "XBRL" in detail  # raw citations are back for analysts
    assert _offenders(visible_text(at))  # the scan does catch technical wording
