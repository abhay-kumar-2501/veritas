"""Performance benchmark for PRD §12 (seed / warm-cache paths; no network).

Usage: .venv/bin/python scripts/bench.py [--tickers NVDA WMT TSLA BA] [--repeat 3]
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable
from typing import Any

from veritas.config import SEED_TICKERS
from veritas.edgar.filings import select_filings
from veritas.edgar.source import SeedSource
from veritas.engine.footnote_diff import run_footnotes
from veritas.engine.html_text import html_to_text
from veritas.export.pdf_tearsheet import build_pdf
from veritas.pipeline import analyze


def _time(fn: Callable[[], Any], repeat: int) -> float:
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=list(SEED_TICKERS))
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    ds = SeedSource()
    rows: list[tuple[str, str, float, float]] = []
    for ticker in args.tickers:
        analyze(ticker, offline=True)  # warm imports and caches
        rows.append(
            (
                ticker,
                "analyze (seed)",
                _time(lambda t=ticker: analyze(t, offline=True), args.repeat),
                2.0,
            )
        )
        company_cik = next(e["cik"] for e in ds.entries if e["ticker"] == ticker)
        sel = select_filings(ds.submissions(company_cik), company_cik, ticker, ds)
        assert sel.latest_10k is not None and sel.prior_10k is not None
        cur = ds.document(sel.latest_10k.url) or ""
        prior = ds.document(sel.prior_10k.url) or ""
        rows.append(
            (
                ticker,
                "parse + preprocess one filing",
                _time(lambda h=cur: html_to_text(h), args.repeat),
                5.0,
            )
        )
        pair = (sel.prior_10k, prior, sel.latest_10k, cur)
        rows.append(
            (
                ticker,
                "diff one filing pair",
                _time(lambda p=pair: run_footnotes(p, None), args.repeat),
                5.0,
            )
        )
        result = analyze(ticker, offline=True)
        rows.append((ticker, "PDF build", _time(lambda r=result: build_pdf(r), args.repeat), 5.0))

    failed = 0
    print(f"{'ticker':<6} {'scenario':<32} {'median s':>9} {'target':>7}  ok")
    for ticker, name, secs, target in rows:
        ok = secs <= target
        failed += 0 if ok else 1
        print(f"{ticker:<6} {name:<32} {secs:>9.3f} {target:>7.1f}  {'✓' if ok else '✗'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
