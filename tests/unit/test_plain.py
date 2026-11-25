"""Plain-English layer (docs/decisions/ui.md D-610): every sentence must restate the
engine's own numbers, never add a judgement, and never use a banned word."""

from __future__ import annotations

import re
from functools import cache

import pytest

from veritas.config import SEED_DIR, SEED_TICKERS
from veritas.lexicons import BANNED_WORDS
from veritas.models import AnalysisResult
from veritas.pipeline import analyze
from veritas.ui import plain, story
from veritas.ui.components import fmt_money
from veritas.ui.views import footnote_edits

seeded = pytest.mark.skipif(
    not (SEED_DIR / "manifest.json").exists(), reason="seed cache not built"
)


@cache
def _r(ticker: str) -> AnalysisResult:
    return analyze(ticker, offline=True)


def _all_text(v: plain.Verdict) -> list[str]:
    return [v.headline, v.summary, *v.good, *v.unchecked] + [
        s for p in v.points for s in (p.title, p.text, p.analogy)
    ]


def test_static_copy_has_no_banned_words() -> None:
    texts: list[str] = [*plain.SEVERITY_PLAIN.values(), *plain.DEDUCTION_PLAIN.values()]
    texts += [s for pair in plain.MODULE_PLAIN.values() for s in pair]
    texts += [
        s for cp in plain.CHECK_PLAIN.values() for s in (cp.name, cp.question, cp.why, cp.analogy)
    ]
    texts += list(plain.GLOSSARY.values()) + list(plain.CLUSTER_TOPIC.values())
    texts += [s for pair in story.VIEW_INTROS.values() for s in pair]
    texts += [story.hero(), story.story_steps(), story.how_it_works()]
    for t in texts:
        assert not BANNED_WORDS.search(t), t
        assert "VERIFIED" not in t
        assert "Integrity Score" not in t


@pytest.mark.parametrize(
    ("score", "start"),
    [
        (100, "Only a few"),
        (85, "Only a few"),
        (84, "A mixed"),
        (60, "A mixed"),
        (59, "Many flags"),
        (None, "Not enough"),
    ],
)
def test_headline_bands_match_ring_bands(score: int | None, start: str) -> None:
    assert plain.headline_for(score).startswith(start)


def test_short_name() -> None:
    assert plain.short_name({"ticker": "BA", "company_name": "BOEING CO"}) == "Boeing"
    assert plain.short_name({"ticker": "XYZ", "company_name": "ACME WIDGETS INC."}) == (
        "Acme Widgets"
    )
    assert plain.short_name({"ticker": "XYZ", "company_name": "Apple Inc."}) == "Apple"


def test_edit_tie_lists_notes_instead_of_most() -> None:
    edits = [
        {"form": "10-K", "note": "Debt", "kind": "INSERTED"},
        {"form": "10-K", "note": "Leases", "kind": "INSERTED"},
        {"form": "10-Q", "note": "Debt", "kind": "INSERTED"},  # unscored: ignored
    ]
    v = plain.verdict({"ticker": "XYZ", "company_name": "X", "modules": {}}, edits)
    (p,) = v.points
    assert "2 notable edits" in p.text
    assert "most in" not in p.text
    assert "“Debt” and “Leases”" in p.text


def test_missing_inputs_fall_back_to_none() -> None:
    assert plain.numbers_sentence({"check_id": "A1_DSO", "outputs": {}, "inputs": {}}, "X") is None
    assert (
        plain.numbers_sentence(
            {"check_id": "A3_ACCRUALS", "outputs": {}, "inputs": {"NI_ttm": 1.0}}, "X"
        )
        is None
    )


def test_more_cash_than_profit_wording() -> None:
    check = {
        "check_id": "A3_ACCRUALS",
        "period_end": "2026-06-30",
        "outputs": {"ccr": 1.5},
        "inputs": {"NI_ttm": 100e6, "OCF_ttm": 150e6},
    }
    s = plain.numbers_sentence(check, "Acme")
    assert s is not None and "more cash than profit" in s and "¢" not in s


@seeded
@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_verdict_text_is_clean(ticker: str) -> None:
    r = _r(ticker)
    v = plain.verdict(r, footnote_edits(r.modules["B"]))
    for t in _all_text(v):
        assert not BANNED_WORDS.search(t), t
        assert "VERIFIED" not in t


@seeded
@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_verdict_points_mirror_scored_findings(ticker: str) -> None:
    """One point per scored flag: no flag invented, none dropped."""
    r = _r(ticker)
    edits = footnote_edits(r.modules["B"])
    v = plain.verdict(r, edits)
    a_flags = [
        f
        for f in r.modules["A"].findings
        if f.category == "accounting" and f.severity in ("CRITICAL", "WARNING")
    ]
    c_flags = [f for f in r.modules["C"].findings if f.category == "incentives"]
    has_10k_edits = any(e["form"] == "10-K" for e in edits)
    hedge = r.modules["B"].extra.get("hedging") or {}
    expected = (
        len(a_flags) + len(c_flags) + int(has_10k_edits) + int(hedge.get("status") == "FIRED")
    )
    assert len(v.points) == expected
    # severities of the accounting points equal the findings' severities
    a_points = [p for p in v.points if p.view == "below"]
    assert sorted(p.severity for p in a_points) == sorted(f.severity for f in a_flags)
    n10k = sum(1 for e in edits if e["form"] == "10-K")
    if n10k:
        assert f"{n10k} notable edit" in v.summary + " ".join(p.text for p in v.points)
    assert f"scores {r.score.score} out of 100" in v.summary


@seeded
@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_numbers_sentences_restate_engine_values(ticker: str) -> None:
    r = _r(ticker)
    for check in r.modules["A"].checks:
        s = plain.numbers_sentence(check, "X")
        if s is None:
            continue
        o, i = check.outputs, check.inputs
        if check.check_id == "A1_DSO":
            assert f"about {o['dso']:,.0f} day" in s
            assert f"({o['dso_prior_year']:,.0f} day" in s
        elif check.check_id == "A2_DIO":
            assert f"about {o['dio']:,.0f} day" in s
            assert f"({o['dio_prior_year']:,.0f} day" in s
        elif check.check_id == "A3_ACCRUALS":
            assert fmt_money(i["NI_ttm"]) in s and fmt_money(i["OCF_ttm"]) in s
            if i["NI_ttm"] > 0 and i["OCF_ttm"] < i["NI_ttm"]:
                cents = int(re.search(r"about (\d+)¢", s).group(1))  # type: ignore[union-attr]
                assert cents == round(i["OCF_ttm"] / i["NI_ttm"] * 100)
                assert abs(cents / 100 - o["ccr"]) < 0.006
        # direction word agrees with the sign of the change
        m = re.search(r"\b(up|down) (\d+) days from", s)
        if m and check.check_id in ("A1_DSO", "A2_DIO"):
            key = "delta_dso_days" if check.check_id == "A1_DSO" else "delta_dio_days"
            assert (m.group(1) == "up") == (o[key] > 0)


@seeded
def test_boeing_c1_plain_uses_engine_amounts() -> None:
    r = _r("BA")
    (c1,) = [f for f in r.modules["C"].findings if f.id == "C1"]
    s = plain.incentive_plain(c1)
    assert s is not None
    assert "$444M" in s and "$16M" in s


# --- Fable review blockers (D-611) -------------------------------------------------


@seeded
@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_b1_no_claim_that_management_said_nothing(ticker: str) -> None:
    r = _r(ticker)
    for f in r.modules["A"].findings:
        s = plain.finding_plain(f, "X") or ""
        assert "no comment" not in s
        if f.check is not None and f.check.check_id == "A3_ACCRUALS":
            assert "text scan" not in s  # the engine never pairs statements with A3


@seeded
@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_b2_guard_never_claims_non_escalation(ticker: str) -> None:
    r = _r(ticker)
    v = plain.verdict(r, footnote_edits(r.modules["B"]))
    for t in _all_text(v):
        assert "not escalated" not in t
        assert "escalat" not in t


def test_b3_c1_compares_amounts_correctly() -> None:
    more = {
        "id": "C1",
        "explanation": "A non-GAAP measure is used in incentive pay while SBC "
        "$444M vs free cash flow $16M (TTM); FCF − SBC = −$428M. Flagged.",
    }
    half = {
        "id": "C1",
        "explanation": "A non-GAAP measure is used in incentive pay while SBC "
        "$600M vs free cash flow $1.0B (TTM); FCF − SBC = $400M. Flagged.",
    }
    s1, s2 = plain.incentive_plain(more), plain.incentive_plain(half)
    assert s1 and "more than the $16M" in s1 and "all employees" in s1
    assert s2 and "at least half of the $1.0B" in s2 and "more than" not in s2
    for s in (s1, s2):
        assert "it paid" not in s and "often leaves out" not in s
        assert "did not check whether this company's figure does" in s


def test_b4_matched_term_not_overclaimed() -> None:
    s = plain.edit_plain(
        {
            "note": "Revenue",
            "kind": "INSERTED",
            "explanation": "A sentence was added in the Revenue note that cites "
            "'recognized'. Flagged for review.",
        }
    )
    assert "accounting terms we watch for" in s
    assert "usually signals" not in s


@seeded
@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_b5_no_data_checks_are_not_listed_as_passing(ticker: str) -> None:
    from veritas.engine.narrative import _has_no_data

    r = _r(ticker)
    v = plain.verdict(r, footnote_edits(r.modules["B"]))
    for k in r.modules["A"].checks:
        cp = plain.CHECK_PLAIN.get(k.check_id)
        if cp is None or not k.check_id.startswith("A"):
            continue
        if k.state != "COMPUTED" or _has_no_data(k):
            assert cp.name not in v.good, (ticker, k.check_id)
            listed = (*v.unchecked, *v.empty, *v.not_applicable)
            assert any(u.startswith(cp.name) for u in listed), (ticker, k.check_id)


def test_trend_uses_displayed_values() -> None:
    # displayed values are 119 and 106, so the stated change must be 13, not 14
    assert plain._trend(119.33, 105.63, "days") == "up 13 days from"
    assert plain._trend(40.83, 40.0, "days") == "up 1 day from"
    assert plain._trend(40.2, 39.9, "days") == "about the same as"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("receivables immaterial (AR < 2% of TTM revenue)", "customers owe it less than 2%"),
        ("inventory immaterial (inventory < 2% of TTM cost of sales)", "unsold goods are worth"),
        ("capitalized software immaterial (< 1% of TTM revenue)", "under 1% of a year's sales"),
        ("no inventory tag reported", "figure for unsold goods"),
        ("capitalized software not tagged", "couldn't find this figure"),
        ("TTM net income unavailable (4 discrete quarters required)", "not enough quarters"),
        ("Assets not tagged at 2026-06-30", "isn't in the company's data"),
        ("TTM revenue unavailable for the materiality gate", "isn't in the company's data"),
        ("financial issuer", "doesn't apply to banks"),
        ("something new from the engine", "the figures needed aren't available"),
    ],
)
def test_n1_each_engine_reason_gets_its_own_plain_text(reason: str, expected: str) -> None:
    out = plain._reason_plain(reason)
    assert expected in out
    if "inventory" in reason or "software" in reason:
        assert "customer" not in out


def test_verdict_accepts_dict_results() -> None:
    check = {
        "check_id": "A5_RESTRUCT",
        "state": "COMPUTED",
        "severity": "NO_FLAG",
        "outputs": {},
        "inputs": {},
    }
    v = plain.verdict({"ticker": "XYZ", "modules": {"A": {"checks": [check], "findings": []}}})
    assert v.good == ("Repeated “one-time” costs",)


# --- Fable review 2 (D-613) ---------------------------------------------------------


@seeded
@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_check_lists_agree_with_coverage(ticker: str) -> None:
    """'Number checks run: N of M' must match what the summary lists."""
    r = _r(ticker)
    v = plain.verdict(r, footnote_edits(r.modules["B"]))
    scored = [k for k in r.modules["A"].checks if k.check_id.startswith("A") and k.scored]
    flagged = [k for k in scored if k.state == "COMPUTED" and k.severity != "NO_FLAG"]
    a_unchecked = [u for u in v.unchecked if not u.startswith("How executives")]
    assert len(v.good) + len(v.empty) + len(flagged) == r.score.coverage_num
    assert len(a_unchecked) == r.score.coverage_den - r.score.coverage_num
    assert len(v.not_applicable) == len(scored) - r.score.coverage_den


def test_no_inventory_tag_is_not_called_unreported() -> None:
    out = plain._reason_plain("no inventory tag reported")
    assert "doesn't report" not in out and "couldn't find" in out


def _a3(ni: float, ocf: float, **outputs: object) -> dict[str, object]:
    return {
        "check_id": "A3_ACCRUALS",
        "period_end": "2026-06-30",
        "severity": outputs.pop("severity", "CRITICAL"),
        "annotations": outputs.pop("annotations", ()),
        "outputs": outputs,
        "inputs": {"NI_ttm": ni, "OCF_ttm": ocf},
    }


def test_a3_loss_and_cash_burn_wording() -> None:
    s = plain.numbers_sentence(_a3(-1e9, -4e8), "Acme") or ""
    assert "a loss of $1.0B" in s and "used up $400.0M of cash" in s
    assert "−" not in s and "-$" not in s and "¢" not in s


def test_a3_uses_engine_ratio_only() -> None:
    # profit under 2% of sales: the engine leaves ccr None, so no cents are printed
    s = plain.numbers_sentence(_a3(1e8, 5e7, ccr=None, accrual_ratio=0.001), "Acme") or ""
    assert "¢" not in s and "about 0%" not in s


def test_a3_profit_up_cash_down_is_explained() -> None:
    s = plain.numbers_sentence(
        _a3(
            1e8,
            1.2e8,
            ccr=1.2,
            OCF_ttm_growth=-0.25,
            annotations=("net income up YoY while operating cash flow fell ≥ 10%",),
        ),
        "Acme",
    )
    assert s and "more cash than profit" in s and "fell by about 25%" in s


def test_a4_flag_has_a_sentence_and_is_never_dropped() -> None:
    check = {
        "check_id": "A4_CAPRATE",
        "period_end": "2026-06-30",
        "state": "COMPUTED",
        "severity": "WARNING",
        "scored": True,
        "outputs": {"cap_rate": 0.32, "cap_rate_prior_year": 0.25, "cap_rate_change_pp": 0.07},
        "inputs": {},
    }
    s = plain.numbers_sentence(check, "Acme") or ""
    assert "about 32%" in s and "up 7 percentage points" in s and "(25%)" in s
    finding = {"category": "accounting", "severity": "WARNING", "check": check, "id": "A4"}
    v = plain.verdict(
        {"ticker": "XYZ", "modules": {"A": {"checks": [check], "findings": [finding]}}}
    )
    assert [p.title for p in v.points] == [plain.CHECK_PLAIN["A4_CAPRATE"].name]


def test_unscored_flag_is_not_a_point() -> None:
    check = {
        "check_id": "A3_ACCRUALS",
        "state": "COMPUTED",
        "severity": "WARNING",
        "scored": False,
        "outputs": {},
        "inputs": {"NI_ttm": 1.0, "OCF_ttm": 0.5},
    }
    finding = {"category": "accounting", "severity": "WARNING", "check": check, "id": "A3"}
    v = plain.verdict(
        {"ticker": "XYZ", "modules": {"A": {"checks": [check], "findings": [finding]}}}
    )
    assert v.points == ()


def test_trend_never_contradicts_engine_change() -> None:
    # displayed 26 vs 17 would say "up 9", but the engine's change is 9.8 points
    assert plain._trend(26.4, 16.6, "percentage points", delta=9.8) == (
        "up 9.8 percentage points from"
    )
    assert plain._trend(26.4, 17.45, "percentage points", delta=8.95) == (
        "up 9 percentage points from"
    )
    assert plain._trend(119.33, 105.63, "days", delta=13.7) == "up 13.7 days from"
    assert plain._trend(40.2, 39.9, "days", delta=0.3) == "about the same as"


def test_ccr_just_under_one_reads_as_much_cash() -> None:
    s = plain.numbers_sentence(_a3(1e8, 9.97e7, ccr=0.997), "Acme") or ""
    assert "100¢" not in s and "about as much cash as profit" in s
