"""Module C tests (PRD §7.4, §11.2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from veritas.engine.proxy_xray import ROIC_FCF_NOTE, cda_span, run_proxy
from veritas.models import DocRef

FIX = Path(__file__).parent.parent / "fixtures" / "proxy"


def _ref(form: str = "DEF 14A") -> DocRef:
    return DocRef(1, "FIX", form, "0000000001-25-000030", date(2025, 4, 1), None, "p.htm", "u")


def _proxy(name: str) -> tuple[DocRef, str]:
    return _ref(), (FIX / name).read_text()


def _run(name: str = "def14a_weights.htm", **kw: Any) -> Any:
    args: dict[str, Any] = {
        "sbc_ttm": None,
        "revenue_ttm": None,
        "fcf_ttm": None,
        "goodwill_assets": None,
        "goodwill_assets_prior": None,
        "acquisition_guard": False,
    }
    args.update(kw)
    return run_proxy(_proxy(name), None, **args)


def test_weight_parsed_only_from_weight_table() -> None:
    res = _run()
    assert res.status == "ok"
    weights = {m["metric"]: m["weight"] for m in res.extra["metrics"] if m["weight"] is not None}
    assert weights == {"ADJ_EBITDA": 40.0, "REVENUE": 30.0, "TSR": 30.0}


def test_weight_none_without_weight_header() -> None:
    res = _run("def14a_noweight.htm")
    assert {m["metric"] for m in res.extra["metrics"]} >= {"ADJ_EBITDA", "REVENUE", "NONGAAP_EPS"}
    assert all(m["weight"] is None for m in res.extra["metrics"])


def test_glossary_only_adjusted_ebitda_not_counted() -> None:
    res = _run("def14a_glossary.htm")
    assert res.status == "low_confidence"
    assert res.extra["metrics"] == []
    assert res.findings == ()


def test_definition_sentence_and_heading_excluded_in_full_doc() -> None:
    res = _run()
    sents = [m["sentence"] for m in res.extra["metrics"]]
    assert not any("is defined as" in s for s in sents)
    assert not any("Free cash flow means" in s for s in sents)


def test_citation_invariant_on_cda_text() -> None:
    res = _run()
    cda = res.extra["cda_text"]
    assert res.extra["metrics"]
    for m in res.extra["metrics"]:
        c = m["citation"]
        assert c["section"] == "DEF 14A CD&A"
        assert cda[c["char_start"] : c["char_end"]] == c["quote_raw"] == m["sentence"]


def test_cda_longest_span_skips_toc() -> None:
    text = (FIX / "def14a_weights.htm").read_text()
    from veritas.engine.html_text import html_to_text

    plain = html_to_text(text)
    s, e, how = cda_span(plain)
    assert (
        how == "cda" and "Adjusted EBITDA" in plain[s:e] and "Table of Contents" not in plain[s:e]
    )


def test_unavailable_without_sources_and_short_item11() -> None:
    res = run_proxy(None, None, None, None, None, None, None, False)
    assert res.status == "unavailable" and res.findings == ()
    short = (
        _ref("10-K"),
        "<html><body><p>Item 11. Executive Compensation</p><p>See proxy.</p>"
        "<p>Item 12. Security Ownership</p></body></html>",
    )
    assert run_proxy(None, short, None, None, None, None, None, False).status == "unavailable"


def test_item11_fallback_with_enough_words() -> None:
    filler = "<p>" + " ".join(["word"] * 1600) + "</p>"
    body = (
        "<html><body><p>Item 11. Executive Compensation</p>"
        "<p>Compensation Discussion and Analysis</p>"
        + filler
        + "<p>The annual bonus target is tied to adjusted EBITDA and revenue growth payout.</p>"
        "<p>Summary Compensation Table</p><p>Item 12. Security Ownership</p></body></html>"
    )
    res = run_proxy(None, (_ref("10-K"), body), None, None, None, None, None, False)
    assert res.status == "ok" and res.extra["source"] == "10-K Item 11"


def test_low_confidence_has_no_flags_even_with_bad_ratios() -> None:
    res = _run(
        "def14a_glossary.htm",
        sbc_ttm=50.0,
        revenue_ttm=100.0,
        fcf_ttm=10.0,
        goodwill_assets=0.9,
        goodwill_assets_prior=0.1,
        acquisition_guard=True,
    )
    assert res.status == "low_confidence" and res.findings == ()


def test_c1_fires_on_each_condition_and_not_otherwise() -> None:
    fire = _run(sbc_ttm=12.0, revenue_ttm=100.0, fcf_ttm=1000.0)  # SBC/Revenue >= 10%
    assert [f.id for f in fire.findings] == ["C1"]
    f = fire.findings[0]
    assert f.category == "incentives" and f.severity == "WARNING" and f.module == "C"
    assert f.citations[0].quote_raw and "EBITDA" in f.citations[0].quote_raw
    assert [x.id for x in _run(sbc_ttm=3.0, revenue_ttm=1000.0, fcf_ttm=5.0).findings] == ["C1"]
    assert [x.id for x in _run(sbc_ttm=6.0, revenue_ttm=1000.0, fcf_ttm=5.0).findings] == ["C1"]
    assert _run(sbc_ttm=3.0, revenue_ttm=1000.0, fcf_ttm=100.0).findings == ()
    # FCF <= 0: only the revenue test applies
    assert _run(sbc_ttm=3.0, revenue_ttm=1000.0, fcf_ttm=-5.0).findings == ()
    assert _run().extra["checks"]["C1"]["state"] == "NOT_COMPUTABLE"


def test_c2_fires_and_does_not_fire() -> None:
    kw: dict[str, Any] = {"goodwill_assets": 0.40, "goodwill_assets_prior": 0.30}
    res = _run(**kw)
    assert [f.id for f in res.findings if f.id == "C2"] == ["C2"]
    assert any(
        f.id == "C2"
        for f in _run(
            goodwill_assets=0.40, goodwill_assets_prior=0.39, acquisition_guard=True
        ).findings
    )
    assert not any(
        f.id == "C2" for f in _run(goodwill_assets=0.40, goodwill_assets_prior=0.39).findings
    )
    assert not any(
        f.id == "C2"
        for f in _run(
            goodwill_assets=0.30, goodwill_assets_prior=0.10, acquisition_guard=True
        ).findings
    )
    # no revenue/M&A metric in the doc: not applicable
    assert _run("def14a_glossary.htm", **kw).findings == ()


def test_roic_fcf_note_neutral_wording() -> None:
    res = _run("def14a_noweight.htm")
    assert res.extra["notes"] == [ROIC_FCF_NOTE]
    assert "zero" not in ROIC_FCF_NOTE.lower() and "weight" not in ROIC_FCF_NOTE.lower()
    # the alias appears somewhere in the CD&A (even in a glossary): no note
    assert _run().extra["notes"] == []


def test_determinism() -> None:
    a = _run(sbc_ttm=12.0, revenue_ttm=100.0, fcf_ttm=1000.0)
    b = _run(sbc_ttm=12.0, revenue_ttm=100.0, fcf_ttm=1000.0)
    assert a.to_json() == b.to_json()


# ---- audit fixes -----------------------------------------------------------
def _doc(*paras: str) -> tuple[DocRef, str]:
    body = "".join(f"<p>{p}</p>" for p in paras)
    return _ref(), (
        "<html><body><p>Compensation Discussion and Analysis</p>"
        + body
        + "<p>Summary Compensation Table</p></body></html>"
    )


def _go(doc: tuple[DocRef, str], **kw: Any) -> Any:
    base: dict[str, Any] = dict(
        sbc_ttm=None, revenue_ttm=None, fcf_ttm=None, goodwill_assets=None,
        goodwill_assets_prior=None, acquisition_guard=False,
    )  # fmt: skip
    base.update(kw)
    return run_proxy(doc, None, **base)


def test_core_eps_counts_as_nongaap_eps() -> None:
    res = _go(
        _doc(
            "The annual bonus payout is tied to core earnings per share against the target.",
            "Revenue growth also sets the bonus payout target for the year.",
        )
    )
    assert {m["metric"] for m in res.extra["metrics"]} == {"NONGAAP_EPS", "REVENUE"}
    fired = _go(
        _doc(
            "The annual bonus payout is tied to core EPS against the target.",
            "Revenue growth also sets the bonus payout target for the year.",
        ),
        sbc_ttm=20.0,
        revenue_ttm=100.0,
    )
    assert [f.id for f in fired.findings] == ["C1"]


def test_negated_metric_not_counted() -> None:
    res = _go(
        _doc(
            "The committee does not believe that relative total shareholder return should set the bonus payout target.",  # noqa: E501
            "The annual bonus payout is tied to revenue growth against the target.",
            "Adjusted EBITDA sets the annual bonus payout against the target.",
        )
    )
    assert "TSR" not in {m["metric"] for m in res.extra["metrics"]}


def test_peer_group_selection_not_a_metric() -> None:
    res = _go(
        _doc(
            "We selected peer companies for the target pay comparison due to their revenue and market capitalization.",  # noqa: E501
            "The annual bonus payout is tied to revenue growth against the target.",
            "Adjusted EBITDA sets the annual bonus payout against the target.",
        )
    )
    sentences = [m["sentence"] for m in res.extra["metrics"]]
    assert not any("peer companies" in s for s in sentences)
    assert len(sentences) == 2


def test_metrics_deduplicated_per_metric_and_context() -> None:
    res = _go(
        _doc(
            "Revenue growth sets the bonus payout.",
            "Revenue growth also affects the bonus payout.",
            "Adjusted EBITDA sets the bonus payout.",
        )
    )
    keys = [(m["metric"], m["context"].lower()) for m in res.extra["metrics"]]
    assert len(keys) == len(set(keys)) == 2
    weighted = _run()
    wk = [(m["metric"], m["context"].lower()) for m in weighted.extra["metrics"]]
    assert len(wk) == len(set(wk))
    assert {m["metric"] for m in weighted.extra["metrics"] if m["weight"] is not None} == {
        "ADJ_EBITDA",
        "REVENUE",
        "TSR",
    }


def test_c1_not_applicable_wording() -> None:
    res = _go(
        _doc(
            "The annual bonus payout is tied to revenue growth against the target.",
            "Relative total shareholder return sets the PSU payout.",
        ),
        sbc_ttm=50.0,
        revenue_ttm=100.0,
    )
    assert res.extra["checks"]["C1"]["reason"] == "Adjusted EBITDA / non-GAAP EPS not detected"
