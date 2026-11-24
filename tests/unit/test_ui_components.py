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
    assert "weight not parsed" in out
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
