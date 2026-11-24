# PROGRESS

## M0 — Scaffold & contracts (2026-11-24) ✅
- uv + Python 3.11.18 bootstrapped; dependencies pinned in `pyproject.toml` and locked in `uv.lock`.
- `models.py` (§5.2 + DocRef), `config.py` (all thresholds, env overrides), `lexicons.py` (Appendix B + section regexes).
- Network-blocking `conftest.py`; CLI skeleton.

## M1 — Data layer + vertical slice ✅
- EDGAR client (≤5 req/s token bucket, retry/backoff, 403 circuit breaker), SQLite runtime cache, resolve, filing selection, LiveSource/SeedSource.
- Seed cache built from live SEC data for NVDA, WMT, TSLA, BA: 3.6 MB of the 15 MB budget (`seed_cache/manifest.json`).
- Appendix D verified against live payloads (docs/decisions/data.md D-101…D-109). A missing User-Agent returns 403, which is handled by the breaker.
- XBRL normalization; checks A1/A3; scorer. The four discrete quarters reconcile to the 10-K FY within $1M for NVDA and WMT.

## M2 — Remaining accounting checks + Ratios view ✅
- A2, A4, A5, acquisition guard, display-only metrics; Streamlit header, command bar, seed buttons, Ratios charts.

## M3 — Narrative extraction + Overview ✅
- html_text, sections (longest-span; WMT table-row headings), claims with negation/forward/intent rejection, period-aligned pairing, escalation.
- Citation invariant holds on all seeds (tests/unit/test_pipeline_seed.py, test_narrative_seed.py).

## M4 — Footnote diff + Proxy + Redline/Proxy views ✅
- iXBRL TextBlock segmentation (XML parse, continuation chains), note matching, normalization, alignment, material edits, modal softening, hedging drift.
- CD&A location, incentive context, weights, LOW_CONFIDENCE, C1/C2.

## M5 — PDF, polish, docs ✅
- Exactly-2-page tear-sheet (all seeds + 50-finding worst case); all §2.3 error/empty states; README; METHODOLOGY; `scripts/bench.py`.

### Independent verification (Fable, 2 auditors)
- Numbers audit: 1,072 values re-derived from raw companyfacts with no veritas imports, 0 mismatches; scores recomputed by hand. Two latent rule bugs (LTM-as-FY in A5; persistence skipping a quarter) and four minors fixed with regression tests.
- Text/UI/PDF audit: one UI blocker (cached re-analysis) and accuracy issues (earnings releases never scanned; spurious material terms; miscategorised notes; aspirational sentences escalating; PDF points and glyphs) fixed with regression tests.
- Golden snapshot of seed results: tests/golden/seed_expected.json.
- Re-verification (both Fable auditors): numbers PASS (1,278 values, 0 mismatches); text/UI/PDF "ready to show the owner". All remaining loose ends they listed were then fixed (footnote list ordering, citation order, formatting, intent-rule position, proxy analysis sentences, PDF labels).

### §12 performance (scripts/bench.py, median of 2, Apple Silicon)
| Scenario | Target | NVDA | WMT | TSLA | BA |
|---|---|---|---|---|---|
| Seed analyze() | ≤ 2 s | 0.58 | 0.74 | 0.71 | 1.05 |
| Parse + preprocess one filing | ≤ 5 s | 0.06 | 0.07 | 0.07 | 0.10 |
| Diff one filing pair | ≤ 5 s | 0.19 | 0.21 | 0.29 | 0.44 |
| PDF build | ≤ 5 s | 0.02 | 0.02 | 0.02 | 0.02 |

### Seed results (offline)
| Ticker | Score | Coverage | Notes |
|---|---|---|---|
| NVDA | 74 | 4/5 | A3 accruals CRITICAL; A1/A2 WARNING (acquisition-distorted, not escalated) |
| WMT | 95 | 3/4 | AR immaterial (A1 N/A); 1 material footnote edit; proxy LOW_CONFIDENCE (1 metric) |
| TSLA | 75 | 4/5 | A2 DIO WARNING; proxy LOW_CONFIDENCE (unassessed) |
| BA | 70 | 3/4 | A3 WARNING (CCR 0.52); C1 (non-GAAP pay metric vs FCF − SBC < 0) |

### Not verified
- Live mode end to end in the UI ("Refresh live", non-seed tickers). The live client was exercised by the seed build and is unit-tested with mocks.
- Financial-issuer and shell paths on real filers (covered by synthetic tests only).
