"""Module B tests (PRD §7.3, §11.2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from veritas.engine.footnote_diff import (
    Segment,
    diff_note,
    hedging_drift,
    match_notes,
    normalize_comparison,
    run_footnotes,
    segment_filing,
    split_sentences,
    word_diff,
)
from veritas.models import DocRef

FIX = Path(__file__).parent.parent / "fixtures" / "footnotes"


def _ref(year: int, form: str = "10-K") -> DocRef:
    return DocRef(
        cik=1,
        ticker="FIX",
        form=form,
        accession=f"0000000001-{year % 100}-000001",
        filing_date=date(year, 2, 1),
        report_date=date(year - 1, 12, 31),
        doc_name="fix.htm",
        url="https://www.sec.gov/Archives/edgar/data/1/fix.htm",
    )


def _ix_pair(form: str = "10-K") -> tuple[DocRef, str, DocRef, str]:
    return (
        _ref(2024, form),
        (FIX / "ixbrl_prior.htm").read_text(),
        _ref(2025, form),
        (FIX / "ixbrl_current.htm").read_text(),
    )


def _rows(res: Any, key: str = "DebtDisclosureTextBlock", pair: str = "10-K") -> list[Any]:
    for n in res.extra["pairs"][pair]["notes"]:
        if n["key"] == key:
            return list(n["rows"])
    raise AssertionError(key)


def test_ixbrl_segmentation_nests_policy_and_follows_continuation() -> None:
    segs, method = segment_filing((FIX / "ixbrl_current.htm").read_text(), "10-K")
    assert method == "ixbrl"
    assert [s.concept for s in segs] == [
        "DebtDisclosureTextBlock",
        "RevenueFromContractWithCustomerTextBlock",
        "SegmentReportingDisclosureTextBlock",
    ]  # DebtPolicyTextBlock is nested, not its own segment
    debt = segs[0].text
    assert "Principal repayments" in debt  # continuedAt chain followed
    assert "Debt policy" in debt  # nested policy kept inside its note
    assert "HIDDENHEADERTEXT" not in debt
    assert segs[0].category == "debt" and segs[1].category == "revenue"
    assert segs[2].category is None


def test_header_fallback_and_renumbered_notes_match_by_title() -> None:
    res = run_footnotes(
        None,
        (
            _ref(2024, "10-Q"),
            (FIX / "header_prior.htm").read_text(),
            _ref(2025, "10-Q"),
            (FIX / "header_current.htm").read_text(),
        ),
    )
    pair = res.extra["pairs"]["10-Q"]
    assert pair["method"] == {"prior": "header", "current": "header"}
    status = {n["title"]: n["status"] for n in pair["notes"]}
    assert status["Debt"] == "MATCHED"  # Note 3 -> Note 4, matched by title not number
    assert status["Leases"] == "NEW NOTE"
    assert res.status == "partial"


def test_renumbered_ixbrl_note_matched_by_concept() -> None:
    p = Segment("DebtDisclosureTextBlock", "Debt", "Note 4 - Debt\nText one.", "debt")
    c = Segment("DebtDisclosureTextBlock", "Borrowings and Credit", "Note 5 - X\nOther.", "debt")
    out = match_notes([p], [c])
    assert out == [(p, c)]


def test_unmatched_notes_are_new_and_removed() -> None:
    p = Segment(None, "Pensions", "Pension plan covers employees in several plans.", None)
    c = Segment(None, "Leases", "The Company leases office space for terms.", None)
    out = match_notes([p], [c])
    assert (None, c) in out and (p, None) in out


def test_normalization_makes_amounts_and_years_equal() -> None:
    a = normalize_comparison("The balance was $4.1 billion in 2024.")
    b = normalize_comparison("The balance was $4.3 billion in 2025.")
    assert a == b == "the balance was <amt> in <year>."
    assert normalize_comparison("On March 31, 2024 and 12/31/2023") == "on <date> and <date>"
    assert normalize_comparison("grew 5% or 40 basis points") == "grew <pct> or <pct>"


def test_amount_year_change_and_moved_text_are_absorbed() -> None:
    res = run_footnotes(_ix_pair(), None)
    rows = _rows(res)
    texts = " ".join((r["current"] or r["prior"] or "") for r in rows)
    assert "term loan" not in texts  # $4.1B/2024 == $4.3B/2025
    assert "Interest on the revolving facility" not in texts  # moved, not edited
    assert {r["kind"] for r in rows} == {"MODIFIED", "INSERTED"}


def test_modal_softening_detected_and_material() -> None:
    res = run_footnotes(_ix_pair(), None)
    mod = [r for r in _rows(res) if r["kind"] == "MODIFIED"]
    assert len(mod) == 1 and mod[0]["softened"] and mod[0]["material"]
    assert mod[0]["ratio"] is not None and mod[0]["ratio"] >= 0.85  # material only via softening
    assert ["delete", "will"] in mod[0]["ops"] and ["insert", "may"] in mod[0]["ops"]


def test_modal_phrase_longest_first_and_no_softening_when_strengthened() -> None:
    _, _, soft = word_diff(
        "We will be able to comply with the covenant at all times.",
        "We may not be able to comply with the covenant at all times.",
    )
    assert soft
    _, _, hard = word_diff(
        "The lender may waive the covenant.", "The lender will waive the covenant."
    )
    assert not hard


def test_material_edit_fires_and_does_not_fire() -> None:
    res = run_footnotes(_ix_pair(), None)
    by_text = {(r["current"] or r["prior"]): r for r in _rows(res)}
    fire = next(r for k, r in by_text.items() if k.startswith("In the fourth quarter"))
    assert fire["material"]
    short = by_text["A default occurred."]
    assert not short["material"]  # < 12 words
    asu = next(r for k, r in by_text.items() if "Accounting Standards Update" in k)
    assert not asu["material"]  # ASU exclusion
    seg_rows = _rows(res, "SegmentReportingDisclosureTextBlock")
    assert len(seg_rows) == 1 and not seg_rows[0]["material"]  # not a category note
    assert [f.title for f in res.findings] == [
        "Material edit (modified): Debt",
        "Material edit (inserted): Debt",
    ]


def test_findings_categories_for_10k_and_10q() -> None:
    k = run_footnotes(_ix_pair("10-K"), None)
    q = run_footnotes(None, _ix_pair("10-Q"))
    assert {f.category for f in k.findings} == {"footnotes"}
    assert {f.category for f in q.findings} == {"footnotes_10q"}
    assert all(f.severity == "WARNING" and f.module == "B" for f in k.findings)
    assert k.status == "ok" and q.status == "partial"
    assert run_footnotes(None, None).status == "unavailable"
    # hedging is a 10-K-pair feature only
    assert q.extra["hedging"]["status"] == "NOT_ASSESSED"


def test_citation_invariant_on_note_texts() -> None:
    res = run_footnotes(_ix_pair(), None)
    assert res.findings
    for f in res.findings:
        c = f.citations[0]
        assert c.section.startswith("Note: ")
        assert c.char_start is not None and c.char_end is not None and c.quote_raw
        key = c.section[len("Note: ") :]
        side = "prior" if c.accession == _ref(2024).accession else "current"
        text = res.extra["note_texts"]["10-K"][key][side]
        assert text[c.char_start : c.char_end] == c.quote_raw


def test_deleted_sentence_cites_prior_filing() -> None:
    res = run_footnotes(_ix_pair(), None)
    prior, cur = _ix_pair()[1], _ix_pair()[3]
    swapped = run_footnotes((_ref(2024), cur, _ref(2025), prior), None)
    deleted = [f for f in swapped.findings if "deleted" in f.title]
    assert deleted and deleted[0].citations[0].accession == _ref(2024).accession
    assert res.status == "ok"


def test_payload_shape_matches_sample_contract() -> None:
    res = run_footnotes(_ix_pair(), None)
    pair = res.extra["pairs"]["10-K"]
    assert {"prior", "current", "notes"} <= pair.keys()
    note = pair["notes"][0]
    assert {"key", "title", "category", "status", "counts", "rows"} <= note.keys()
    assert set(note["counts"]) == {"inserted", "deleted", "modified", "softened", "material"}
    row = note["rows"][0]
    assert {"kind", "prior", "current", "material", "softened"} <= row.keys()
    assert set(res.extra["hedging"]) >= {
        "status",
        "prior_rate",
        "current_rate",
        "prior_hits",
        "current_hits",
        "prior_tokens",
        "current_tokens",
    }


def test_residue_cap_marks_truncated_and_unscored() -> None:
    prior = "\n".join(
        f"Alpha{i} sentence number {i} recognizes revenue estimate." for i in range(401)
    )
    cur = "\n".join(f"Beta{i} different statement {i} about reserve allowance." for i in range(401))
    nd = diff_note(prior, cur, True)
    assert nd.truncated and nd.rows == []
    ok = diff_note(prior[:2000], cur[:2000], True)
    assert not ok.truncated


def _corpus(n_tokens: int, n_hits: int) -> str:
    words = ["may"] * n_hits + ["filler"] * (n_tokens - n_hits)
    return " ".join(words)


def test_hedging_minimum_token_rule() -> None:
    out = hedging_drift(_corpus(1499, 5), _corpus(1500, 60))
    assert out["status"] == "INSUFFICIENT_TEXT"
    out = hedging_drift(_corpus(1500, 5), _corpus(1500, 60))
    assert out["status"] == "FIRED"


def test_hedging_each_condition_blocks_firing() -> None:
    assert hedging_drift(_corpus(2000, 20), _corpus(2000, 40))["status"] == "FIRED"
    # relative increase <= 15%
    assert hedging_drift(_corpus(2000, 40), _corpus(2000, 45))["status"] == "NO_DRIFT"
    # absolute increase < 1.5 per 1,000 (but relative > 15% and hits +4)
    assert hedging_drift(_corpus(4000, 8), _corpus(4000, 12))["status"] == "NO_DRIFT"
    # hit count up by < 5 even though rates move enough
    assert hedging_drift(_corpus(1500, 10), _corpus(1500, 14))["status"] == "NO_DRIFT"


def test_hedging_finding_emitted_in_run() -> None:
    # tiny fixtures are below the 1,500-token floor: no hedging finding
    res = run_footnotes(_ix_pair(), None)
    assert res.extra["hedging"]["status"] == "INSUFFICIENT_TEXT"
    assert not [f for f in res.findings if f.category == "hedging"]


def test_sentence_spans_are_verbatim_slices() -> None:
    text = "Acme Inc. owed $4.1 million. See No. 5 here.\nSecond line follows. Third one!"
    spans = split_sentences(text)
    assert [text[s.start : s.end] for s in spans] == [
        "Acme Inc. owed $4.1 million.",
        "See No. 5 here.",
        "Second line follows.",
        "Third one!",
    ]


def test_determinism() -> None:
    a = run_footnotes(_ix_pair(), _ix_pair("10-Q"))
    b = run_footnotes(_ix_pair(), _ix_pair("10-Q"))
    assert a.to_json() == b.to_json()


# ---- audit fixes -----------------------------------------------------------
def test_category_uses_title_before_concept() -> None:
    from veritas.engine.footnote_diff import _category

    concept = "InvestmentsInDebtAndMarketableEquitySecuritiesDisclosureTextBlock"
    assert _category("Cash Equivalents and Marketable Securities", concept) is None
    assert _category("Investments", concept) is None
    assert _category("Debt", concept) == "debt"
    # no heading title: the concept is the only evidence
    assert _category("Investments In Debt", concept, from_heading=False) == "debt"


def test_federally_recognized_is_not_a_recognition_term() -> None:
    from veritas.engine.footnote_diff import _is_material

    tribes = "Some stores are located on land owned by federally recognized Native American tribes in several states."  # noqa: E501
    assert not _is_material("INSERTED", None, tribes, None, False)
    real = "The Company recognizes revenue when control of the promised goods transfers to the customer."  # noqa: E501
    assert _is_material("INSERTED", None, real, None, False)


def test_deleting_a_modal_is_not_softening() -> None:
    _, _, soft = word_diff(
        "The Company will comply with the financial covenant at all times during the term.",
        "The Company complies with the financial covenant at all times during the term.",
    )
    assert not soft
    _, _, real = word_diff(
        "The Company will comply with the financial covenant at all times during the term.",
        "The Company may comply with the financial covenant at all times during the term.",
    )
    assert real


def test_wrapped_lines_join_and_st_abbreviation() -> None:
    text = "Terms may change\nbased, in part, on use. Next sentence."
    assert [text[s.start : s.end] for s in split_sentences(text)] == [
        "Terms may change\nbased, in part, on use.",
        "Next sentence.",
    ]
    heading = "Note 5 - Debt\nThe Company borrowed."
    assert len(split_sentences(heading)) == 2  # a heading is not joined to the next line
    st = "We lease a store on Main St. in Dallas. Rent is fixed."
    assert len(split_sentences(st)) == 2


def test_explanation_names_matched_term() -> None:
    res = run_footnotes(_ix_pair(), None)
    inserted = next(f for f in res.findings if "inserted" in f.title)
    assert "'covenant'" in inserted.explanation or "'waiver'" in inserted.explanation


def test_non_note_textblocks_excluded() -> None:
    def blk(prefix: str, concept: str, i: int, body: str) -> str:
        return (
            f'<div><ix:nonNumeric name="{prefix}:{concept}TextBlock" id="b{i}">'
            f"<p>{body}</p></ix:nonNumeric></div>"
        )

    doc = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>'
        + blk("dei", "DocumentsIncorporatedByReference", 1, "Portions of the proxy.")
        + blk(
            "cyd",
            "CybersecurityRiskBoardOfDirectorsOversight",
            2,
            "Our Board of Directors oversees risk.",
        )
        + blk("ecd", "MtrlTermsOfTrdArr", 3, "Insider trading arrangements.")
        + blk("us-gaap", "DebtDisclosure", 4, "Debt note text.")
        + blk("us-gaap", "LeasesDisclosure", 5, "Lease note text.")
        + blk("us-gaap", "IncomeTaxDisclosure", 6, "Tax note text.")
        + blk("us-gaap", "SegmentDisclosure", 7, "Segment note text.")
        + "</body></html>"
    )
    segs, method = segment_filing(doc, "10-K")
    assert method == "ixbrl"
    assert [s.concept for s in segs] == [
        "DebtDisclosureTextBlock",
        "LeasesDisclosureTextBlock",
        "IncomeTaxDisclosureTextBlock",
        "SegmentDisclosureTextBlock",
    ]


def test_explanation_uses_full_matched_word() -> None:
    from veritas.engine.footnote_diff import _explain, _term

    sentence = "Management revised its estimate of the allocation of shared costs for the period."
    assert _term(None, sentence) == "estimate"
    assert _term(None, "Revenue is recognized when control transfers.") == "recognized"
    text = _explain({"kind": "INSERTED", "softened": False, "term": "estimate"}, "Debt", "debt")
    assert "cites 'estimate'" in text
    assert "cites" in _explain(
        {"kind": "DELETED", "softened": False, "term": "waiver"}, "D", "debt"
    )
