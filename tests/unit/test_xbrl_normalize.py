"""§7.0 XBRL normalization rules (fixtures documented in tests/fixtures/xbrl/README.md)."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.xbrl.factory import build, calendar, const, fact
from veritas.engine.xbrl_normalize import days, duration_class, growth, normalize

XBRL = Path(__file__).parents[1] / "fixtures" / "xbrl"
REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((XBRL / f"{name}.json").read_text())
    return data


# ---------------------------------------------------------------- global rules
def test_days_is_inclusive() -> None:
    assert days(date(2024, 1, 1), date(2024, 3, 31)) == 91
    assert days(date(2023, 10, 1), date(2023, 12, 31)) == 92


@pytest.mark.parametrize(
    ("n_days", "expected"),
    [(79, None), (80, "3M"), (100, "3M"), (101, None), (170, "6M"), (190, "6M"), (191, None)]
    + [(260, "9M"), (285, "9M"), (349, None), (350, "FY"), (364, "FY"), (371, "FY")]
    + [(380, "FY"), (381, None)],
)
def test_duration_classes(n_days: int, expected: str | None) -> None:
    start = date(2023, 1, 1)
    end = date.fromordinal(start.toordinal() + n_days - 1)
    assert duration_class(start, end) == expected


def test_growth_zero_and_negative_base_is_undefined() -> None:
    assert growth(5.0, 0.0) is None
    assert growth(5.0, -2.0) is None
    assert growth(None, 1.0) is None
    assert growth(15.0, 10.0) == pytest.approx(0.5)


# ------------------------------------------------------------------ Q4 / YTD
def test_q4_equals_fy_minus_9m() -> None:
    nf = normalize(load("q4_derivation_ytd"))
    q4 = nf.quarter("Revenue", date(2023, 12, 31))
    assert q4 is not None
    assert q4.value == 130.0  # 460 − 330
    assert q4.derived is True
    assert q4.start == date(2023, 10, 1)
    assert q4.form == "10-K" and q4.accession == "0009999999-24-000010"
    nine, fy = nf.components(q4)
    assert (nine.start, nine.end, nine.value) == (date(2023, 1, 1), date(2023, 9, 30), 330.0)
    assert fy.value == 460.0
    assert q4.value + nine.value == fy.value
    q1 = nf.quarter("Revenue", date(2023, 3, 31))
    assert q1 is not None and q1.value == 100.0 and q1.derived is False


def test_ytd_only_cash_flow_differencing() -> None:
    nf = normalize(load("q4_derivation_ytd"))
    got = {p.end: (p.value, p.derived) for p in nf.series("OCF", "quarter").points}
    assert got == {
        date(2023, 3, 31): (50.0, False),
        date(2023, 6, 30): (30.0, True),
        date(2023, 9, 30): (70.0, True),
        date(2023, 12, 31): (110.0, True),
    }
    ttm = nf.ttm("OCF", date(2023, 12, 31))
    assert ttm is not None and ttm.value == 260.0


def test_missing_component_gives_none() -> None:
    data = load("q4_derivation_ytd")
    rows = data["facts"]["us-gaap"][REV]["units"]["USD"]
    data["facts"]["us-gaap"][REV]["units"]["USD"] = [
        r for r in rows if not (r.get("start") == "2023-01-01" and r["end"] == "2023-09-30")
    ]
    nf = normalize(data)
    assert nf.quarter("Revenue", date(2023, 12, 31)) is None  # no 9M → no Q4
    assert nf.ttm("Revenue", date(2023, 12, 31)) is None
    assert nf.quarter("Revenue", date(2023, 9, 30)) is not None  # 3M still reported


def test_ttm_requires_all_four_quarters() -> None:
    nf = normalize(load("q4_derivation_ytd"))
    assert nf.ttm("Revenue", date(2023, 12, 31)) is not None
    assert nf.ttm("Revenue", date(2023, 9, 30)) is None  # only 3 quarters before it


def test_non_accepted_forms_and_other_units_ignored() -> None:
    nf = normalize(load("q4_derivation_ytd"))
    q1 = nf.quarter("Revenue", date(2023, 3, 31))
    assert q1 is not None and q1.value == 100.0  # not the 8-K 999, not the EUR 95
    assert q1.unit == "USD"


def test_fy_fp_frame_never_used() -> None:
    """Shuffling fy/fp/frame must not change any output."""
    data = load("q4_derivation_ytd")
    scrambled = copy.deepcopy(data)
    for tag in scrambled["facts"]["us-gaap"].values():
        for rows in tag["units"].values():
            for r in rows:
                r["fy"], r["fp"], r["frame"] = 1900, "FY", "CY1900"
    a, b = normalize(data).metrics(), normalize(scrambled).metrics()
    assert {k: v.to_dict() for k, v in a.items()} == {k: v.to_dict() for k, v in b.items()}


# ---------------------------------------------------------------- dedup
def test_dedup_keeps_latest_filed_and_flags_restatement() -> None:
    nf = normalize(load("dedup_restatement"))
    ar = nf.instant("AR", date(2023, 12, 31), tol_days=0)
    assert ar is not None
    assert ar.value == 1030.0
    assert ar.accession == "0009999999-25-000010"
    assert ar.filed == date(2025, 2, 20)
    assert ar.restated is True
    ar22 = nf.instant("AR", date(2022, 12, 31), tol_days=0)
    assert ar22 is not None and ar22.value == 905.0 and ar22.restated is False
    q1 = nf.quarter("Revenue", date(2023, 3, 31))
    assert q1 is not None
    assert q1.value == 120.0 and q1.restated is True and q1.form == "10-Q"  # S-1 777 ignored
    restated = {(m, p.end) for m, p in nf.restated_points()}
    assert restated == {("AR", date(2023, 12, 31)), ("Revenue", date(2023, 3, 31))}


# ---------------------------------------------------------------- calendars
def test_53_week_year_duration_class_and_yoy_match() -> None:
    nf = normalize(load("fy_52_53_week"))
    assert nf.fiscal_years() == [
        (date(2023, 1, 30), date(2024, 2, 4)),  # 371 days (53 weeks)
        (date(2022, 1, 31), date(2023, 1, 29)),  # 364 days
    ]
    q4 = nf.quarter("Revenue", date(2024, 2, 4))
    assert q4 is not None and q4.value == 1700.0 and q4.derived
    assert q4.start is not None and days(q4.start, q4.end) == 98
    assert nf.prior_year_quarter_end(date(2024, 2, 4)) == date(2023, 1, 29)
    prior = nf.yoy_quarter("Revenue", date(2024, 2, 4))
    assert prior is not None and prior.value == 950.0
    ar_prior = nf.yoy_instant("AR", date(2024, 2, 4))
    assert ar_prior is not None and ar_prior.value == 500.0
    ttm = nf.ttm("Revenue", date(2024, 2, 4))
    assert ttm is not None and ttm.value == 5400.0


def test_fiscal_year_ending_january() -> None:
    nf = normalize(load("fy_january"))
    assert nf.fiscal_years() == [
        (date(2023, 2, 1), date(2024, 1, 31)),
        (date(2022, 2, 1), date(2023, 1, 31)),
    ]
    q4 = nf.quarter("Revenue", date(2024, 1, 31))
    assert q4 is not None and q4.value == 175.0 and q4.start == date(2023, 11, 1)
    assert nf.quarter_ends() == [
        date(2024, 1, 31),
        date(2023, 10, 31),
        date(2023, 7, 31),
        date(2023, 4, 30),
    ]
    fy = nf.fy("Revenue", date(2023, 1, 31))
    assert fy is not None and fy.value == 600.0


def test_yoy_never_sequential() -> None:
    qs = calendar(2023, 2)
    nf = normalize(build(qs, flows={REV: [10.0, 20, 30, 40, 11, 21, 31, 41]}))
    prior = nf.yoy_quarter("Revenue", date(2024, 9, 30))
    assert prior is not None and prior.end == date(2023, 9, 30) and prior.value == 30.0


def test_instant_tolerance() -> None:
    data = build(
        calendar(2024, 1),
        extra={"Assets": [fact(None, date(2024, 12, 28), 5.0, "10-K")]},
    )
    nf = normalize(data)
    assert nf.instant("Assets", date(2024, 12, 31)) is not None
    assert nf.instant("Assets", date(2024, 12, 31), tol_days=0) is None
    assert nf.instant("Assets", date(2025, 1, 15)) is None  # 18 days away


# ---------------------------------------------------------------- tag fallback
def test_tag_switch_mid_series() -> None:
    qs = calendar(2022, 3)
    old = [100.0] * 8 + [None] * 4
    new = [None] * 8 + [100.0] * 4
    nf = normalize(build(qs, flows={"SalesRevenueNet": old, REV: new}))
    series = nf.series("Revenue", "quarter")
    assert len(series.points) == 12
    assert series.tag_changed is True
    assert series.points[0].xbrl_tag == "SalesRevenueNet"
    assert series.points[-1].xbrl_tag == REV
    ttm = nf.ttm("Revenue", date(2024, 3, 31))  # mixes 3 old-tag + 1 new-tag quarters
    assert ttm is not None and ttm.value == 400.0


def test_first_tag_wins_per_period() -> None:
    qs = calendar(2024, 1)
    nf = normalize(build(qs, flows={"Revenues": const(7.0, 4), REV: const(5.0, 4)}))
    q = nf.quarter("Revenue", date(2024, 3, 31))
    assert q is not None and q.xbrl_tag == REV and q.value == 5.0
    assert nf.series("Revenue", "quarter").tag_changed is False


def test_metrics_keys() -> None:
    qs = calendar(2024, 1)
    data = build(
        qs,
        flows={REV: const(100.0, 4)},
        ytd={"NetCashProvidedByUsedInOperatingActivities": const(10.0, 4)},
        instants={"AccountsReceivableNetCurrent": const(50.0, 4)},
    )
    keys = set(normalize(data).metrics())
    assert {"Revenue.quarter", "Revenue.fy", "Revenue.ttm", "AR.instant", "OCF.ttm"} <= keys
    assert "AR.quarter" not in keys


def test_empty_payload() -> None:
    nf = normalize({})
    assert nf.quarter_ends() == []
    assert nf.metrics() == {}
    assert nf.fiscal_years() == []
