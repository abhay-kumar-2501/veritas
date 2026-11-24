"""HTML builders: escaping, labels, designed empty states, charts."""

from __future__ import annotations

import dataclasses

from tests.fixtures.sample_result import sample_checks, sample_result
from veritas.lexicons import BANNED_WORDS
from veritas.ui import charts
from veritas.ui import components as c

EVIL = '<script>alert("x")</script> & "quoted" $5 million'


def _assert_escaped(markup: str) -> None:
    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup


def test_esc_encodes_markup_and_dollar() -> None:
    out = c.esc(EVIL)
    _assert_escaped(out)
    assert "&quot;" in out
    assert "$" not in out and "&#36;" in out


def test_safe_url_only_links_sec_hosts() -> None:
    assert c.safe_url("https://www.sec.gov/Archives/x.htm")
    assert c.safe_url("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
    assert c.safe_url("javascript:alert(1)") is None
    assert c.safe_url("https://evil.example.com/") is None
    assert c.safe_url('https://www.sec.gov/"onmouseover="x') is None


def test_severity_labels_exact() -> None:
    assert [c.sev_label(s) for s in ("CRITICAL", "WARNING", "NO_FLAG", None)] == [
        "CRITICAL",
        "WARNING",
        "NO FLAG",
        "N/A",
    ]


def test_finding_card_escapes_quote_title_and_explanation() -> None:
    res = sample_result()
    f = res.modules["A"].findings[0]
    claim = f.claims[0]
    evil_claim = dataclasses.replace(
        claim, citation=dataclasses.replace(claim.citation, quote_raw=EVIL)
    )
    f2 = dataclasses.replace(f, claims=(evil_claim,), title=EVIL, explanation=EVIL)
    out = c.finding_card(f2)
    _assert_escaped(out)
    assert out.count("&lt;script&gt;") == 3
    assert "is-contra" in out  # contradiction connector


def test_redline_row_escapes_both_sides() -> None:
    row = {"kind": "MODIFIED", "prior": EVIL, "current": EVIL + " may not", "material": True}
    out = c.redline_row(row, 0, "p", "c")
    assert "<script>" not in out
    assert "<ins>" in out
    ins_row = {"kind": "INSERTED", "prior": None, "current": EVIL}
    _assert_escaped(c.redline_row(ins_row, 0, "p", "c"))


def test_word_diff_marks_changes() -> None:
    left, right = c.word_diff("The Company will remain", "The Company may not remain")
    assert "<del>will</del>" in left
    assert "<ins>may not</ins>" in right


def test_proxy_table_and_notes_escape() -> None:
    out = c.proxy_table([{"metric": EVIL, "context": EVIL, "weight": None, "sentence": EVIL}])
    _assert_escaped(out)
    assert "not parsed" in out
    _assert_escaped(c.notes_list([EVIL]))


def test_not_assessed_panel() -> None:
    out = c.not_assessed("C", "Proxy not available", "unavailable")
    assert "Not assessed: Proxy not available" in out
    _assert_escaped(c.not_assessed("B", EVIL, "error"))


def test_score_ring_states() -> None:
    assert "INSUFFICIENT" in c.score_ring(None)
    assert "--n:58" in c.score_ring(58)
    assert c.score_band(90) == "noflag"
    assert c.score_band(70) == "warning"
    assert c.score_band(40) == "critical"


def test_markup_is_single_line() -> None:
    res = sample_result()
    statuses = {k: m.status for k, m in res.modules.items()}
    for markup in (
        c.hero(),
        c.how_it_works(),
        c.identity(res, synthetic=True),
        c.score_panel(res.score, "S", {"accounting": 45}, statuses),
        c.finding_card(res.modules["A"].findings[0]),
    ):
        assert "\n" not in markup


def test_static_copy_has_no_banned_words() -> None:
    res = sample_result()
    statuses = {k: m.status for k, m in res.modules.items()}
    for markup in (
        c.hero(),
        c.how_it_works(),
        c.score_panel(res.score, "S", {}, statuses),
        c.module_chips(statuses, True),
    ):
        assert not BANNED_WORDS.search(markup)


def test_check_figure_and_missing_history() -> None:
    checks = {k.check_id: k for k in sample_checks()}
    for cid in ("A1_DSO", "A2_DIO", "A3_ACCRUALS", "A5_RESTRUCT"):
        fig = charts.check_figure(cid, checks[cid].title, checks[cid].history)
        assert fig is not None
        assert fig.layout.paper_bgcolor == charts.BG
    assert charts.check_figure("A4_CAPRATE", "x", ()) is None
    assert (
        charts.check_figure("A1_DSO", "x", ({"period_end": "2024-12-31", "value": None},)) is None
    )
    assert charts.band_note("A5_RESTRUCT").startswith("No band")


def test_human_labels_and_formats() -> None:
    assert c.human_key("COGS_q") == "COGS (quarter)"
    assert c.human_key("Inventory_end") == "Inventory (end)"
    assert c.human_key("Inventory_end_prior_year") == "Inventory (end, prior year)"
    assert c.human_key("delta_dio_days").startswith("Δ DIO")
    assert c.fmt_value("days_q", 92) == "92.0 d"
    assert c.fmt_value("dio_rel_change", 0.11634) == "+11.6%"
    assert c.fmt_value("COGS_q", 19_892_000_000) == "$19.9B"
    assert c.fmt_value("goodwill", 257_000_000) == "$257.0M"
    assert c.fmt_value("COGS_q", None) == "—"
    assert c.human_text("WARNING -> CRITICAL") == "WARNING → CRITICAL"
    assert "related party" in c.human_text("related_party")
    assert c.human_reason("no inventory tag reported").startswith("InventoryNet not tagged")


def test_deduction_zero_is_muted_and_no_label_row() -> None:
    res = sample_result()
    score = dataclasses.replace(res.score, deductions=(("hedging", 0),))
    out = c.score_panel(score, "S", {"hedging": 5}, {"A": "ok"})
    assert "is-zero" in out and "−0" not in out
    assert ">Label<" not in out


def test_citation_chip_has_title_and_ellipsis_span() -> None:
    cit = sample_result().filings[0]
    out = c.citation_link(cit)
    assert 'title="' in out and "vx-cite-text" in out


def test_context_badge_for_prior_period() -> None:
    f = dataclasses.replace(
        sample_result().modules["A"].findings[0], category="accounting_prior_period"
    )
    out = c.finding_card(f)
    assert "is-context" in out and "prior period · context · unscored" in out


def test_footnote_edit_row_and_sources_escape() -> None:
    row = c.footnote_edit_row(
        {
            "form": "10-Q",
            "note": EVIL,
            "kind": "MODIFIED",
            "prior": "will " + EVIL,
            "current": "may " + EVIL,
            "category": "related_party",
        }
    )
    _assert_escaped(row)
    assert "10-Q · unscored" in row and "related party" in row
    src = c.narrative_sources([{"form": "8-K", "status": "NOT_FOUND", "reason": EVIL}])
    _assert_escaped(src)
    assert "Narrative unavailable for this filing" in src
    block = c.text_block("a\n\nb " + EVIL)
    _assert_escaped(block)
    assert "<br><br>" in block


def test_formatter_percentages_multiples_money() -> None:
    assert c.fmt_value("goodwill_growth", 3.015) == "+301.5%"
    assert c.fmt_value("acquisitions_pct_prior_assets", 0.014) == "1.4%"
    assert c.fmt_value("roic", 0.233) == "23.3%"
    assert c.fmt_value("accrual_ratio", 0.057) == "5.7%"
    assert c.fmt_value("cap_rate_change_pp", 0.052) == "+5.2 pp"
    assert c.fmt_value("capex_da", 2.314) == "2.31×"
    assert c.fmt_value("fcf", 13_509_000_000) == "$13.5B"
    assert c.fmt_value("OCF_ttm_growth", -0.12) == "−12.0%"
    assert c.fmt_value("delta_DSO", 12.1) == "12.1 d"


def test_check_labels_and_display_tiles() -> None:
    checks = {k.check_id: k for k in sample_checks()}
    head = c.check_header(checks["A4_CAPRATE"])
    assert "A4 · Capitalization intensity" in head and "A4_CAPRATE" not in head
    tiles = c.display_metrics([checks["X_FCF"]])
    assert "Free cash flow" in tiles and "X_FCF" not in tiles


def test_reason_mapping_everywhere() -> None:
    raw = "no inventory tag reported"
    assert "InventoryNet not tagged" in c.human_text("Not applicable: " + raw)
    a2 = dataclasses.replace(sample_checks()[1], state="NOT_APPLICABLE", reason=raw)
    assert raw not in c.display_metrics([a2])


def test_current_period_citations_first() -> None:
    from datetime import date

    res = sample_result()
    old = dataclasses.replace(
        res.filings[1], report_date=date(2023, 12, 31), filing_date=date(2024, 2, 21)
    )
    cur = dataclasses.replace(
        res.filings[0], report_date=date(2024, 12, 31), filing_date=date(2025, 2, 20)
    )
    ordered = c.current_citations_first([old, old, cur], date(2024, 12, 31))
    assert ordered[0] is cur


def test_unscored_edit_uses_context_badge() -> None:
    row = c.footnote_edit_row({"form": "10-Q", "kind": "INSERTED", "current": "x"})
    assert "is-context" in row and "unscored" in row
    scored = c.footnote_edit_row(
        {"form": "10-K", "kind": "INSERTED", "current": "x", "explanation": "cites 'estimate'"}
    )
    assert "vx-badge warning" in scored and "is-context" not in scored
    assert "cites &#x27;estimate&#x27;" in scored  # engine wording shown unchanged
