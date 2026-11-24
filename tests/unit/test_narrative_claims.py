"""Tests for sentence splitting and claim extraction (PRD §7.2.3, §7.2.4, §7.2.6)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from veritas.engine.html_text import html_to_text
from veritas.engine.narrative import display_quote, extract_claims, split_sentences
from veritas.engine.sections import (
    LABEL_8K,
    Section,
    locate_earnings_release,
    locate_mda,
)
from veritas.models import Claim, DocRef

HTML_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "html"
PERIOD = date(2025, 12, 31)


def _doc(form: str = "10-K", report_date: date | None = PERIOD) -> DocRef:
    return DocRef(
        cik=1,
        ticker="ACME",
        form=form,
        accession="0000000001-26-000001",
        filing_date=date(2026, 2, 20),
        report_date=report_date,
        doc_name="acme.htm",
        url="https://www.sec.gov/Archives/edgar/data/1/000000000126000001/acme.htm",
    )


def _text(name: str) -> str:
    return html_to_text((HTML_DIR / name).read_bytes())


def _tenk_claims() -> tuple[Section, tuple[Claim, ...]]:
    sec = locate_mda(_text("tenk_toc_item7.htm"), "10-K")
    return sec, extract_claims(sec, _doc())


def _quotes(claims: tuple[Claim, ...]) -> list[str]:
    return [c.citation.quote_raw or "" for c in claims]


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------
def test_split_sentences_abbreviations_and_decimals() -> None:
    text = (
        "Sales to Acme Corp. and Beta Inc. in the U.S. grew 12.5% vs. last year. "
        "Margins were approx. 41.5% in Dec. 2025, e.g. in Europe. Next sentence here!"
    )
    spans = split_sentences(text)
    sentences = [text[s:e] for s, e in spans]
    assert sentences == [
        "Sales to Acme Corp. and Beta Inc. in the U.S. grew 12.5% vs. last year.",
        "Margins were approx. 41.5% in Dec. 2025, e.g. in Europe.",
        "Next sentence here!",
    ]


def test_split_sentences_never_crosses_lines_and_offsets_are_exact() -> None:
    text = "First line ends here\nSecond line. Third (quoted) part.\n\n  Indented one."
    spans = split_sentences(text)
    assert [text[s:e] for s, e in spans] == [
        "First line ends here",
        "Second line.",
        "Third (quoted) part.",
        "Indented one.",
    ]


def test_split_requires_capital_after_period() -> None:
    text = "Revenue was $4.1 billion. in total the year was good. Then it ended."
    assert [text[s:e] for s, e in split_sentences(text)] == [
        "Revenue was $4.1 billion. in total the year was good.",
        "Then it ended.",
    ]


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------
def test_claims_from_10k_fixture() -> None:
    sec, claims = _tenk_claims()
    by_quote = {(c.cluster, c.citation.quote_raw) for c in claims}
    assert (
        "DEMAND",
        "Revenue increased 18% in fiscal 2025, driven by strong demand for our data center "
        "products across all regions.",
    ) in by_quote
    assert (
        "MARGIN",
        "Gross margin rose 220 basis points to 41.5% in fiscal 2025 as a result of operating "
        "leverage on higher volumes.",
    ) in by_quote
    assert (
        "ONETIME",
        "Restructuring charges of $45.2 million in fiscal 2025 were non-recurring and related "
        "to the closure of the Austin facility.",
    ) in by_quote
    assert (
        "DEMAND",
        "Sales to Acme Corp. and Beta Inc. in the U.S. grew 12.5% year over year on record "
        "orders from hyperscale customers.",
    ) in by_quote
    assert len(claims) == 4


def test_negation_rejected() -> None:
    _, claims = _tenk_claims()
    joined = " ".join(_quotes(claims))
    assert "did not see strong demand" not in joined
    assert "softer pipeline" not in joined


def test_negation_window_is_six_tokens() -> None:
    near = "We did not see strong demand in the consumer segment this year at all."
    far = (
        "Despite no help from pricing in the first half overall, strong demand for "
        "servers lifted revenue."
    )
    text = f"{near}\n{far}"
    sec = Section(status="OK", text=text, word_count=len(text.split()), label="10-K Item 7")
    quotes = _quotes(extract_claims(sec, _doc()))
    assert quotes == [far]


def test_negative_contraction_rejected() -> None:
    text = "Customers didn't place strong orders for our legacy products during the year."
    sec = Section(status="OK", text=text, word_count=12, label="10-K Item 7")
    assert extract_claims(sec, _doc()) == ()


def test_forward_looking_rejected() -> None:
    _, claims = _tenk_claims()
    assert not any("We expect robust demand" in q for q in _quotes(claims))


def test_boilerplate_paragraph_rejected() -> None:
    _, claims = _tenk_claims()
    joined = " ".join(_quotes(claims))
    assert "record backlog" not in joined  # paragraph mentions forward-looking statements
    assert "pricing power" not in joined  # paragraph mentions the PSLRA


def test_out_of_section_and_data_rows_and_short_sentences_rejected() -> None:
    _, claims = _tenk_claims()
    joined = " ".join(_quotes(claims))
    assert "Historically strong demand" not in joined  # Item 1A
    assert "interest rate risk" not in joined  # Item 7A
    assert "Strong demand drove record results" not in joined  # data row
    assert "Record bookings." not in _quotes(claims)  # < 8 words


def test_claim_period_section_and_citation_fields() -> None:
    sec, claims = _tenk_claims()
    for c in claims:
        assert c.period_end == PERIOD
        assert c.citation.section == "10-K Item 7"
        assert c.citation.accession == "0000000001-26-000001"
        assert c.citation.report_date == PERIOD
        assert c.citation.xbrl_tag is None


def test_no_claims_without_period_or_section() -> None:
    sec, _ = _tenk_claims()
    assert extract_claims(sec, _doc(report_date=None)) == ()
    missing = Section(status="NOT_FOUND", text="", word_count=0, label="10-K Item 7")
    assert extract_claims(missing, _doc()) == ()


def test_earnings_release_claims_use_8k_label() -> None:
    sec = locate_earnings_release(_text("earnings_release.htm"))
    claims = extract_claims(sec, _doc(form="8-K"))
    assert {c.cluster for c in claims} == {"DEMAND", "MARGIN"}
    assert all(c.citation.section == LABEL_8K for c in claims)
    # Empty label falls back to the 8-K label.
    unlabeled = Section(status="OK", text=sec.text, word_count=sec.word_count, label="")
    assert all(c.citation.section == LABEL_8K for c in extract_claims(unlabeled, _doc("8-K")))


ALL_FIXTURE_SECTIONS = [
    ("tenk_toc_item7.htm", "10-K"),
    ("tenq_part1_vs_part2.htm", "10-Q"),
    ("earnings_release.htm", "8-K"),
    ("earnings_release_fls_first.htm", "8-K"),
    ("ex13", "10-K"),
]


@pytest.mark.parametrize(("name", "form"), ALL_FIXTURE_SECTIONS)
def test_citation_invariant_over_fixtures(name: str, form: str) -> None:
    if name == "ex13":
        sec = locate_mda(
            _text("tenk_ex13_reference.htm"), form, ex13_text=_text("ex13_annual_report.htm")
        )
    elif form == "8-K":
        sec = locate_earnings_release(_text(name))
    else:
        sec = locate_mda(_text(name), form)
    assert sec.status == "OK"
    claims = extract_claims(sec, _doc(form=form))
    assert claims, f"fixture {name} should yield at least one claim"
    for c in claims:
        cit = c.citation
        assert cit.char_start is not None and cit.char_end is not None
        assert sec.text[cit.char_start : cit.char_end] == cit.quote_raw


def test_extraction_is_deterministic() -> None:
    _, a = _tenk_claims()
    _, b = _tenk_claims()
    assert [c.to_json() for c in a] == [c.to_json() for c in b]


def test_display_quote_truncation() -> None:
    short = "word " * 10
    assert display_quote(short.strip()) == short.strip()
    long = " ".join(f"w{i}" for i in range(75))
    shown = display_quote(long)
    assert shown.endswith("…")
    assert shown[:-1].split() == [f"w{i}" for i in range(60)]
    assert long.startswith(shown[:-1])


@pytest.mark.parametrize(
    "sentence",
    [
        # TSLA 10-Q/10-K wording: aspiration, not an achieved result.
        "We continue to ramp production and build and optimize our manufacturing capacity, "
        "expand our operations while focusing on further cost reductions and operational "
        "efficiencies to enable increased deliveries and deployments.",
        # WMT 10-K/10-Q strategy sentence.
        "This includes increasing comparable store and club sales through increasing "
        "membership, accelerating eCommerce sales growth and expansion of omnichannel "
        "initiatives that complement our strategy.",
        "Our goal is to deliver record bookings across every region in our industrial business.",
        "We reorganized the sales force in order to sustain robust demand from key accounts.",
    ],
)
def test_intent_sentences_rejected(sentence: str) -> None:
    sec = Section(status="OK", text=sentence, word_count=len(sentence.split()), label="10-K")
    assert extract_claims(sec, _doc()) == ()


def test_strategic_adjective_is_not_intent() -> None:
    sentence = (
        "The increase for fiscal 2026 was primarily due to operational efficiencies and higher "
        "margins in fuel, partially offset by charges related to strategic supply chain decisions."
    )
    sec = Section(status="OK", text=sentence, word_count=len(sentence.split()), label="10-K")
    assert [c.cluster for c in extract_claims(sec, _doc())] == ["MARGIN"]


@pytest.mark.parametrize(
    "sentence",
    [
        # BA 10-K: the intent word follows the claim, so the claim stands.
        "Outside of the U.S., we are seeing similar solid demand as governments prioritize "
        "security, defense technology and global cooperation given evolving threats.",
        "Revenue grew 14% on strong demand from airlines, and we continue to focus on quality.",
    ],
)
def test_intent_after_cluster_match_is_accepted(sentence: str) -> None:
    sec = Section(status="OK", text=sentence, word_count=len(sentence.split()), label="10-K")
    claims = extract_claims(sec, _doc())
    assert [c.cluster for c in claims] == ["DEMAND"]
    assert claims[0].citation.quote_raw == sentence


def test_strategy_noun_rejects_even_after_match() -> None:
    sentence = (
        "Robust demand for our services in fiscal 2025 reflected the success of our strategy."
    )
    sec = Section(status="OK", text=sentence, word_count=len(sentence.split()), label="10-K")
    assert extract_claims(sec, _doc()) == ()
