# VERITAS

VERITAS is a local Streamlit app that screens one US domestic SEC filer at a time. It pulls public data from SEC EDGAR, runs deterministic accounting checks, lines up management's narrative against those numbers, and produces a cited screening score and a two-page PDF tear-sheet. All analysis is heuristic and is meant to point to items for further reading.

It has four modules:

1. **Narrative–Numbers checks (Module A):** receivables (DSO), inventory (DIO), accruals and cash conversion, capitalization intensity, and recurring "one-time" restructuring, each paired with matching MD&A and earnings-release language.
2. **Footnote Redline (Module B):** year-over-year diff of financial-statement notes, with inserted, deleted, and softened language and a hedging-drift measure.
3. **Proxy Incentive X-Ray (Module C):** incentive metrics from the DEF 14A compensation discussion, cross-checked against stock-based compensation, free cash flow, and goodwill.
4. **Narrative–Numbers Consistency Score (Module D):** a capped, coverage-aware 0–100 screening score, with a PDF tear-sheet.

The full method, thresholds, and limitations are in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).


## Requirements

- **Python 3.11** (`requires-python = ">=3.11,<3.13"`), managed with **uv**.
- **uv** is installed at `~/.local/bin/uv` on this machine. If Python 3.11 is not yet available, run `~/.local/bin/uv python install 3.11`.

## Setup

From the repository root:

```bash
~/.local/bin/uv sync --extra dev
```

This creates `.venv/` with the pinned runtime and development dependencies from `uv.lock`.

## Configuration

Settings are read from environment variables. A local `.env` file is supported.

1. Copy the template: `cp .env.example .env`
2. Edit `.env` and set `VERITAS_SEC_USER_AGENT`.

`.env` is listed in `.gitignore` and must never be committed.

| Variable | Default | Meaning |
|---|---|---|
| `VERITAS_SEC_USER_AGENT` | *(unset)* | Required for live mode. Format `"AppName/Version Name email@domain"`. Rejected if empty or containing `yourdomain`, `example.com`, or `@example`. |
| `VERITAS_OFFLINE` | `0` | `1` = no network; seed + cache only |
| `VERITAS_CACHE_DIR` | `./cache` | Runtime cache location (gitignored) |
| `VERITAS_LOG_LEVEL` | `INFO` | Logging level |
| `VERITAS_<THRESHOLD>` | per `config.py` | Optional overrides for any threshold |

### SEC User-Agent

The SEC asks automated clients to identify themselves. Set `VERITAS_SEC_USER_AGENT` in `.env` using this format:

```text
AppName/Version Your Name you@yourdomain.com
```

Use your own name and contact address. The placeholder above is rejected on purpose. Without a valid value, live mode is disabled and only the seed tickers are available.

## Running the app

```bash
.venv/bin/streamlit run app.py
```

The header offers seed buttons for `NVDA`, `WMT`, `TSLA`, and `BA`, plus a command bar.

## Command-line interface

```bash
.venv/bin/python -m veritas analyze NVDA --offline --json
.venv/bin/python -m veritas analyze NVDA --offline --pdf OUT.pdf
```

- `--offline` uses only the seed and runtime cache, with no network access.
- `--json` prints the `AnalysisResult` as JSON.
- `--pdf OUT.pdf` writes the two-page PDF tear-sheet to the given path.

## Command bar

Commands are case-insensitive and whitespace-trimmed.

| Input | Action |
|---|---|
| `ANALYZE <T>` or bare `<T>` (matches `^[A-Z][A-Z0-9.\-/]{0,9}$`) | Run analysis |
| `CIK <digits>` | Run analysis by CIK |
| `DIFF` | Switch to Redline view |
| `CONTRA` | Switch to Overview with `severity_filter = CRITICAL` |
| `RATIOS`, `PROXY`, `OVERVIEW` | Switch view |
| `EXPORT` | Build PDF and reveal `st.download_button` |
| anything else | Inline error listing valid commands |

## Seed tickers and seed cache

The seed tickers are **NVDA**, **WMT**, **TSLA**, and **BA**. Their data is stored in `seed_cache/`, which is committed and read-only. Seed buttons load from the seed cache without network access.

Rebuild the seed cache with a live network connection and a valid `VERITAS_SEC_USER_AGENT`:

```bash
.venv/bin/python scripts/build_seed_cache.py --tickers NVDA WMT TSLA BA
```

Do not edit the seed cache by hand. The script writes `seed_cache/manifest.json` with the fetch time, accession numbers, and file hashes.

## Quality gate

Run this before every commit. It must pass.

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src/veritas && .venv/bin/pytest -q
```

Unit tests run with network access blocked. Tests marked `live` are excluded by default.

## Project layout

```text
veritas/
├── pyproject.toml            # deps, ruff, mypy, pytest config
├── uv.lock | requirements.lock
├── .env.example  .gitignore  # gitignore: .venv/ cache/ .env *.pdf
├── .streamlit/config.toml
├── app.py                    # thin Streamlit entry → veritas.ui
├── README.md                 # setup, User-Agent, usage, seed rebuild, limitations
├── DECISIONS.md  PROGRESS.md
├── docs/METHODOLOGY.md       # every formula, threshold, lexicon, limitation
├── scripts/
│   ├── build_seed_cache.py
│   └── bench.py
├── seed_cache/               # committed, read-only, ≤15 MB
├── src/veritas/
│   ├── __init__.py  __main__.py  cli.py
│   ├── config.py             # thresholds, weights, PARSER_VERSION
│   ├── lexicons.py           # all regexes from Appendix B
│   ├── models.py             # §5.2
│   ├── pipeline.py           # analyze()
│   ├── edgar/  client.py ratelimit.py cache.py resolve.py filings.py seed.py
│   ├── engine/ xbrl_normalize.py tags.py gating.py accounting_checks.py
│   │           html_text.py sections.py narrative.py footnote_diff.py
│   │           proxy_xray.py scorer.py
│   ├── export/ pdf_tearsheet.py
│   └── ui/     layout.py views.py commands.py styles.css
└── tests/ unit/ golden/ fixtures/ conftest.py
```

`DECISIONS.md` records every ambiguity resolved during the build and every SEC or XBRL behavior checked against live data.

## Known limitations

These are summarized here. The complete list is in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

- Text matching is heuristic. It misses unusually phrased claims and can pair sentences incorrectly.
- FX effects, LIFO, receivables factoring or securitization, segment mix, dimensional XBRL data, and company-specific extension tags are not modelled.
- 53-week fiscal years make one quarter about 7% longer. Growth-based metrics do not adjust for this.
- Restatements use as-restated values. Original values are flagged, not used.
- Proxy metrics that appear only in images or charts are not detected.
- Financial issuers, foreign private issuers, funds, and shells are out of scope or limited.
- The score is uncalibrated and not comparable across sectors.

## Disclaimer

> *Automated screening heuristics derived from public SEC filings. Flags indicate items warranting further review, not findings of misstatement, fraud, or wrongdoing. Heuristics can produce false positives; verify against the cited source filings. Not investment advice. Not audited. Not affiliated with the SEC.*
