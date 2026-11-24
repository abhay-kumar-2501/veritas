"""Seed-filing checks for MD&A location and the citation invariant (PRD §7.2.2, §7.2.6).

Skipped when seed_cache/ has not been built.
"""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from veritas.config import SEED_DIR
from veritas.engine.html_text import html_to_text
from veritas.engine.narrative import extract_claims
from veritas.engine.sections import Section, locate_earnings_release, locate_mda
from veritas.models import DocRef

MANIFEST = SEED_DIR / "manifest.json"
pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="seed cache not built")

PERIODIC_ROLES = {
    "latest_10k": "10-K",
    "prior_10k": "10-K",
    "latest_10q": "10-Q",
    "prior_year_10q": "10-Q",
}


def _entries() -> list[dict[str, Any]]:
    if not MANIFEST.exists():
        return []
    data = json.loads(MANIFEST.read_text())
    entries: list[dict[str, Any]] = data.get("entries", [])
    return entries


def _doc_path(entry: dict[str, Any], accession: str) -> Path | None:
    folder = accession.replace("-", "")
    for f in entry.get("files", []):
        parts = f["path"].split("/")
        if len(parts) >= 4 and parts[1] == "docs" and parts[2] == folder:
            return SEED_DIR / str(f["path"])
    return None


def _cases(prefix_filter: str | None) -> list[tuple[str, str, str, Path]]:
    out: list[tuple[str, str, str, Path]] = []
    for entry in _entries():
        for role, acc in sorted(entry.get("roles", {}).items()):
            if role in PERIODIC_ROLES:
                form = PERIODIC_ROLES[role]
            elif role.startswith("earnings_ex99"):
                form = "8-K"
            else:
                continue
            if prefix_filter is not None and form != prefix_filter:
                continue
            path = _doc_path(entry, acc)
            if path is not None:
                out.append((entry["ticker"], role, form, path))
    return out


def _section(form: str, path: Path) -> Section:
    text = html_to_text(gzip.decompress(path.read_bytes()))
    return locate_earnings_release(text) if form == "8-K" else locate_mda(text, form)


def _id(case: tuple[str, str, str, Path]) -> str:
    return f"{case[0]}-{case[1]}"


@pytest.mark.parametrize("case", _cases("10-K"), ids=_id)
def test_seed_10k_mda_found(case: tuple[str, str, str, Path]) -> None:
    _, _, form, path = case
    sec = _section(form, path)
    assert sec.status == "OK", sec.reason
    assert sec.label == "10-K Item 7"
    assert sec.word_count >= 1500


@pytest.mark.parametrize("case", _cases("10-Q"), ids=_id)
def test_seed_10q_mda_found(case: tuple[str, str, str, Path]) -> None:
    _, _, form, path = case
    sec = _section(form, path)
    assert sec.status == "OK", sec.reason
    assert sec.label == "10-Q Item 2"
    assert "unregistered sales" not in sec.text.lower()[:200]


@pytest.mark.parametrize("case", _cases(None), ids=_id)
def test_seed_citation_invariant(case: tuple[str, str, str, Path]) -> None:
    ticker, _, form, path = case
    sec = _section(form, path)
    doc = DocRef(
        cik=0,
        ticker=ticker,
        form=form,
        accession="0000000000-00-000000",
        filing_date=date(2026, 1, 1),
        report_date=date(2025, 12, 31),
        doc_name=path.name.removesuffix(".gz"),
        url="https://www.sec.gov/Archives/edgar/data/0/0/" + path.name.removesuffix(".gz"),
    )
    for claim in extract_claims(sec, doc):
        cit = claim.citation
        assert cit.char_start is not None and cit.char_end is not None
        assert sec.text[cit.char_start : cit.char_end] == cit.quote_raw
        assert cit.section == sec.label


@pytest.mark.parametrize("form", ["8-K", "EX-99.1"])
def test_seed_earnings_releases_located_and_cited(form: str) -> None:
    cases = _cases("8-K")
    if not cases:
        pytest.skip("no seed earnings releases")
    ok = 0
    for ticker, _, _, path in cases:
        sec = _section("8-K", path)
        if sec.status != "OK":
            continue
        ok += 1
        assert sec.label == "8-K EX-99.1"
        # Slide-deck releases are cut at the summary heading (D-312).
        assert sec.word_count < 2000, f"{ticker} release not cut ({sec.word_count} words)"
        doc = DocRef(
            cik=0,
            ticker=ticker,
            form=form,  # the data layer may label the exhibit either way
            accession="0000000000-00-000000",
            filing_date=date(2026, 1, 1),
            report_date=date(2025, 12, 31),
            doc_name=path.name.removesuffix(".gz"),
            url="https://www.sec.gov/Archives/edgar/data/0/0/" + path.name.removesuffix(".gz"),
        )
        for claim in extract_claims(sec, doc):
            cit = claim.citation
            assert cit.char_start is not None and cit.char_end is not None
            assert sec.text[cit.char_start : cit.char_end] == cit.quote_raw
            assert cit.section == "8-K EX-99.1"
    assert ok >= 1
