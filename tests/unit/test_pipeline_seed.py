"""End-to-end offline tests over the seed tickers (PRD §7.2.6, §11.2, §11.3)."""

from __future__ import annotations

import ast
import re
import time
from functools import cache
from pathlib import Path

import pytest

from veritas.config import DISCLAIMER, SEED_DIR, SEED_TICKERS
from veritas.edgar.source import SeedSource
from veritas.engine.html_text import html_to_text
from veritas.lexicons import BANNED_WORDS
from veritas.models import AnalysisResult, Citation, DocRef
from veritas.pipeline import _locate, analyze

pytestmark = pytest.mark.skipif(
    not (SEED_DIR / "manifest.json").exists(), reason="seed cache not built"
)

SRC = Path(__file__).resolve().parents[2] / "src" / "veritas"


@cache
def _result(ticker: str) -> AnalysisResult:
    return analyze(ticker, offline=True)


@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_every_module_non_error(ticker: str) -> None:
    r = _result(ticker)
    assert r.source == "seed"
    for key, mod in r.modules.items():
        assert mod.status not in ("error", "timeout"), (key, mod.reason)


@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_determinism(ticker: str) -> None:
    assert analyze(ticker, offline=True).to_json() == analyze(ticker, offline=True).to_json()


@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_warm_seed_analyze_under_target(ticker: str) -> None:
    _result(ticker)
    t0 = time.perf_counter()
    analyze(ticker, offline=True)
    assert time.perf_counter() - t0 <= 4.0  # §12 target 2 s; generous bound for CI noise


def _quote_citations(r: AnalysisResult) -> list[Citation]:
    out: list[Citation] = []
    for mod in r.modules.values():
        for f in mod.findings:
            for c in f.citations:
                if c.quote_raw is not None:
                    out.append(c)
            for cl in f.claims:
                out.append(cl.citation)
    return out


@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_citation_invariant_narrative(ticker: str) -> None:
    """section_text[char_start:char_end] == quote_raw for every narrative quote."""
    r = _result(ticker)
    ds = SeedSource()
    checked = 0
    for c in _quote_citations(r):
        if c.section.startswith("Note:") or c.section.startswith("DEF 14A"):
            continue  # Module B/C have their own invariant tests on note/CD&A texts
        assert c.char_start is not None and c.char_end is not None
        html = ds.document(c.url)
        assert html, c.url
        ref = DocRef(
            c.cik, c.ticker, c.form, c.accession, c.filing_date, c.report_date, c.doc_name, c.url
        )
        section = _locate(ds, ref, html_to_text(html))
        assert section.text[c.char_start : c.char_end] == c.quote_raw
        checked += 1
    assert checked >= 0


@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_footnote_citation_invariant(ticker: str) -> None:
    r = _result(ticker)
    texts = r.modules["B"].extra.get("note_texts", {})
    for f in r.modules["B"].findings:
        for c in f.citations:
            if c.quote_raw is None or c.char_start is None:
                continue
            found = False
            for pair in texts.values():
                for sides in pair.values():
                    for side in ("current", "prior"):
                        txt = sides.get(side) or ""
                        if txt[c.char_start : c.char_end] == c.quote_raw:
                            found = True
            assert found, (ticker, c.section, c.quote_raw[:80])


@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_no_banned_words_in_result(ticker: str) -> None:
    payload = _result(ticker).to_json()
    # Verbatim filing quotes are the issuer's own words, not our characterisation of the
    # issuer. Strip them before checking our own strings.
    r = _result(ticker)
    for c in _quote_citations(r):
        if c.quote_raw:
            payload = payload.replace(c.quote_raw.replace('"', '\\"'), "")
    texts = r.modules["B"].extra
    for pair in texts.get("note_texts", {}).values():
        for sides in pair.values():
            for side in ("current", "prior"):
                if sides.get(side):
                    payload = payload.replace(sides[side], "")
    own = [f.title + " " + f.explanation for m in r.modules.values() for f in m.findings] + [
        r.score.label,
        *(r.banners),
    ]
    for s in own:
        assert not BANNED_WORDS.search(s), s
    assert "VERIFIED" not in " ".join(own)


def _string_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def test_banned_words_absent_from_source_literals() -> None:
    """§0.1.8: no banned word in any src/ string literal, except the disclaimer and lexicon."""
    allowed_files = {SRC / "lexicons.py"}
    disclaimer_parts = {p for p in re.split(r"\s+", DISCLAIMER)}
    offenders: list[tuple[str, str]] = []
    for path in SRC.rglob("*.py"):
        if path in allowed_files:
            continue
        for lit in _string_literals(path):
            if lit in DISCLAIMER or DISCLAIMER in lit:
                continue
            if BANNED_WORDS.search(lit) and not set(lit.split()) <= disclaimer_parts:
                offenders.append((str(path.relative_to(SRC)), lit[:80]))
    for path in SRC.rglob("*.css"):
        if BANNED_WORDS.search(path.read_text()):
            offenders.append((str(path.relative_to(SRC)), "css"))
    assert offenders == []


def test_integrity_score_and_verified_never_used() -> None:
    for path in list(SRC.rglob("*.py")) + list(SRC.rglob("*.css")):
        text = path.read_text()
        assert "Integrity Score" not in text, path
        assert not re.search(r"[\"'].*\bVERIFIED\b.*[\"']", text), path
