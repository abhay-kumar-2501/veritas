"""Seed-ticker accounting tests (offline; skipped when seed_cache is absent)."""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from veritas.engine.accounting_checks import evaluate_check, run_accounting
from veritas.engine.gating import gate
from veritas.engine.xbrl_normalize import normalize

SEED = Path(__file__).parents[2] / "seed_cache"


def _entry(ticker: str) -> dict[str, Any]:
    manifest = SEED / "manifest.json"
    facts = SEED / ticker / "companyfacts.json.gz"
    if not manifest.exists() or not facts.exists():
        pytest.skip(f"seed_cache/{ticker} absent")
    for e in json.loads(manifest.read_text())["entries"]:
        if e["ticker"] == ticker:
            out: dict[str, Any] = e
            return out
    pytest.skip(f"{ticker} not in seed manifest")


def _load(ticker: str) -> tuple[Any, Any, dict[str, Any]]:
    e = _entry(ticker)
    with gzip.open(SEED / ticker / "companyfacts.json.gz", "rt", encoding="utf-8") as fh:
        nf = normalize(json.load(fh))
    latest = nf.quarter_ends("Revenue")[0]
    ttm = nf.ttm("Revenue", latest)
    g = gate(e.get("sic"), True, ttm.value if ttm else None)
    return nf, g, e


def test_nvda_a1_not_critical() -> None:
    nf, g, e = _load("NVDA")
    out = run_accounting(nf, g, int(e["cik"]), "NVDA")
    a1 = next(c for c in out.checks if c.check_id == "A1_DSO")
    assert a1.severity != "CRITICAL"
    # Also at every quarter in the 12-quarter window (incl. FY2024, the PRD example).
    for h in a1.history:
        r = evaluate_check("A1_DSO", nf, date.fromisoformat(h["period_end"]), g, 0, "NVDA")
        assert r.severity != "CRITICAL", h


@pytest.mark.parametrize("ticker", ["NVDA", "WMT", "TSLA", "BA"])
def test_q4_derivation_reconciles_to_fy(ticker: str) -> None:
    nf, _g, _e = _load(ticker)
    fys = nf.fiscal_years()[:3]
    assert fys
    for _start, end in fys:
        fy = nf.fy("Revenue", end)
        q4 = nf.quarter("Revenue", end)
        assert fy is not None and q4 is not None
        if q4.derived:
            nine, big = nf.components(q4)
            assert q4.value + nine.value == fy.value == big.value
        ttm = nf.ttm("Revenue", end)
        assert ttm is not None and ttm.value == pytest.approx(fy.value)


def test_wmt_receivables_immaterial() -> None:
    nf, g, e = _load("WMT")
    out = run_accounting(nf, g, int(e["cik"]), "WMT")
    a1 = next(c for c in out.checks if c.check_id == "A1_DSO")
    assert a1.state == "NOT_APPLICABLE"
