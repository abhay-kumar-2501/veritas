"""Resolution, filing selection and the seed cache (PRD §6.3, §6.4, §10.1)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import pytest

from veritas.config import SEED_DIR, SEED_TICKERS, ti
from veritas.edgar.filings import (
    find_earnings_exhibit,
    find_ex13,
    find_exhibit,
    select_filings,
)
from veritas.edgar.resolve import TickerNotFound, normalize_ticker, resolve
from veritas.edgar.source import SeedSource, parse_index_headers

FX = Path(__file__).parents[1] / "fixtures" / "edgar"


class FakeDS:
    source: Literal["live", "cache", "seed"] = "cache"
    fetched_at: datetime | None = None

    def __init__(self) -> None:
        self.banners: list[str] = []
        self.indexes = json.loads((FX / "synthetic_indexes.json").read_text())
        self.tickers = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
            "2": {"cik_str": 1067983, "ticker": "BRK-A", "title": "BERKSHIRE HATHAWAY INC"},
            "3": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
            "4": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
            "5": {"cik_str": 999999, "ticker": "SYN", "title": "SYNTH CORP"},
        }
        self.asked: list[str] = []

    def company_tickers(self) -> dict[str, Any]:
        return self.tickers

    def submissions(self, cik: int) -> dict[str, Any]:
        from veritas.edgar.source import DataUnavailable

        if cik != 999999:
            raise DataUnavailable("no")
        return json.loads((FX / "synthetic_submissions.json").read_text())

    def companyfacts(self, cik: int) -> dict[str, Any] | None:
        return None

    def document(self, url: str) -> str | None:
        return None

    def filing_index(self, cik: int, accession: str) -> dict[str, Any] | None:
        self.asked.append(accession)
        return self.indexes.get(accession)


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "norm"),
    [("BRK.B", "BRK-B"), (" brk/b ", "BRK-B"), ("brk-b", "BRK-B"), ("aapl", "AAPL")],
)
def test_normalize_ticker(raw: str, norm: str) -> None:
    assert normalize_ticker(raw) == norm


def test_resolve_ticker_variants() -> None:
    ds = FakeDS()
    for raw in ("BRK.B", "brk-b", "BRK/B"):
        ref = resolve(raw, ds)
        assert (ref.cik, ref.ticker) == (1067983, "BRK-B")
    assert resolve("goog", ds).ticker == "GOOG"  # many tickers -> one CIK, exact match wins
    assert resolve("GOOGL", ds).cik == resolve("GOOG", ds).cik


def test_resolve_cik_input() -> None:
    ds = FakeDS()
    assert resolve("CIK 320193", ds).ticker == "AAPL"
    assert resolve("cik320193", ds).cik == 320193
    assert resolve("CIK 0000320193", ds).title == "Apple Inc."
    # CIK absent from ticker file: falls back to submissions' ticker list
    assert resolve("CIK 999999", ds).ticker == "SYN"


def test_resolve_unknown_cik_raises() -> None:
    with pytest.raises(TickerNotFound):
        resolve("CIK 1", FakeDS())


def test_resolve_miss_gives_prefix_suggestions() -> None:
    ds = FakeDS()
    ds.tickers.update(
        {
            str(10 + i): {"cik_str": 100 + i, "ticker": f"AAP{c}", "title": "x"}
            for i, c in enumerate("BCDEFGH")
        }
    )
    with pytest.raises(TickerNotFound) as ei:
        resolve("AAPZ", ds)
    s = ei.value.suggestions
    assert 1 <= len(s) <= 5
    assert all(x.startswith("AAP") or x.startswith("AA") for x in s)
    assert "delisted/renamed" in str(ei.value)
    with pytest.raises(TickerNotFound) as ei2:
        resolve("ZZZZ", ds)
    assert ei2.value.suggestions == []


# ---------------------------------------------------------------------------
# filing selection (synthetic fixture)
# ---------------------------------------------------------------------------
@pytest.fixture
def sel() -> Any:
    ds = FakeDS()
    subs = ds.submissions(999999)
    return select_filings(subs, 999999, "SYN", ds, as_of=date(2025, 12, 1)), ds


def test_latest_prior_10k_and_amendment_notice(sel: Any) -> None:
    s, _ = sel
    assert s.latest_10k.accession == "0000999999-25-000010"
    assert s.latest_10k.url == (
        "https://www.sec.gov/Archives/edgar/data/999999/000099999925000010/x10k-24.htm"
    )
    assert s.latest_10k.report_date == date(2024, 12, 31)
    assert s.amendment_notice is not None and "0000999999-25-000030" in s.amendment_notice
    # prior 10-K is a .txt primary doc: document unavailable, with an explicit reason
    assert s.prior_10k is None
    assert any("x10k-23.txt" in n and "document unavailable" in n for n in s.notes)
    assert s.unsupported_reason is None
    assert (s.sic, s.fiscal_year_end) == ("3571", "1231")


def test_ten_qs_and_pair(sel: Any) -> None:
    s, _ = sel
    assert [q.accession for q in s.ten_qs] == [
        "0000999999-25-000080",
        "0000999999-25-000070",
        "0000999999-25-000050",
        "0000999999-24-000090",
    ]
    latest, prior = s.q_pair
    assert latest.accession == "0000999999-25-000080"
    assert prior.accession == "0000999999-24-000090"  # reportDate 2024-09-30, 365 d earlier


def test_proxy_ignores_defa14a_and_friends(sel: Any) -> None:
    s, _ = sel
    assert s.proxy is not None and s.proxy.form == "DEF 14A"
    assert s.proxy.accession == "0000999999-25-000035"


def test_proxy_older_than_18_months_is_none() -> None:
    ds = FakeDS()
    s = select_filings(ds.submissions(999999), 999999, "SYN", ds, as_of=date(2026, 12, 1))
    assert s.proxy is None
    assert any("older than 18 months" in n for n in s.notes)


def test_earnings_8k_window_and_exhibit_fallbacks(sel: Any) -> None:
    s, ds = sel
    q = s.earnings_8k["0000999999-25-000080"]  # 10-Q: 8-K item 2.02 on 2025-11-04
    assert q.doc_name == "ex991-q3.htm" and q.form == "8-K"
    assert q.accession == "0000999999-25-000079"
    k = s.earnings_8k["0000999999-25-000010"]  # no EX-99.1 type: first EX-99* by sequence
    assert k.doc_name == "ex993.htm" and k.accession == "0000999999-25-000009"
    # the 5.02-only 8-K and the 2025-12-02 8-K (outside the window) are never opened
    assert "0000999999-25-000078" not in ds.asked and "0000999999-25-000090" not in ds.asked


def test_earnings_8k_missing_is_skipped_silently_with_note() -> None:
    ds = FakeDS()
    ds.indexes = {}
    s = select_filings(ds.submissions(999999), 999999, "SYN", ds, as_of=date(2025, 12, 1))
    assert s.earnings_8k == {}
    assert any("no earnings press release" in n for n in s.notes)


def test_unsupported_filer_when_no_recent_10k() -> None:
    ds = FakeDS()
    s = select_filings(ds.submissions(999999), 999999, "SYN", ds, as_of=date(2029, 1, 1))
    assert s.unsupported_reason is not None and "Unsupported filer type" in s.unsupported_reason


def test_no_10k_at_all() -> None:
    ds = FakeDS()
    subs = ds.submissions(999999)
    keep = [i for i, f in enumerate(subs["filings"]["recent"]["form"]) if not f.startswith("10-K")]
    subs["filings"]["recent"] = {
        k: [v[i] for i in keep] for k, v in subs["filings"]["recent"].items()
    }
    s = select_filings(subs, 999999, "SYN", ds, as_of=date(2025, 12, 1))
    assert s.latest_10k is None and s.unsupported_reason is not None


def test_missing_columns_raise() -> None:
    ds = FakeDS()
    with pytest.raises(KeyError):
        select_filings({"filings": {"recent": {"form": []}}}, 1, "X", ds, as_of=date(2025, 1, 1))


# ---------------------------------------------------------------------------
# exhibits
# ---------------------------------------------------------------------------
def test_find_exhibit_by_type_and_name_fallback() -> None:
    idx = json.loads((FX / "synthetic_indexes.json").read_text())["0000999999-25-000079"]
    assert find_exhibit(idx, "EX-99") == "ex992.htm"  # first by sequence
    assert find_earnings_exhibit(idx) == "ex991-q3.htm"
    names_only = json.loads((FX / "index_names_only.json").read_text())
    assert find_earnings_exhibit(names_only) == "ex-99_1.htm"  # filename regex fallback
    assert find_ex13(names_only) is None
    assert find_ex13(None) is None


def test_find_ex13_by_type_and_html_only() -> None:
    idx = {
        "directory": {"item": []},
        "_veritas_documents": [
            {"seq": 1, "type": "10-K", "name": "k.htm"},
            {"seq": 2, "type": "EX-13", "name": "ar.pdf"},
            {"seq": 3, "type": "EX-13.1", "name": "ar.htm"},
        ],
    }
    assert find_ex13(idx) == "ar.htm"


def test_parse_index_headers() -> None:
    raw = (
        "&lt;DOCUMENT&gt;\n&lt;TYPE&gt;8-K\n&lt;SEQUENCE&gt;1\n&lt;FILENAME&gt;a.htm\n"
        "&lt;DESCRIPTION&gt;8-K\n&lt;TEXT&gt;\n&lt;/DOCUMENT&gt;\n"
        "&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;SEQUENCE&gt;2\n&lt;FILENAME&gt;pr.htm\n"
        "&lt;DESCRIPTION&gt;PR &amp; more\n&lt;TEXT&gt;\n"
    )
    docs = parse_index_headers(raw)
    assert [(d["type"], d["name"], d["seq"]) for d in docs] == [
        ("8-K", "a.htm", 1),
        ("EX-99.1", "pr.htm", 2),
    ]
    assert docs[1]["description"] == "PR & more"


# ---------------------------------------------------------------------------
# seed cache
# ---------------------------------------------------------------------------
def _seed_dir_bytes() -> int:
    return sum(p.stat().st_size for p in SEED_DIR.rglob("*") if p.is_file())


def test_seed_size_budget() -> None:
    assert _seed_dir_bytes() <= ti("SEED_MAX_BYTES")


def test_seed_manifest_hashes_match() -> None:
    ds = SeedSource()
    assert sorted(ds.tickers) == sorted(SEED_TICKERS)
    for e in ds.entries:
        assert e["accessions"] and e["fetched_at"] and e["cik"]
        for f in e["files"]:
            data = (SEED_DIR / f["path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == f["sha256"], f["path"]


@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_seed_source_loads_offline_end_to_end(ticker: str) -> None:
    ds = SeedSource()
    assert ds.source == "seed" and ds.fetched_at is not None
    ref = resolve(ticker, ds)
    assert ref.ticker == ticker
    subs = ds.submissions(ref.cik)
    facts = ds.companyfacts(ref.cik)
    assert facts is not None and "us-gaap" in facts["facts"]
    s = select_filings(subs, ref.cik, ref.ticker, ds)
    assert s.unsupported_reason is None
    assert s.latest_10k and s.prior_10k and s.q_pair and s.proxy
    needed = [s.latest_10k, s.prior_10k, *s.q_pair, s.proxy, *s.earnings_8k.values()]
    assert len(s.earnings_8k) == 2
    for d in needed:
        text = ds.document(d.url)
        assert text and len(text) > 5000, d.url
    assert (
        ds.document("https://www.sec.gov/Archives/edgar/data/1/000000000100000001/none.htm") is None
    )
    assert s.sic and s.fiscal_year_end


def test_seed_source_unknown_cik_raises() -> None:
    from veritas.edgar.source import DataUnavailable

    with pytest.raises(DataUnavailable):
        SeedSource().submissions(320193)
    with pytest.raises(TickerNotFound):
        resolve("AAPL", SeedSource())
