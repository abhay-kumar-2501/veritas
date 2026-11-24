"""Seed-ticker smoke tests for Module C (skipped when the seed is absent)."""

from __future__ import annotations

import json

import pytest

from tests.unit.test_footnote_seed import load_doc
from veritas.config import SEED_DIR
from veritas.engine.proxy_xray import run_proxy

MANIFEST = SEED_DIR / "manifest.json"
pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="seed_cache absent")


def _entries() -> list[dict[str, object]]:
    return list(json.loads(MANIFEST.read_text())["entries"]) if MANIFEST.exists() else []


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: str(e["ticker"]))
def test_seed_proxy_not_error_and_quotes_verbatim(entry: dict[str, object]) -> None:
    roles = entry["roles"]
    assert isinstance(roles, dict)
    t = str(entry["ticker"])
    px = load_doc(t, roles["proxy"])
    k = load_doc(t, roles["latest_10k"])
    res = run_proxy(px, k, 1.0, 100.0, 50.0, 0.1, 0.1, False)
    assert res.status in ("ok", "low_confidence", "unavailable")
    cda = res.extra["cda_text"]
    for m in res.extra["metrics"]:
        c = m["citation"]
        assert cda[c["char_start"] : c["char_end"]] == c["quote_raw"]
    if res.status == "low_confidence":
        assert res.findings == ()
