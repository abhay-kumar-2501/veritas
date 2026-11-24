"""Seed-ticker smoke tests for Module B (skipped when the seed is absent)."""

from __future__ import annotations

import gzip
import json
from datetime import date

import pytest

from veritas.config import SEED_DIR
from veritas.engine.footnote_diff import run_footnotes
from veritas.models import DocRef

MANIFEST = SEED_DIR / "manifest.json"
pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="seed_cache absent")


def _entries() -> list[dict[str, object]]:
    return list(json.loads(MANIFEST.read_text())["entries"]) if MANIFEST.exists() else []


def load_doc(ticker: str, accession: str) -> tuple[DocRef, str] | None:
    d = SEED_DIR / ticker / "docs" / accession.replace("-", "")
    files = sorted(d.glob("*.gz")) if d.exists() else []
    if not files:
        return None
    name = files[0].name[:-3]
    html = gzip.decompress(files[0].read_bytes()).decode("utf-8", "replace")
    ref = DocRef(1, ticker, "10-K", accession, date(2025, 1, 1), None, name, f"https://x/{name}")
    return ref, html


def _pair(ticker: str, a: str, b: str) -> tuple[DocRef, str, DocRef, str] | None:
    p, c = load_doc(ticker, a), load_doc(ticker, b)
    if p is None or c is None:
        return None
    return p[0], p[1], c[0], c[1]


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: str(e["ticker"]))
def test_seed_footnotes_not_error_and_citations_hold(entry: dict[str, object]) -> None:
    roles = entry["roles"]
    assert isinstance(roles, dict)
    t = str(entry["ticker"])
    res = run_footnotes(
        _pair(t, roles["prior_10k"], roles["latest_10k"]),
        _pair(t, roles["prior_year_10q"], roles["latest_10q"]),
    )
    assert res.status in ("ok", "partial")
    for kind, pair in res.extra["pairs"].items():
        assert pair["status"] == "OK", (t, kind)
        assert pair["segments"]["current"] >= 3
    for f in res.findings:
        c = f.citations[0]
        if c.quote_raw is None:
            continue
        kind = "10-K" if f.category == "footnotes" else "10-Q"
        key = c.section[len("Note: ") :]
        prior_acc = roles["prior_10k"] if kind == "10-K" else roles["prior_year_10q"]
        side = "prior" if c.accession == prior_acc else "current"
        text = res.extra["note_texts"][kind][key][side]
        assert c.char_start is not None and c.char_end is not None
        assert text[c.char_start : c.char_end] == c.quote_raw
    assert (
        res.to_json()
        == run_footnotes(
            _pair(t, roles["prior_10k"], roles["latest_10k"]),
            _pair(t, roles["prior_year_10q"], roles["latest_10q"]),
        ).to_json()
    )
