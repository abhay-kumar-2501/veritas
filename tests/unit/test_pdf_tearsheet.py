"""PDF tear-sheet tests (PRD §9, §11.2): exactly 2 pages, disclaimer, citations, language."""

from __future__ import annotations

import dataclasses
import io
import re
import time
from datetime import datetime
from pathlib import Path

import pytest
from pypdf import PdfReader

from tests.fixtures.sample_result import sample_result
from veritas.config import DISCLAIMER
from veritas.export.pdf_tearsheet import _ranked_findings, build_pdf, pdf_filename
from veritas.models import AnalysisResult, ModuleResult, ScoreBreakdown

BANNED = ("fraud", "manipulation", "misleading", "deceptive", "stuffing", "cooking the books")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _pages(pdf: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf))
    return [_norm(p.extract_text() or "") for p in reader.pages]


def _unavailable() -> AnalysisResult:
    base = sample_result(0)
    mods = {
        k: ModuleResult("unavailable", (), (), "no data for this filer", {})
        for k in ("A", "B", "C")
    }
    score = ScoreBreakdown(None, 0, 0, (), "INSUFFICIENT DATA", None, 0, ("A", "B", "C"))
    return dataclasses.replace(base, modules=mods, score=score, filings=(), banners=())  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def sample_pdf() -> bytes:
    return build_pdf(sample_result(0))


@pytest.fixture(scope="module")
def worst_pdf() -> bytes:
    return build_pdf(sample_result(50))


def test_two_pages_sample(sample_pdf: bytes) -> None:
    assert len(PdfReader(io.BytesIO(sample_pdf)).pages) == 2


def test_two_pages_worst_case(worst_pdf: bytes) -> None:
    assert len(PdfReader(io.BytesIO(worst_pdf)).pages) == 2


def test_two_pages_all_unavailable() -> None:
    pdf = build_pdf(_unavailable())
    pages = _pages(pdf)
    assert len(pages) == 2
    assert "Not assessed" in pages[1]
    assert "INSUFFICIENT DATA" in pages[0]


def test_two_pages_hostile_text() -> None:
    r = sample_result(3)
    nasty = dataclasses.replace(r, company_name="A&B <Corp> ≥ − " + "X" * 300)
    pages = _pages(build_pdf(nasty))
    assert len(pages) == 2


@pytest.mark.parametrize("fixture", ["sample_pdf", "worst_pdf"])
def test_disclaimer_on_both_pages(fixture: str, request: pytest.FixtureRequest) -> None:
    pdf = request.getfixturevalue(fixture)
    for text in _pages(pdf):
        assert _norm(DISCLAIMER) in text


def test_unavailable_has_disclaimer_both_pages() -> None:
    for text in _pages(build_pdf(_unavailable())):
        assert _norm(DISCLAIMER) in text


def test_sample_findings_accessions_present(sample_pdf: bytes) -> None:
    r = sample_result(0)
    page1 = _pages(sample_pdf)[0]
    shown = _ranked_findings(r)[:5]
    assert shown
    for f in shown:
        assert f.citations[0].accession in page1


def test_worst_case_shows_five_and_more_in_app(worst_pdf: bytes) -> None:
    r = sample_result(50)
    ranked = _ranked_findings(r)
    page1 = _pages(worst_pdf)[0]
    for f in ranked[:5]:
        assert f.citations[0].accession in page1
    assert f"+{len(ranked) - 5} more in app" in page1


def test_no_banned_words_outside_disclaimer(sample_pdf: bytes, worst_pdf: bytes) -> None:
    for pdf in (sample_pdf, worst_pdf, build_pdf(_unavailable())):
        for text in _pages(pdf):
            stripped = text.replace(_norm(DISCLAIMER), "").lower()
            for word in BANNED:
                assert word not in stripped


def test_labels_and_score_name(sample_pdf: bytes) -> None:
    text = " ".join(_pages(sample_pdf))
    assert "Narrative–Numbers Consistency Score".upper() in text.upper()
    assert "VERIFIED" not in text.upper()
    assert "CRITICAL" in text and "WARNING" in text


def test_page2_content(sample_pdf: bytes) -> None:
    p2 = _pages(sample_pdf)[1]
    for cid in ("A1", "A2", "A3", "A4", "A5"):
        assert cid in p2
    assert "[+" in p2 and "[-" in p2
    assert "uncalibrated" in p2
    assert "Not assessed: Capitalized software not tagged" in p2


def test_page1_content(sample_pdf: bytes) -> None:
    p1 = _pages(sample_pdf)[0]
    assert "SAMPLECO" in p1 and "9999999" in p1
    assert "0009999999-25-000010" in p1


def test_markup_escaped() -> None:
    r = sample_result(0)
    r2 = dataclasses.replace(r, company_name="Tom & Jerry <b>Inc</b>")
    p1 = _pages(build_pdf(r2))[0]
    assert "Tom & Jerry <b>Inc</b>" in p1


def test_filename() -> None:
    r = sample_result(0)
    assert pdf_filename(r) == "VERITAS_SAMPLECO_20250501.pdf"
    r2 = dataclasses.replace(r, as_of=datetime(2026, 1, 9))
    assert pdf_filename(r2) == "VERITAS_SAMPLECO_20260109.pdf"


def test_build_time_and_determinism() -> None:
    r = sample_result(50)
    t0 = time.perf_counter()
    a = build_pdf(r)
    assert time.perf_counter() - t0 < 5
    assert build_pdf(r) == a
    assert len(PdfReader(io.BytesIO(a)).pages) == 2


def test_samples_written(sample_pdf: bytes, worst_pdf: bytes) -> None:
    Path("/tmp/veritas_sample.pdf").write_bytes(sample_pdf)
    Path("/tmp/veritas_worst.pdf").write_bytes(worst_pdf)


def test_quote_truncated_to_240() -> None:
    r = sample_result(0)
    long = "strong demand " * 40
    a = r.modules["A"]
    f0 = a.findings[0]
    cl = dataclasses.replace(
        f0.claims[0], citation=dataclasses.replace(f0.claims[0].citation, quote_raw=long.strip())
    )
    f1 = dataclasses.replace(f0, claims=(cl,))
    mod = dataclasses.replace(a, findings=(f1, *a.findings[1:]))
    r2 = dataclasses.replace(r, modules={**r.modules, "A": mod})
    p1 = _pages(build_pdf(r2))[0]
    m = re.search(r"“(strong demand.*?)”", p1)
    assert m and m.group(1).endswith("…") and len(m.group(1)) <= 240


SEEDS = ("NVDA", "WMT", "TSLA", "BA")
_SEED = Path(__file__).resolve().parents[2] / "seed_cache"


def _pipeline_available() -> bool:
    try:
        import veritas.pipeline  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    not (_SEED / "manifest.json").exists() or not _pipeline_available(),
    reason="seed_cache manifest or veritas.pipeline not available yet",
)
@pytest.mark.parametrize("ticker", SEEDS)
def test_seed_tickers_two_pages(ticker: str) -> None:
    from veritas.pipeline import analyze

    result = analyze(ticker, offline=True)
    pdf = build_pdf(result)
    pages = _pages(pdf)
    assert len(pages) == 2
    for text in pages:
        assert _norm(DISCLAIMER) in text
