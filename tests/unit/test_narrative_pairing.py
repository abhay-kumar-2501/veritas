"""Tests for claim/check pairing (PRD §7.2.5)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from veritas.engine.narrative import pair_findings
from veritas.models import CheckResult, Claim, ClaimCluster, DocRef, Severity

P = date(2025, 10, 26)
P_PRIOR = date(2025, 1, 26)


def _check(
    check_id: str = "A1_DSO",
    severity: Severity | None = "WARNING",
    period: date = P,
    annotations: tuple[str, ...] = (),
    scored: bool = True,
    state: str = "COMPUTED",
    inputs: dict[str, float | None] | None = None,
    reason: str | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=f"{check_id} title",
        state=state,  # type: ignore[arg-type]
        severity=severity if state == "COMPUTED" else None,
        period_end=period,
        inputs={"x": 1.0} if inputs is None else inputs,
        outputs={},
        formula="f",
        reason=reason,
        annotations=annotations,
        citations=(),
        scored=scored,
    )


def _claim(
    cluster: ClaimCluster = "DEMAND",
    period: date = P,
    form: str = "10-Q",
    start: int = 0,
    accession: str = "0000000001-25-000010",
) -> Claim:
    doc = DocRef(
        cik=1,
        ticker="ACME",
        form=form,
        accession=accession,
        filing_date=date(2025, 11, 20),
        report_date=period,
        doc_name="d.htm",
        url="https://www.sec.gov/Archives/edgar/data/1/x/d.htm",
    )
    quote = f"quote at {start} " * 3
    cit = doc.cite(
        section="10-Q Item 2" if form != "8-K" else "8-K EX-99.1",
        parser_version="1.0.0",
        quote_raw=quote,
        char_start=start,
        char_end=start + len(quote),
    )
    return Claim(cluster=cluster, citation=cit, period_end=period)


def _lookup(*checks: CheckResult) -> Callable[[str, date], CheckResult | None]:
    table = {(c.check_id, c.period_end): c for c in checks}

    def get(check_id: str, period: date) -> CheckResult | None:
        return table.get((check_id, period))

    return get


def test_escalation_by_paired_claim() -> None:
    chk = _check()
    (f,) = pair_findings(_lookup(chk), [_claim()], [chk])
    assert f.severity == "CRITICAL"
    assert f.contradiction is True
    assert f.module == "A"
    assert f.category == "accounting"
    assert f.check is chk
    assert len(f.claims) == 1
    assert f.citations[0] is f.claims[0].citation


def test_no_escalation_for_acquisition_distorted() -> None:
    chk = _check(annotations=("acquisition-distorted",))
    (f,) = pair_findings(_lookup(chk), [_claim()], [chk])
    assert f.severity == "WARNING"
    assert f.contradiction is True
    assert "acquisition-distorted" in f.explanation


def test_unaddressed_divergence_without_claim() -> None:
    chk = _check(severity="CRITICAL")
    (f,) = pair_findings(_lookup(chk), [], [chk])
    assert f.severity == "CRITICAL"
    assert f.contradiction is False
    assert f.claims == ()
    assert f.title.startswith("Unaddressed divergence")
    assert f.category == "accounting"


def test_unpaired_cluster_does_not_escalate() -> None:
    chk = _check("A1_DSO")
    (f,) = pair_findings(_lookup(chk), [_claim("MARGIN"), _claim("ONETIME")], [chk])
    assert f.title.startswith("Unaddressed divergence")
    assert f.severity == "WARNING"


def test_a3_never_pairs() -> None:
    chk = _check("A3_ACCRUALS")
    claims = [_claim(c) for c in ("DEMAND", "MARGIN", "ONETIME")]
    (f,) = pair_findings(_lookup(chk), claims, [chk])
    assert f.claims == ()
    assert f.severity == "WARNING"


def test_a2_pairs_demand_and_margin_and_a5_onetime() -> None:
    a2 = _check("A2_DIO")
    a5 = _check("A5_RESTRUCT")
    findings = pair_findings(
        _lookup(a2, a5), [_claim("MARGIN", start=5), _claim("ONETIME", start=9)], [a2, a5]
    )
    by_id = {f.check.check_id: f for f in findings if f.check is not None}
    assert by_id["A2_DIO"].severity == "CRITICAL"
    assert by_id["A2_DIO"].claims[0].cluster == "MARGIN"
    assert by_id["A5_RESTRUCT"].severity == "CRITICAL"


def test_period_mismatch_no_pairing() -> None:
    latest = _check(period=P)
    # Claim from a filing with a different period; no check evaluated at that period.
    findings = pair_findings(_lookup(latest), [_claim(period=P_PRIOR)], [latest])
    assert len(findings) == 1
    assert findings[0].title.startswith("Unaddressed divergence")
    assert findings[0].severity == "WARNING"


def test_callback_returning_other_period_is_ignored() -> None:
    latest = _check(period=P)

    def wrong(check_id: str, period: date) -> CheckResult | None:
        return latest  # evaluated at P, not at the claim's period

    findings = pair_findings(wrong, [_claim(period=P_PRIOR)], [latest])
    assert [f.id.rsplit(":", 1)[1] for f in findings] == ["unaddressed"]


def test_prior_period_flag_is_unscored_contradiction() -> None:
    latest = _check(severity="NO_FLAG", period=P)
    prior = _check(severity="WARNING", period=P_PRIOR)
    findings = pair_findings(_lookup(latest, prior), [_claim(period=P_PRIOR)], [latest])
    (f,) = findings
    assert f.category == "accounting_prior_period"
    assert f.contradiction is True
    assert f.severity == "CRITICAL"
    assert f.check is prior


def test_no_flag_check_gives_consistent_finding() -> None:
    chk = _check(severity="NO_FLAG")
    (f,) = pair_findings(_lookup(chk), [_claim()], [chk])
    assert f.severity == "NO_FLAG"
    assert f.category == "claims_consistent"
    assert f.contradiction is False
    assert len(f.claims) == 1


def test_non_computed_or_unscored_checks_produce_nothing() -> None:
    na = _check(state="NOT_APPLICABLE")
    unscored = _check("A2_DIO", scored=False)
    assert pair_findings(_lookup(na, unscored), [_claim()], [na, unscored]) == ()


def test_max_two_quotes_prefer_mda_then_earliest() -> None:
    chk = _check()
    claims = [
        _claim(form="8-K", start=1, accession="0000000001-25-000009"),
        _claim(start=500),
        _claim(start=40),
        _claim(start=900),
    ]
    (f,) = pair_findings(_lookup(chk), claims, [chk])
    assert len(f.claims) == 2
    assert [c.citation.char_start for c in f.claims] == [40, 500]
    assert all(c.citation.form == "10-Q" for c in f.claims)
    # 8-K quotes are used when MD&A has fewer than two.
    (g,) = pair_findings(_lookup(chk), [claims[0], claims[1]], [chk])
    assert [c.citation.form for c in g.claims] == ["10-Q", "8-K"]


def test_duplicate_sentence_counted_once() -> None:
    chk = _check("A2_DIO")
    claims = [_claim("DEMAND", start=10), _claim("MARGIN", start=10), _claim("MARGIN", start=70)]
    (f,) = pair_findings(_lookup(chk), claims, [chk])
    assert [c.citation.char_start for c in f.claims] == [10, 70]


def test_determinism_and_order_independence() -> None:
    a1 = _check("A1_DSO")
    a3 = _check("A3_ACCRUALS", severity="CRITICAL")
    a5 = _check("A5_RESTRUCT", severity="NO_FLAG")
    claims = [_claim("DEMAND", start=3), _claim("ONETIME", start=8), _claim("DEMAND", start=1)]
    lookup = _lookup(a1, a3, a5)
    one = pair_findings(lookup, claims, [a1, a3, a5])
    two = pair_findings(lookup, list(reversed(claims)), [a5, a3, a1])
    assert [f.to_json() for f in one] == [f.to_json() for f in two]
    assert [f.id for f in one] == [
        "A:A1_DSO:2025-10-26:contradiction",
        "A:A3_ACCRUALS:2025-10-26:unaddressed",
        "A:A5_RESTRUCT:2025-10-26:consistent",
    ]
    assert len({f.id for f in one}) == len(one)


def test_no_data_no_flag_check_gives_no_consistent_finding() -> None:
    marker = _check(
        "A5_RESTRUCT",
        severity="NO_FLAG",
        inputs={},
        reason="no restructuring tags reported",
        annotations=("no restructuring tags reported",),
    )
    claims = [_claim("ONETIME", start=1), _claim("ONETIME", start=50)]
    assert pair_findings(_lookup(marker), claims, [marker]) == ()
    all_none = _check("A1_DSO", severity="NO_FLAG", inputs={"AR_end": None, "Revenue_q": None})
    assert pair_findings(_lookup(all_none), [_claim("DEMAND")], [all_none]) == ()
    a5_no_restruct = CheckResult(
        **{
            **_check("A5_RESTRUCT", severity="NO_FLAG").__dict__,
            "inputs": {"Restructuring_q": None, "Revenue_q": 9.0},
            "outputs": {"quarters_with_data_last8": 0.0},
        }
    )
    assert pair_findings(_lookup(a5_no_restruct), [_claim("ONETIME")], [a5_no_restruct]) == ()
    with_data = CheckResult(
        **{
            **a5_no_restruct.__dict__,
            "inputs": {"Restructuring_q": 5.0, "Revenue_q": 9.0},
            "outputs": {"quarters_with_data_last8": 4.0},
        }
    )
    (f,) = pair_findings(_lookup(with_data), [_claim("ONETIME")], [with_data])
    assert f.category == "claims_consistent"


def test_exhibit_form_counts_as_8k_for_quote_preference() -> None:
    chk = _check()
    ex = _claim(form="EX-99.1", start=1, accession="0000000001-25-000009")
    (f,) = pair_findings(_lookup(chk), [ex, _claim(start=300)], [chk])
    assert [c.citation.form for c in f.claims] == ["10-Q", "EX-99.1"]
