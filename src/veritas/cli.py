"""Command line entry point: `python -m veritas analyze <TICKER> [options]`."""

from __future__ import annotations

import argparse
import logging
import sys

from veritas.config import APP_VERSION, get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veritas",
        description="Narrative-vs-Numbers screening over public SEC EDGAR filings.",
    )
    parser.add_argument("--version", action="version", version=f"veritas {APP_VERSION}")
    sub = parser.add_subparsers(dest="command")
    analyze = sub.add_parser("analyze", help="Analyze one ticker (or 'CIK <digits>')")
    analyze.add_argument("target", nargs="+", help="Ticker, or CIK followed by digits")
    analyze.add_argument("--offline", action="store_true", help="No network: seed + cache only")
    analyze.add_argument("--json", action="store_true", help="Print AnalysisResult JSON")
    analyze.add_argument("--pdf", metavar="OUT.pdf", help="Write the 2-page tear-sheet")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    if args.command != "analyze":
        build_parser().print_help()
        return 0

    from veritas.edgar.client import EdgarError
    from veritas.edgar.source import DataUnavailable
    from veritas.pipeline import AnalysisError, TickerNotFound, analyze  # deferred: heavy

    target = " ".join(args.target)
    try:
        result = analyze(target, offline=args.offline or settings.offline)
    except TickerNotFound as exc:
        hint = f" Did you mean: {', '.join(exc.suggestions)}?" if exc.suggestions else ""
        print(f"Ticker not found (delisted/renamed tickers unsupported).{hint}", file=sys.stderr)
        return 2
    except AnalysisError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except DataUnavailable as exc:
        print(f"Data unavailable: {exc}", file=sys.stderr)
        return 2
    except EdgarError as exc:
        print(f"SEC EDGAR request failed: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(result.to_json())
    else:
        print(f"{result.company_name} ({result.ticker}) CIK {result.cik} — {result.score.label}")
        for key, mod in sorted(result.modules.items()):
            print(f"  Module {key}: {mod.status} — {len(mod.findings)} finding(s)")
        for banner in result.banners:
            print(f"  ! {banner}")
    if args.pdf:
        from veritas.export.pdf_tearsheet import build_pdf

        with open(args.pdf, "wb") as fh:
            fh.write(build_pdf(result))
        print(f"PDF written: {args.pdf}", file=sys.stderr)
    return 0
