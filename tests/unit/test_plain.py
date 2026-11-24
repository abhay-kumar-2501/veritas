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
        (100, "The story"),
        (85, "The story"),
        (84, "Mostly"),
        (60, "Mostly"),
        (59, "Several"),
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
        "outputs": {},
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
    a_points = [p for p in v.points if p.view == "[1] Overview"]
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
