#!/usr/bin/env python
"""Build seed_cache/ from LIVE SEC data (PRD §10.1). Needs network and a valid
VERITAS_SEC_USER_AGENT. Never hand-edit the output.

    .venv/bin/python scripts/build_seed_cache.py --tickers NVDA WMT TSLA BA

Layout per ticker T under seed_cache/T/:
    submissions.json.gz      merged filings.recent (older pages appended)
    companyfacts.json.gz     trimmed to engine.tags.all_tags() (+ dei)
    docs/<acc_nodash>/<name>.gz   raw document text for every document the pipeline uses
    index/<acc_nodash>.json.gz    index.json (+ document types from index-headers)
Top level: manifest.json, company_tickers.json (subset).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veritas.config import SEED_DIR, SEED_TICKERS, get_settings, ti  # noqa: E402
from veritas.edgar.cache import RuntimeCache  # noqa: E402
from veritas.edgar.client import SecClient  # noqa: E402
from veritas.edgar.filings import (  # noqa: E402
    FilingSelection,
    exhibit_docref,
    find_ex13,
    select_filings,
)
from veritas.edgar.resolve import resolve  # noqa: E402
from veritas.edgar.source import LiveSource, acc_nodash, parse_archive_url  # noqa: E402
from veritas.engine.tags import all_tags  # noqa: E402
from veritas.models import DocRef  # noqa: E402

IX_HEADER_RE = None  # set when --strip-ix-header is used


def gz_bytes(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)


def trim_companyfacts(facts: dict[str, Any]) -> dict[str, Any]:
    keep = all_tags()
    out_facts: dict[str, Any] = {}
    for taxonomy, tags in facts.get("facts", {}).items():
        if taxonomy == "us-gaap":
            out_facts[taxonomy] = {k: v for k, v in tags.items() if k in keep}
        elif taxonomy == "dei":
            out_facts[taxonomy] = tags  # small; entity-level metadata
    return {"cik": facts.get("cik"), "entityName": facts.get("entityName"), "facts": out_facts}


def strip_ix_header(text: str) -> str:
    """Remove the hidden <ix:header>...</ix:header> block (XBRL contexts; not display text)."""
    import re

    return re.sub(r"<ix:header>.*?</ix:header>", "", text, flags=re.S | re.I)


def build_ticker(ticker: str, out: Path, ds: LiveSource, strip_header: bool) -> dict[str, Any]:
    ref = resolve(ticker, ds)
    sub = ds.submissions(ref.cik)
    fetched_at = datetime.now(UTC).replace(microsecond=0)
    sel: FilingSelection = select_filings(sub, ref.cik, ref.ticker, ds, as_of=fetched_at.date())
    tdir = out / ticker
    files: list[dict[str, Any]] = []
    accessions: dict[str, None] = {}

    def write(rel: str, data: bytes) -> None:
        p = tdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        files.append(
            {
                "path": f"{ticker}/{rel}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )

    write("submissions.json.gz", gz_bytes(json.dumps(sub, separators=(",", ":")).encode()))
    facts = ds.companyfacts(ref.cik)
    if facts is None:
        raise SystemExit(f"{ticker}: companyfacts 404; cannot seed")
    write(
        "companyfacts.json.gz",
        gz_bytes(json.dumps(trim_companyfacts(facts), separators=(",", ":")).encode()),
    )

    docs: dict[str, DocRef] = {}  # url -> ref, insertion-ordered
    roles: dict[str, str] = {}

    def want(role: str, d: DocRef | None) -> None:
        if d is not None:
            docs.setdefault(d.url, d)
            roles[role] = d.accession

    want("latest_10k", sel.latest_10k)
    want("prior_10k", sel.prior_10k)
    if sel.q_pair:
        want("latest_10q", sel.q_pair[0])
        want("prior_year_10q", sel.q_pair[1])
    want("proxy", sel.proxy)
    for acc, d in sel.earnings_8k.items():
        want(f"earnings_ex99_for_{acc}", d)

    index_accs: dict[str, None] = {}
    for d in [sel.latest_10k, sel.prior_10k, *(sel.q_pair or ())]:
        if d is not None:
            index_accs[d.accession] = None
    for acc in sel.earnings_8k.values():
        index_accs[acc.accession] = None
    for d in (sel.latest_10k, sel.prior_10k):  # EX-13 (annual report incorporated by reference)
        if d is None:
            continue
        idx = ds.filing_index(ref.cik, d.accession)
        name = find_ex13(idx)
        if name:
            want(f"ex13_{d.accession}", exhibit_docref(d, "EX-13", name))
    for acc in index_accs:
        idx = ds.filing_index(ref.cik, acc)
        if idx is not None:
            write(
                f"index/{acc_nodash(acc)}.json.gz",
                gz_bytes(json.dumps(idx, separators=(",", ":")).encode()),
            )

    for url, d in docs.items():
        text = ds.document(url)
        if text is None:
            raise SystemExit(f"{ticker}: document unavailable: {url}")
        if strip_header:
            text = strip_ix_header(text)
        parsed = parse_archive_url(url)
        assert parsed is not None
        _, accnd, name = parsed
        write(f"docs/{accnd}/{name}.gz", gz_bytes(text.encode("utf-8")))
        accessions[d.accession] = None

    return {
        "ticker": ticker,
        "cik": ref.cik,
        "title": ref.title,
        "fetched_at": fetched_at.isoformat(),
        "accessions": list(accessions),
        "roles": roles,
        "sic": sel.sic,
        "fiscal_year_end": sel.fiscal_year_end,
        "notes": list(sel.notes),
        "files": files,
        "bytes": sum(f["bytes"] for f in files),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=list(SEED_TICKERS))
    ap.add_argument("--out", type=Path, default=SEED_DIR)
    ap.add_argument(
        "--strip-ix-header",
        action="store_true",
        help="drop hidden <ix:header> blocks from stored documents (size lever; see data.md)",
    )
    args = ap.parse_args()

    ua = get_settings().user_agent
    if ua is None:
        print("VERITAS_SEC_USER_AGENT is unset or a placeholder; cannot fetch live data.")
        return 2

    tickers = [t.upper() for t in args.tickers]
    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    with tempfile.TemporaryDirectory() as tmp:  # fresh cache so nothing stale is seeded
        ds = LiveSource(SecClient(ua), RuntimeCache(Path(tmp) / "build.sqlite"))
        full_tickers = ds.company_tickers()
        entries: list[dict[str, Any]] = []
        subset: dict[str, Any] = {}
        for tk in tickers:
            print(f"[{tk}] building...", flush=True)
            e = build_ticker(tk, out, ds, args.strip_ix_header)
            entries.append(e)
            for v in full_tickers.values():
                if int(v["cik_str"]) == e["cik"]:
                    subset[str(len(subset))] = v
            print(f"[{tk}] {e['bytes'] / 1e6:.2f} MB, accessions={e['accessions']}", flush=True)

    (out / "company_tickers.json").write_text(
        json.dumps(subset, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    manifest = {
        "version": 1,
        "built_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "strip_ix_header": bool(args.strip_ix_header),
        "entries": entries,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    limit = ti("SEED_MAX_BYTES")
    print(f"TOTAL {total / 1e6:.2f} MB (limit {limit / 1e6:.2f} MB)")
    for e in entries:
        print(f"  {e['ticker']}: {e['bytes'] / 1e6:.2f} MB")
    if total > limit:
        print("OVER BUDGET: see docs/decisions/data.md for the size levers")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
