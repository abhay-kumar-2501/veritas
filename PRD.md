# VERITAS — Narrative-vs-Numbers Screening Terminal
## Product Requirements Document v2.0 (build-ready)

> **For the coding agent:** Read this entire document before writing code. Section 0 is binding. If anything conflicts, the order of precedence is §0 > §5 (data contracts) > §7 (engine specs) > everything else. Where this PRD leaves something open, follow §0.3 — do not stop to ask.

---

## Table of Contents
0. Agent Operating Rules (read first)
1. Summary, Scope, Supported Coverage, Non-Goals
2. Personas & User Flows (incl. error flows)
3. Legal, Disclaimers & Language Rules
4. Environment & Bootstrap
5. Architecture & Data Contracts
6. SEC EDGAR Integration (endpoints, fair access, filing selection, cache)
7. Engine Specs
   - 7.0 XBRL Normalization (periods, dedup, tag fallbacks)
   - 7.1 Module A — Accounting Checks
   - 7.2 Module A — Narrative Claim Extraction & Pairing
   - 7.3 Module B — Footnote Redline Diff & Hedging Drift
   - 7.4 Module C — Proxy Incentive X-Ray
   - 7.5 Module D — Consistency Score
   - 7.6 Sector & Filer-Type Gating
8. UI Spec (Streamlit)
9. PDF Tear-Sheet Spec
10. Seed Cache & Test Fixtures
11. Testing Strategy & Quality Gate
12. Performance Targets
13. Repository Structure & Conventions
14. Milestones M0–M5 with Definition of Done
15. Known Limitations (shown to users)
16. Appendices: A) Tag fallbacks, B) Lexicons & regexes, C) DECISIONS.md template, D) Facts the agent must verify against live data

---

## 0. Agent Operating Rules (binding)

### 0.1 MUST NOT
1. Fabricate, estimate, interpolate, or "fill in" any financial value, quote, filing, or seed data. Every quote shown anywhere must be a verbatim substring of cached section text (enforced by a test, §7.2.6).
2. Return `0`, `""`, or a default for missing data. Missing = `None` plus an explicit status/reason.
3. Call any LLM, paid API, or any network host other than `www.sec.gov` and `data.sec.gov`.
4. Hardcode a personal name or email anywhere. Commit `cache/`, `.env`, or any secret.
5. Weaken, skip, or delete tests to make them pass. Mark a test `xfail` only with a linked `DECISIONS.md` entry.
6. Add dependencies beyond §4.2 without a `DECISIONS.md` entry.
7. Inject JavaScript or custom components for keyboard capture (§8 makes shortcuts a non-goal).
8. Use the words *fraud, manipulation, misleading, deceptive, stuffing, cooking the books* in any user-facing string about an issuer (enforced by a test that greps `src/` string literals and the rendered PDF).
9. Truncate a filing's input text silently. If a limit is hit, set a visible status (`TRUNCATED` / `TIMEOUT`).
10. Install packages into the system Python or downgrade code syntax to fit an older interpreter.

### 0.2 MUST
1. Put every threshold, weight, and lexicon in `config.py` (thresholds) or `lexicons.py` (regexes), never inline in engine code.
2. Attach a `Citation` (§5) to every number and quote shown in the UI or PDF.
3. Keep the analysis deterministic: the same inputs and `PARSER_VERSION` produce byte-identical `AnalysisResult` JSON (tested).
4. End every milestone with: quality gate green (§11.4), `PROGRESS.md` updated, a git commit.
5. Verify every XBRL tag name and SEC behavior listed in Appendix D against real payloads (seed data) before relying on it. Record the outcomes in `DECISIONS.md`.

### 0.3 Ambiguity protocol
When this PRD is silent or unclear, pick the simplest option consistent with it, add an entry to `DECISIONS.md` (template in Appendix C), and continue. Stop and report only when (a) there is no network access for a step that requires live SEC data, or (b) Python 3.11 cannot be obtained (§4.1).

---

## 1. Summary, Scope, Coverage, Non-Goals

### 1.1 Problem
Analysts read MD&A narratives, earnings releases, and long footnotes under time pressure. Optimistic management language can sit next to deteriorating working-capital, cash-conversion, or disclosure trends that are easy to miss. Existing terminals give raw filings and standardized financials. They do not systematically line up what management *said* against what the *numbers* show.

### 1.2 Solution
VERITAS is a local Streamlit app that, for one US domestic filer at a time, pulls public data from SEC EDGAR (free, no API key) and produces:
1. **Narrative-vs-Numbers checks (Module A):** deterministic accounting screens from XBRL (DSO, DIO, accruals/cash conversion, capitalization intensity, recurring restructuring). Each one is paired with matching management claims from MD&A and earnings releases for the same period.
2. **Footnote Redline (Module B):** year-over-year diff of financial-statement notes in comparable filings. It surfaces inserted, deleted, and softened language, and measures hedging drift.
3. **Proxy Incentive X-Ray (Module C):** incentive metrics from the DEF 14A CD&A, cross-checked against SBC, FCF, and goodwill trends.
4. **Narrative–Numbers Consistency Score (Module D):** a capped, coverage-aware 0–100 screening score, plus a **2-page PDF tear-sheet** with citations.

**Philosophy:** free public data; deterministic, reproducible, cited analysis; a small pinned dependency set. All text analysis is heuristic and labelled as such.

### 1.3 Supported coverage (v1)
- **Supported:** US domestic filers that file 10-K/10-Q with XBRL (companyfacts available).
- **Limited:** financial issuers, SIC 6000–6799 (§7.6).
- **Unsupported, with an explicit message:** 20-F/40-F/6-K filers (foreign private issuers), funds/ETFs, shells/SPACs (SIC 6770), pre-revenue issuers, delisted or renamed tickers that are absent from the current ticker file, legacy `.txt`-only filings.

### 1.4 Non-goals (v1)
DOCX export; keyboard shortcuts; hosted/multi-user deployment and authentication; bank- or insurance-specific credit metrics; segment/dimensional XBRL data; IFRS taxonomy; peer comparison; market/price data (hence no Altman Z); Beneish M-Score; LLM summarization; alerts; mobile layout.

---

## 2. Personas & User Flows

### 2.1 Personas
- **ER / IB / PE analyst:** pre-earnings or diligence screen of a public company.
- **Strategy / turnaround consultant:** outside-in diagnostic of a client or competitor.

### 2.2 Happy path
1. User opens the app. The header shows four seed buttons (`NVDA`, `WMT`, `TSLA`, `BA`) and the command bar.
2. User clicks a seed button or types `ANALYZE <TICKER>` (or just `<TICKER>`) and presses Enter.
3. A staged progress panel runs: Resolve CIK → Fetch submissions → Fetch company facts → Fetch filings → Module A → B → C → Score.
4. The Overview view renders: score header, module status chips, and finding cards.
5. User switches views (Overview / Redline / Ratios / Proxy) with a horizontal selector, or with the commands `DIFF` and `CONTRA`.
6. `EXPORT` (command or button) builds the PDF and shows a download button.

### 2.3 Error & edge flows (each one needs a specific UI message)
| Situation | Behavior |
|---|---|
| Ticker not in SEC ticker file | "Ticker not found (delisted/renamed tickers unsupported)" plus up to 5 prefix suggestions |
| Raw CIK typed (`CIK 320193`) | Accept and resolve directly |
| No 10-K in last 3 years (FPI, fund, recent IPO) | "Unsupported filer type in v1"; stop |
| SIC 6770 or TTM revenue missing/0 | "Shell / pre-revenue: forensic checks not applicable"; no score |
| SIC 6000–6799 | Banner "Financial issuer: limited scope" (§7.6) |
| companyfacts 404 | "No structured XBRL data for this filer"; stop |
| SEC 403 | Open circuit breaker for 10 min; serve cache if any; banner "SEC access temporarily blocked; showing cached data" |
| 429/5xx/timeout after retries | Serve stale cache with banner; else error panel with retry button |
| `VERITAS_SEC_USER_AGENT` unset or placeholder | Live mode disabled; seed tickers only; banner explains how to set it |
| A module fails or times out | That module shows a grey "Not assessed: {reason}" panel; other modules continue |
| No DEF 14A | Module C "Proxy not available"; no deduction |

---

## 3. Legal, Disclaimers & Language Rules
1. The product name for the score is **"Narrative–Numbers Consistency Score"**. Never use "Integrity Score".
2. Severity labels are exactly: `CRITICAL`, `WARNING`, `NO FLAG`, `N/A`. Do **not** use "VERIFIED" (absence of a flag is not verification).
3. Flags use neutral, descriptive names, e.g. "Receivables outpacing sales (DSO up)", not "Channel stuffing".
4. A fixed disclaimer appears in the footer of every view and on both PDF pages:
   > *Automated screening heuristics derived from public SEC filings. Flags indicate items warranting further review, not findings of misstatement, fraud, or wrongdoing. Heuristics can produce false positives; verify against the cited source filings. Not investment advice. Not audited. Not affiliated with the SEC.*
5. Every finding exposes its formula, its input values, and source citation links.
6. Scores are uncalibrated and not comparable across sectors. Say so in the methodology text.

---

## 4. Environment & Bootstrap

### 4.1 Python
- Target **Python 3.11** (`requires-python = ">=3.11,<3.13"`).
- Bootstrap order:
  1. If `uv` exists: `uv python install 3.11 && uv venv --python 3.11 && uv pip install -e ".[dev]"`.
  2. Else if `python3.11` exists: `python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
  3. Else STOP. Tell the human that Python 3.11 is required and suggest installing `uv` (https://docs.astral.sh/uv/). The development machine is known to have only system Python 3.9.
- Commit a lock (`uv.lock`) or a fully pinned `requirements.lock`.

### 4.2 Dependencies (pin exact versions at M0 and record them)
Runtime: `requests`, `lxml`, `scikit-learn`, `plotly`, `streamlit`, `reportlab`, `python-dotenv`.
Dev: `pytest`, `pytest-cov`, `ruff`, `mypy`, `pypdf` (PDF assertions).
**Not used:** BeautifulSoup (lxml directly), fpdf2, python-docx, any LLM SDK.

### 4.3 Configuration (env vars, `.env` supported, `.env.example` committed)
| Var | Default | Meaning |
|---|---|---|
| `VERITAS_SEC_USER_AGENT` | *(unset)* | Required for live mode. Format `"AppName/Version Name email@domain"`. Rejected if empty or containing `yourdomain`, `example.com`, or `@example`. |
| `VERITAS_OFFLINE` | `0` | `1` = no network; seed + cache only |
| `VERITAS_CACHE_DIR` | `./cache` | Runtime cache location (gitignored) |
| `VERITAS_LOG_LEVEL` | `INFO` | Logging level |
| `VERITAS_<THRESHOLD>` | per `config.py` | Optional overrides for any threshold |

---

## 5. Architecture & Data Contracts

### 5.1 Pipeline
```
analyze(ticker_or_cik, *, offline=False) -> AnalysisResult
  resolve → submissions → filing selection (§6.4) → companyfacts → normalize (§7.0)
  → Module A numeric (§7.1) → narrative extraction & pairing (§7.2)
  → Module B (§7.3) → Module C (§7.4) → score (§7.5)
```
- The UI (`app.py`) and the PDF builder consume **only** `AnalysisResult`. They never call engine modules directly.
- CLI: `python -m veritas analyze <TICKER> [--offline] [--json] [--pdf OUT.pdf]`.
- Each module runs inside a `try/except Exception`. An exception becomes `ModuleResult(status="error", reason=...)`, is logged with traceback, and never crashes the pipeline.

### 5.2 Contracts (`src/veritas/models.py`, frozen dataclasses, fully typed, JSON-serializable via `to_dict()`)
```python
Severity = Literal["CRITICAL", "WARNING", "NO_FLAG"]
CheckState = Literal["COMPUTED", "NOT_APPLICABLE", "NOT_COMPUTABLE"]
ModStatus = Literal["ok", "partial", "unavailable", "low_confidence", "timeout", "error"]


@dataclass(frozen=True)
class Citation:
    cik: int
    ticker: str
    form: str
    accession: str  # accession WITH dashes
    filing_date: date
    report_date: date | None
    doc_name: str
    url: str  # full Archives URL
    section: str  # "10-K Item 7", "Note: <concept>", "DEF 14A CD&A", "XBRL"
    quote_raw: str | None  # verbatim, entity-decoded, whitespace-collapsed only
    char_start: int | None
    char_end: int | None  # offsets into cached section text
    xbrl_tag: str | None
    parser_version: str


@dataclass(frozen=True)
class MetricPoint:
    start: date | None
    end: date  # start=None for instants
    value: float
    unit: str
    xbrl_tag: str
    accession: str
    form: str
    filed: date
    derived: bool  # True if computed by YTD differencing
    restated: bool  # earliest-filed value differs >1% from latest


@dataclass(frozen=True)
class MetricSeries:
    name: str
    frequency: Literal["instant", "quarter", "fy", "ttm"]
    points: tuple[MetricPoint, ...]
    tag_changed: bool


@dataclass(frozen=True)
class CheckResult:
    check_id: (
        str  # "A1_DSO", "A2_DIO", "A3_ACCRUALS", "A4_CAPRATE", "A5_RESTRUCT", display-only: "X_*"
    )
    title: str
    state: CheckState
    severity: Severity | None  # severity None unless COMPUTED
    period_end: date | None
    inputs: dict[str, float | None]
    outputs: dict[str, float | None]
    formula: str
    reason: str | None
    annotations: tuple[str, ...]  # e.g. "acquisition-distorted", "tag changed"
    citations: tuple[Citation, ...]
    scored: bool


@dataclass(frozen=True)
class Claim:
    cluster: Literal["DEMAND", "MARGIN", "ONETIME"]
    citation: Citation
    period_end: date


@dataclass(frozen=True)
class Finding:
    id: str
    module: Literal["A", "B", "C"]
    category: str  # scoring category, §7.5
    severity: Severity
    title: str
    explanation: str
    check: CheckResult | None
    claims: tuple[Claim, ...]
    contradiction: bool
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class ModuleResult:
    status: ModStatus
    findings: tuple[Finding, ...]
    checks: tuple[CheckResult, ...]
    reason: str | None
    extra: dict[str, Any]  # module-specific display payload (redlines, proxy table)


@dataclass(frozen=True)
class ScoreBreakdown:
    score: int | None
    coverage_num: int
    coverage_den: int
    deductions: tuple[tuple[str, int], ...]  # (category, capped points)
    label: str  # e.g. "Score 72 · coverage 4/5" or "INSUFFICIENT DATA"
    scope_note: str | None  # e.g. "Financial issuer: limited scope"


@dataclass(frozen=True)
class AnalysisResult:
    ticker: str
    cik: int
    company_name: str
    sic: str | None
    fiscal_year_end: str | None
    as_of: datetime
    source: Literal["live", "cache", "seed"]
    parser_version: str
    filings: tuple[Citation, ...]
    metrics: dict[str, MetricSeries]
    modules: dict[Literal["A", "B", "C"], ModuleResult]
    score: ScoreBreakdown
    banners: tuple[str, ...]
```

Modules never assign score points; only `scorer.py` does (§7.5).

---

## 6. SEC EDGAR Integration

### 6.1 Hosts & endpoints
- JSON APIs live on `data.sec.gov`. Ticker files and documents live on `www.sec.gov`. Always use `https`, and always the `www.` prefix for www-host URLs.
- `cik10` = CIK zero-padded to 10 digits. `cik_int` = unpadded integer. `acc` = `0000320193-24-000123`. `acc_nodash` = 18 digits.

| # | Purpose | URL |
|---|---|---|
| 1 | Ticker map | `https://www.sec.gov/files/company_tickers.json` — dict keyed `"0","1",…` of `{cik_str, ticker, title}` |
| 2 | Submissions | `https://data.sec.gov/submissions/CIK{cik10}.json` |
| 2b | Older submissions pages | `https://data.sec.gov/submissions/{name}` for each `filings.files[].name` |
| 3 | Company facts | `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json` |
| 4 | Filing document | `https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{primaryDocument}` |
| 5 | Filing index | `https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json` (`directory.item[] = {name, type, size, …}`) |

All fetching happens server-side in Python (data.sec.gov does not allow browser CORS).

### 6.2 Fair-access compliance
- Headers on every request: `User-Agent: $VERITAS_SEC_USER_AGENT`, `Accept-Encoding: gzip, deflate`. Do not set `Host`.
- One **process-global, thread-safe** token bucket (`threading.Lock`, held via `st.cache_resource` in the app): **≤ 5 requests/second**, at most 2 in flight. The SEC cap is 10/s per IP; we stay well under it.
- Timeouts: connect 5 s, read 30 s.
- Retry only on 429, 5xx, and connection errors: 3 attempts, backoff 1 s / 2 s / 4 s plus jitter; honor `Retry-After`.
- **403:** do not retry. Open a 10-minute circuit breaker for all live requests and fall back to cache (§2.3).
- **404 on companyfacts:** "no XBRL" (not an error).
- Never download full-submission `.txt` bundles. Stream documents and abort above 40 MB (status `TRUNCATED`, module continues without that doc).

### 6.3 Ticker resolution
Normalize: strip, uppercase, replace `.` and `/` with `-` (SEC uses `BRK-B`). Match exactly on `ticker`. Many tickers can map to one CIK (GOOG/GOOGL); cache by CIK. Accept `CIK <digits>` input.

### 6.4 Filing selection (from submissions; `filings.recent` is columnar parallel arrays)
Page through `filings.files` if the needed forms are not in `recent`.
| Need | Rule |
|---|---|
| Latest 10-K | Newest `form == "10-K"` exactly (also `10-KT`). Ignore `10-K/A` for text; show a notice if a newer `10-K/A` exists. |
| Prior 10-K | Next-older `10-K` (for the footnote diff). |
| 10-Qs | Newest 4 with `form == "10-Q"`. Pair the latest 10-Q with the 10-Q of the same fiscal quarter one year earlier (match on `reportDate` 365±10 days). |
| Proxy | Newest `form == "DEF 14A"` exactly, filed within 18 months. Never `DEFA14A`, `DEFM14A`, `DEFR14A`, `PRE 14A`. |
| Earnings 8-K | `form == "8-K"` and `"2.02"` in `items.split(",")`, with `filingDate` within `[period_end, periodic_filing_date + 5 days]` for the periodic filing being analyzed. Document = `index.json` item typed `EX-99.1`, else first `EX-99*`, else name matching `/ex-?99[._-]?1/i`. If none, skip silently. |
| Primary doc | Only parse `.htm`/`.html`. Empty, `.txt`, or `.pdf` → status "document unavailable". |
| Metadata | `sic` (string) and `fiscalYearEnd` (`"MMDD"`) come from submissions. |

### 6.5 Cache (two layers)
**(a) Seed cache `seed_cache/`:** read-only, committed (§10).

**(b) Runtime cache `$VERITAS_CACHE_DIR`:** gitignored.
- SQLite table `http_cache(url TEXT PRIMARY KEY, fetched_at, status, body BLOB /*gzip*/)` and table `derived(key TEXT PRIMARY KEY, created_at, body BLOB)`.
- Open a new connection per call/thread; WAL mode; `busy_timeout=5000`.

TTLs:
| Resource | TTL |
|---|---|
| Archives documents and `index.json` | Never expire (immutable; key includes accession) |
| `company_tickers.json` | 24 h |
| Submissions | 6 h |
| Companyfacts | 24 h, or sooner if submissions shows a newer 10-K/10-Q accession than the cached facts |
| Derived artifacts (extracted sections, segments, citations, normalized series) | Keyed by `(accession, PARSER_VERSION)` |

Behavior:
- **Stale-on-error:** if a live fetch fails and an expired entry exists, serve it and add a "stale data" banner.
- A per-CIK in-process lock prevents duplicate concurrent fetches during Streamlit reruns.
- **Eviction:** LRU above 500 MB.

---

## 7. Engine Specs

### Global numeric rules
- `growth(x) = (x_t − x_{t−1}) / x_{t−1}`, defined only when `x_{t−1} > 0`; otherwise the result is `None` and the check is `NOT_COMPUTABLE`.
- Thresholds on differences of ratios are in **percentage points (pp)**. Thresholds on relative change are written **rel %**.
- Never compare sequential quarters. Always compare the same fiscal quarter one year earlier (seasonality-neutral).
- `days(period) = (end − start).days + 1`.

### 7.0 XBRL Normalization (`engine/xbrl_normalize.py`)
1. Fact shape: `facts[taxonomy][tag].units[unit][] = {start?, end, val, accn, fy, fp, form, filed, frame?}`.
2. **`fy`/`fp`/`frame` describe the reporting filing, not the fact's period. Never key on them.** Key on `(tag, unit, start, end)`.
3. Accept forms `10-K, 10-K/A, 10-Q, 10-Q/A, 10-KT, 10-QT`. Units: `USD` for money, `shares`, `USD/shares`. Never mix units.
4. **Dedup:** for identical `(tag, unit, start, end)`, keep the row with the latest `filed` (as-restated). Set `restated=True` when the earliest-filed value differs from the latest by more than 1%.
5. **Duration classes** by `days`: 3M = 80–100; 6M = 170–190; 9M = 260–285; FY = 350–380. Discard any other duration.
6. **Discrete quarters:** use the 3M fact if present. Otherwise derive Q2 = 6M − 3M, Q3 = 9M − 6M, Q4 = FY − 9M, where all components share the same fiscal-year `start` and the same tag. Set `derived=True`. If any component is missing, the value is `None`. 10-Q cash-flow items are YTD-only, so they are always derived.
7. **Fiscal calendar:** never assume December. A fiscal year is defined by the FY fact's `(start, end)`. Label periods by end date ("FQ ending 2025-01-26"). WMT (Jan 31) and NVDA (last Sunday of January; 52/53-week years) are the reference cases.
8. **YoY match:** the prior period is the same duration class with `end` 365 ± 10 days earlier. For instants, the instant at that prior end (± 10 days).
9. **TTM** = sum of the last 4 discrete quarters; all 4 are required, otherwise `None`.
10. **Tag fallback (Appendix A):** applied per period. The first tag with a value wins. Record the tag used. If current and comparison periods used different tags, set `tag_changed=True` and annotate the check.
11. companyfacts excludes dimensional and company-extension facts. That is out of scope.

### 7.1 Module A — Accounting Checks (`engine/accounting_checks.py`)
**Evaluation point:** each check is evaluated at a discrete quarter end `P`.
- The primary result is the latest quarter with data.
- Series for the last 12 quarters are kept for charts.
- Narrative pairing (§7.2) uses `P` = the `reportDate` of the filing a claim came from.

Every check returns a `CheckResult`. Rules:
- If an input is missing after fallback → `NOT_COMPUTABLE` with the specific reason ("AR not tagged", "prior-year quarter missing").
- If a materiality or sector gate fails → `NOT_APPLICABLE` with the reason.

#### A1 — Receivables outpacing sales (DSO) — scored
- `DSO_q = AR_end / Revenue_q × days(q)`. `ΔDSO = DSO_q − DSO_{q,−1y}` (days). `rel = ΔDSO / DSO_{q,−1y}`.
- **Gates:**
  - `Revenue_q ≤ 0` or prior-year data missing → `NOT_COMPUTABLE`.
  - `AR_end < 2%` of TTM revenue → `NOT_APPLICABLE` ("receivables immaterial"; expected for WMT).
- **WARNING:** ΔDSO ≥ +5 days **and** rel ≥ +10%.
- **CRITICAL:** ΔDSO ≥ +10 days **and** rel ≥ +20% **and** the WARNING condition also held at the previous quarter end.
- Otherwise `NO_FLAG`.
- *Why DSO and not growth-rate differences:* NVDA FY2024 revenue rose about 126% while AR rose about 161% (a +35 pp "divergence"), yet quarter-based DSO **fell**. A growth-difference rule raises a false CRITICAL. A test asserts NVDA is not CRITICAL on A1.

#### A2 — Inventory outpacing cost of sales (DIO) — scored
- `DIO_q = Inventory_end / COGS_q × days(q)`. Same ΔDIO and rel thresholds and persistence rule as A1.
- **Gates:** `COGS_q ≤ 0` → `NOT_COMPUTABLE`. `Inventory_end < 2%` of TTM COGS, or no inventory tag reported at all → `NOT_APPLICABLE`.

#### Acquisition guard (applies to A1, A2)
If TTM `PaymentsToAcquireBusinessesNetOfCashAcquired ≥ 5%` of prior-year `Assets`, **or** `Goodwill` rose ≥ 10% YoY:
- cap the severity at WARNING;
- add the annotation `"acquisition-distorted"`.

#### A3 — Accruals / cash conversion — scored
- **Primary (Sloan):** `AccrualRatio = (NI_TTM − OCF_TTM) / avg(Assets_P, Assets_{P−1y})`. WARNING ≥ 5%; CRITICAL ≥ 10%.
- **Secondary:** `CCR = OCF_TTM / NI_TTM`. Compute it only if `NI_TTM > 0` **and** `NI_TTM ≥ 2%` of TTM revenue; otherwise display "n/m". WARNING if CCR < 0.75. CRITICAL if CCR < 0.5, **or** if `NI_TTM` rose YoY while `OCF_TTM` fell ≥ 10% rel.
- **Loss-making** (`NI_TTM ≤ 0`): use the accrual ratio only, with the note "loss-making: cash conversion n/m".
- Severity = max(primary, secondary). The check counts once.

#### A4 — Capitalization intensity — scored
- `CapSW` = `PaymentsToDevelopSoftware`, else `CapitalizedComputerSoftwareAdditions` (TTM).
- `CapRate = CapSW_TTM / (CapSW_TTM + ResearchAndDevelopmentExpense_TTM)`.
- **Gates:** tags absent → `NOT_COMPUTABLE` (expected for many issuers). `CapSW_TTM < 1%` of revenue → `NOT_APPLICABLE`.
- WARNING: CapRate up ≥ 5 pp YoY. CRITICAL: up ≥ 10 pp YoY.

#### A5 — Recurring "one-time" restructuring — scored
- `R_q` = `RestructuringCharges`, else `RestructuringCostsAndAssetImpairmentCharges`, else `RestructuringAndRelatedCostIncurredCost` (discrete quarter per §7.0).
- A quarter **counts** if `|R_q| ≥ 0.5%` of that quarter's revenue.
- WARNING: ≥ 3 of the last 4 quarters count. CRITICAL: ≥ 6 of the last 8 count, **or** a material charge in each of the last 3 fiscal years.
- No restructuring tag in any period → `NO_FLAG`, note "no restructuring tags reported".
- Impairments (`GoodwillImpairmentLoss`, `AssetImpairmentCharges`, `ImpairmentOfLongLivedAssetsHeldForUse`) are displayed alongside but never trigger A5.

#### Display-only metrics (`scored=False`, shown on the Ratios view, never scored)
- `X_CAPEX_DA`: CapEx_TTM / D&A_TTM. Note when ≥ 2.0 and up ≥ 25% rel. D&A ≤ 0 → n/m.
- `X_DPO`: AP_end / COGS_q × days. Note "payables stretch supporting OCF" if up ≥ 10 days and ≥ 20% rel.
- `X_FCF`: `OCF_TTM − CapEx_TTM`. `X_FCF_SBC`: `FCF − SBC_TTM`.
- `X_ROIC`:
  - `NOPAT = OperatingIncomeLoss_TTM × (1 − t)`, with `t = clamp(IncomeTaxExpenseBenefit_TTM / PreTaxIncome_TTM, 0, 0.35)`; use 0.21 if pre-tax income ≤ 0.
  - `IC = avg(TotalDebt + StockholdersEquity − CashAndCashEquivalentsAtCarryingValue)` over P and P−1y.
  - IC ≤ 0 → n/m (expected for BA).
- `X_RESTATED`: list the metrics with `restated=True` points.

### 7.2 Module A — Narrative Claim Extraction & Pairing (`engine/narrative.py`)

#### 7.2.1 HTML preprocessing (shared by all text modules: `engine/html_text.py`)
- Parse with `lxml.html` (`huge_tree=True`). No BeautifulSoup.
- Remove `ix:header`, `script`, `style`, and elements whose inline style matches `display:\s*none`. Unwrap other `ix:*` tags, keeping their text.
- Emit one line per block element (`p, div, td, li, h1–h6, br, tr`). Serialize table rows as cells joined with ` | `.
- **Normalize:** NFKC; `\xa0`, ` `, `​` → space; `’ ‘` → `'`; `“ ”` → `"`; `– —` → `-`; delete `\xad`; collapse whitespace.
- **Drop lines:**
  - lines matching `^\d{1,3}$` or `^table of contents$` (case-insensitive);
  - any line of ≤ 12 words repeated ≥ 5 times (page headers/footers).
- **For narrative scanning only:** drop "data table" rows where ≥ 40% of non-empty cells match `^[\$\(\)\d,.\-%\s]+$`.
- The cached section text is the post-normalization text. Citation offsets refer to it.

#### 7.2.2 Section location
- **Longest-span rule:** compute the span for every start match and choose the longest. This defeats table-of-contents and cross-reference hits.
- **10-K MD&A:**
  - start `(?im)^item\s*7\s*[.:\-]?\s*management'?s?\s+discussion`;
  - end at the next `(?im)^item\s*(7a|8)\b`;
  - require ≥ 1,500 words.
- **10-Q MD&A:**
  - start `(?im)^item\s*2\s*[.:\-]?\s*management'?s?\s+discussion` (the title is mandatory, which excludes Part II Item 2);
  - end `(?im)^(item\s*3\b|item\s*4\s*[.:\-]?\s*controls|part\s+ii\b)`;
  - require ≥ 800 words.
- **Exhibit 13 fallback:** if the span is too short and contains `incorporated\s+(herein\s+)?by\s+reference`:
  - open the `index.json` document typed `EX-13*`;
  - take the span from heading `management'?s\s+discussion\s+and\s+analysis` to heading `(report of independent registered|consolidated (balance sheets?|statements? of))`.
- **8-K earnings release:** text of EX-99.1 up to the first data table or the paragraph containing `forward-looking statements`, whichever comes first.
- **Failure:** `section_status = "NOT_FOUND"`. The UI shows "Narrative unavailable for this filing". Numeric checks still render. Never fall back to whole-document text.

#### 7.2.3 Sentence splitting
- Mask the periods in `Inc. Corp. Co. Ltd. U.S. No. vs. e.g. i.e. Mr. Ms. Dr. approx.`, in month abbreviations, and in `\d\.\d`.
- Split on `(?<=[.!?])\s+(?=["(]?[A-Z])`.
- Keep sentences of 8–80 words.

#### 7.2.4 Claim clusters & rejection (regexes in Appendix B)
A sentence matching a cluster is a **claim** unless any of these hold:
- **Negation:** a negation/negative token appears within the 6 tokens before the match.
- **Forward-looking:** the sentence contains a forward-looking marker (tagged `FORWARD`, never used).
- **Boilerplate:** the paragraph mentions `forward-looking statements` or `Private Securities Litigation Reform`.
- **Out of scope:** the sentence lies outside the located section. Item 1A Risk Factors is never scanned.

#### 7.2.5 Pairing (fixed map) & period alignment
| Check | Paired clusters |
|---|---|
| A1 DSO | DEMAND |
| A2 DIO | DEMAND, MARGIN |
| A3 Accruals | — (numbers only) |
| A4 CapRate | MARGIN |
| A5 Restructuring | ONETIME |

- **Period alignment:** a claim from a filing with `reportDate = P` pairs only with that check evaluated at quarter end `P`.
  - For a 10-K, `P` = fiscal year end (Q4, derived).
  - An 8-K pairs with the period of the 10-Q/10-K it precedes.
- **Finding severity:**
  - start from the check's numeric severity (§7.1);
  - if it is ≥ WARNING **and** ≥ 1 paired claim exists for the same `P`, set `contradiction=True` and escalate WARNING → CRITICAL;
  - **exception:** an `acquisition-distorted` check is never escalated above WARNING.
- A numeric flag with no claim is shown as "Unaddressed divergence" with no quote.
- A claim whose paired check is `NO_FLAG` is shown under "Claims consistent with numbers" (informational, unscored).
- Show at most 2 quotes per check. Prefer MD&A over 8-K, then the earliest position.

#### 7.2.6 Citation invariant
`section_text[char_start:char_end] == quote_raw` for every Citation with a quote. This is unit-tested over all seed tickers. Quotes longer than 60 words are truncated for display only, at a word boundary with "…".

### 7.3 Module B — Footnote Redline & Hedging Drift (`engine/footnote_diff.py`)

#### 7.3.1 Filing pairs
- 10-K vs prior-year 10-K (default).
- Latest 10-Q vs the same fiscal quarter's 10-Q one year earlier.
- Never compare across form types. Ignore amendments.

#### 7.3.2 Segmentation
- **Primary (iXBRL):** each `ix:nonNumeric` whose `name` ends in `TextBlock` is a segment keyed by concept name.
  - Follow `continuedAt` → `ix:continuation` chains.
  - Prefer note-level blocks; nest `*PolicyTextBlock` segments inside their note.
- **Fallback:** within Item 8 (10-K) or Part I Item 1 (10-Q), after the longest-span match of `notes\s+to\s+(the\s+)?(condensed\s+)?(consolidated\s+)?financial\s+statements`.
  - A header is a line of ≤ 15 words matching `^(note\s+)?\d{1,2}\s*[.:\-]\s*[A-Z]`.
- **Note matching across years**, in this order:
  1. identical concept name;
  2. title-token Jaccard ≥ 0.5;
  3. TF-IDF cosine of the bodies ≥ 0.6.
- Note numbers are never used for matching. Unmatched notes → `NEW NOTE` / `REMOVED NOTE` (displayed, unscored).
- **Categories** by title/concept keyword:
  - `revenue`; `debt|borrowing|credit`; `inventor`; `commitment|contingenc|legal|litigation`; `goodwill|intangible`; `related part`.
  - All other notes are displayed only.

#### 7.3.3 Normalization (comparison copy only; always display original text). Apply in order:
1. Dates → `<DATE>`: `(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b`
2. Amounts → `<AMT>`: `\(?\$\s?[\d,]+(\.\d+)?\)?(\s?(thousand|million|billion|[MB])\b)?`
3. Percent → `<PCT>`: `[\d,.]+\s?(%|percent|basis points|bps)`
4. Years → `<YEAR>`: `\b(19|20)\d{2}\b`
5. Other numbers → `<NUM>`: `\b[\d,]+(\.\d+)?\b`
6. Lowercase.

#### 7.3.4 Alignment (per matched note)
1. Remove sentences whose normalized form appears on both sides (hash set; this also absorbs moved text).
2. On the residue, compute TF-IDF (word 1–2-grams) cosine. A greedy one-to-one match at ≥ 0.55 → `MODIFIED`. Unmatched sentences → `INSERTED` / `DELETED`.
3. Run `difflib.SequenceMatcher(autojunk=False)` on the tokens of `MODIFIED` pairs only, for the word-level redline.
4. Cap the residue at 400 sentences per side. Beyond that, mark the note `TRUNCATED` (displayed) and do not score it.

#### 7.3.5 Material edit (scored, deterministic)
The changed sentence must satisfy all of:
- it is in a category note;
- it has ≥ 12 words;
- it contains `recogni[sz]|covenant|default|waiver|reserve|allowance|impair|probable|reasonably possible|estimat|useful li(fe|ves)|going concern|restat|material weakness|related part|guarantee`;
- **and** one of:
  - it is `INSERTED` or `DELETED`;
  - it is `MODIFIED` with SequenceMatcher ratio < 0.85;
  - it is a modal softening (below).

Exclude sentences that contain `ASU|accounting standards update|recently (issued|adopted)`.

**Modal softening:** the MODIFIED pair's maximum modal strength among deleted tokens is greater than the maximum among inserted tokens. Strengths:
- 3: `will|shall|must|is required to|are required to`
- 2: `expects? to|intends? to|plans? to`
- 1: `may|might|could|seeks? to|intends? to seek|may not be able to|no assurance`

Match longer phrases first.

#### 7.3.6 Hedging drift (10-K pair only)
- **Corpus:** concatenated matched category notes, normalized.
- **Rate:** hedge hits per 1,000 tokens (lexicon in Appendix B).
- **Fires only if all hold:**
  - both sides have ≥ 1,500 tokens;
  - relative increase > 15%;
  - absolute increase ≥ 1.5 per 1,000;
  - hit count up by ≥ 5.
- Otherwise display "No significant drift" or "Insufficient text".

### 7.4 Module C — Proxy Incentive X-Ray (`engine/proxy_xray.py`)
#### 7.4.1 Source
- Use the DEF 14A selected in §6.4.
- If there is none, use 10-K Part III Item 11 when it has ≥ 1,500 words.
- Otherwise the status is `unavailable`, with 0 deduction.

#### 7.4.2 CD&A span
- Start: `compensation\s+discussion\s+(and|&)\s+analysis`.
- End: `compensation\s+committee\s+report|summary\s+compensation\s+table`.
- Apply the longest-span rule.
- If the start is not found, use the span from heading `executive\s+compensation` to the end pattern.

#### 7.4.3 Metric detection (aliases in Appendix B)
- **Incentive context:** a hit counts only if its sentence or table row also contains `weight\w*|target|threshold|maximum|payout|performance\s+(metric|measure|goal)|annual\s+incentive|bonus|PSU|performance\s+share`.
- Exclude hits under headings matching `non-gaap|reconciliation|definitions?|appendix|annex`, and sentences containing `is defined as|we define|\bmeans\b`.
- **Weight** = `(\d{1,3})\s?%` in the same table row as the alias, in a table whose header row contains `weight`. Otherwise `weight=None`, displayed as "weight not parsed".
- **LOW_CONFIDENCE:** fewer than 2 metrics detected, or the CD&A has ≥ 3 `<img>` and fewer than 2 metrics. Show the extracted text, raise no flags, and deduct 0.

#### 7.4.4 Flags
Both flags below are WARNING and scored; §7.5 sets the points.

**C1 SBC vs non-GAAP pay.** Fires if a non-GAAP metric (ADJ_EBITDA or NONGAAP_EPS) is detected **and** any of:
- `SBC_TTM / Revenue_TTM ≥ 10%`;
- `SBC_TTM ≥ 50%` of `FCF_TTM` (when FCF > 0);
- `FCF_TTM > 0` and `FCF − SBC < 0`.

**C2 Acquisition-heavy goodwill.** Fires if a REVENUE or M&A metric is detected **and** `Goodwill/Assets ≥ 35%` **and** either:
- Goodwill/Assets is up ≥ 3 pp YoY; or
- the acquisition guard (§7.1) is tripped.

**Note (unscored):** "ROIC/FCF not identified among incentive metrics". Shown only if ≥ 2 other metrics were detected and neither alias appears anywhere in the CD&A. Wording must not claim "zero weighting".

### 7.5 Module D — Consistency Score (`engine/scorer.py`)
| Category | Source | Per CRITICAL | Per WARNING | Category cap |
|---|---|---|---|---|
| `accounting` | A1–A5 findings (post-escalation) | 12 | 5 | 45 |
| `footnotes` | Material edits (§7.3.5), 10-K pair only | — | 5 each | 20 |
| `incentives` | C1, C2 | — | 5 each | 10 |
| `hedging` | Hedging drift fired | — | 5 | 5 |

- **De-dup:** if A3 fires together with A1 or A2 at the same `P`, halve A3's points (round down).
- `score = max(0, 100 − Σ capped category deductions)`.
- **Coverage** = (scored accounting checks with state `COMPUTED`) / (scored accounting checks not `NOT_APPLICABLE`).
  - If the denominator is 0, or coverage < 60% → `score=None` and the label "INSUFFICIENT DATA".
  - Otherwise the label is `"Score {n} · coverage {num}/{den}"`.
- Modules B and C contribute only when their status is `ok` or `partial`. Show "{k} of 3 modules assessed" next to the score.
- Never reward missing data: an unassessed module deducts nothing, **and** it is visibly listed as unassessed.

### 7.6 Sector & Filer-Type Gating (`engine/gating.py`, runs first)
1. **No 10-K in last 3 years** → unsupported filer; stop.
2. **SIC 6770, or TTM revenue missing/0** → shell/pre-revenue; Module A numeric checks `NOT_APPLICABLE`; no score.
3. **SIC 6000–6799 (financial issuer):**
   - A1, A2, A4 → `NOT_APPLICABLE ("financial issuer")`.
   - A3 computed but `scored=False`, labelled "low reliability for financials".
   - A5 runs normally. Modules B and C run normally.
   - `scope_note = "Financial issuer: limited scope; sector credit metrics not supported in v1."`
4. **Ordinary filer:** everything runs.

---

## 8. UI Spec (Streamlit, `app.py` + `src/veritas/ui/`)

### 8.1 Layout & state
- `st.set_page_config(layout="wide")`. Theme via `.streamlit/config.toml` plus one CSS file.
- **Colors:**

  | Token | Hex |
  |---|---|
  | Background | `#0B0E14` |
  | Panels | `#131822` |
  | NO FLAG | `#00E676` |
  | CRITICAL | `#FF3B30` |
  | WARNING | `#FFB300` |
  | N/A | grey `#6B7280` |

- Fonts: CSS stack `"JetBrains Mono","SF Mono",Menlo,Consolas,monospace`. **No external font fetch.**
- **State:** `st.session_state` keys `ticker`, `view`, `result`, `severity_filter`, `pdf_bytes`, `cmd_error`.
- Pipeline call: `analyze()` wrapped in `st.cache_data(ttl=3600)`, keyed by `(cik, offline)`. The rate limiter and DB handle live in `st.cache_resource`.
- Store only `AnalysisResult` in session state. Never store raw HTML (memory).

### 8.2 Header (always visible)
- Command bar: `st.text_input` inside `st.form` (Enter submits).
- Seed buttons: NVDA / WMT / TSLA / BA.
- Company name, ticker, CIK, SIC.
- Source badge `LIVE` / `CACHE` / `SEED as of {fetched_at}`.
- "Refresh live" button: disabled in offline mode or without a User-Agent.
- Banners.

### 8.3 Command grammar (case-insensitive, whitespace-trimmed)
| Input | Action |
|---|---|
| `ANALYZE <T>` or bare `<T>` (matches `^[A-Z][A-Z0-9.\-/]{0,9}$`) | Run analysis |
| `CIK <digits>` | Run analysis by CIK |
| `DIFF` | Switch to Redline view |
| `CONTRA` | Switch to Overview with `severity_filter = CRITICAL` |
| `RATIOS`, `PROXY`, `OVERVIEW` | Switch view |
| `EXPORT` | Build PDF and reveal `st.download_button` |
| anything else | Inline error listing valid commands |

### 8.4 Views
Selected with `st.radio(horizontal=True, key="view")`, **not** `st.tabs`, because tabs cannot be switched programmatically by commands. Labels: `[1] Overview`, `[2] Redline`, `[3] Ratios`, `[4] Proxy`.

1. **Overview**
   - Score header: number or "INSUFFICIENT DATA", coverage, modules assessed, scope note.
   - Module status chips. Severity filter.
   - Finding cards in three columns: "What management said" (quote + citation link) | "What the numbers show" (formula, inputs, outputs, period) | severity badge plus annotations.
   - Sections: Contradictions → Unaddressed divergences → Claims consistent with numbers.
2. **Redline**
   - Filing-pair selector (10-K pair / 10-Q pair). Note selector, sorted with category notes first.
   - Counts: inserted / deleted / modified / softened / material.
   - Side-by-side prior | current, with `<ins>` green and `<del>` red strikethrough. Escape all filing text before inserting markup.
   - Hedging-drift table.
3. **Ratios**
   - Per check: a Plotly line chart of the last 12 quarters (metric + threshold band), plus a raw table with value, period, tag, accession link, and `derived`/`restated` flags.
   - Display-only metrics are shown below.
4. **Proxy**
   - Table: metric | plan context | weight or "not parsed" | source sentence + citation.
   - Flags. LOW_CONFIDENCE / unavailable empty states.

### 8.5 Footer
Disclaimer (§3.4) and "Methodology" expander (rendered from `docs/METHODOLOGY.md`).

---

## 9. PDF Tear-Sheet Spec (`src/veritas/export/pdf_tearsheet.py`, reportlab Platypus)
- US Letter portrait, 0.6" margins, light background (print-friendly), built-in Helvetica/Courier (no font files).
- Filename: `VERITAS_{TICKER}_{YYYYMMDD}.pdf`.
- **Page 1:**
  - header: company, ticker, CIK, data as-of, source;
  - filings used (form, filing date, accession);
  - score box with breakdown table;
  - top 5 findings, ordered by severity then points — title, quote (≤ 240 chars, "…"), metric line, citation (form + accession + section).
- **Page 2:**
  - check table (A1–A5 × latest 3 evaluation points);
  - top 3 material footnote edits (redline as text with [+inserted+] / [−deleted−]);
  - proxy metrics and flags;
  - known limitations (§15, abbreviated);
  - methodology note;
  - disclaimer.
- **Overflow:** hard caps per block with "+N more in app". **Exactly 2 pages, always.** A test asserts the page count is 2 for every seed ticker and for a synthetic worst case with 50 findings.
- Empty states print the same "Not assessed: {reason}" text as the UI.

---

## 10. Seed Cache & Test Fixtures

### 10.1 Seed cache
- Created **only** by `scripts/build_seed_cache.py --tickers NVDA WMT TSLA BA`. Requires live network and a User-Agent. Never hand-edit it.
- Contents per ticker:
  - submissions JSON;
  - companyfacts trimmed to the Appendix A tags (gzip);
  - extracted section texts and raw HTML only for the documents actually used (gzip);
  - `index.json` files used.
- `seed_cache/manifest.json`: `{ticker, cik, fetched_at, accessions[], files[{path, sha256}]}`.
- **Size budget:** ≤ 15 MB total, enforced by a test.
- Demo buttons always load the seed (no network). The badge shows "SEED as of {fetched_at}". "Refresh live" fetches current data.
- If the agent has no network: do **not** invent seed contents. Build synthetic fixtures (§10.2), leave seed generation as a documented manual step in the README, and report it.

### 10.2 Test fixtures (`tests/fixtures/`)
- **Synthetic companyfacts JSON**, hand-built, with documented expected values, covering:
  - YTD-only cash flow; Q4 derivation; duplicate facts across filings; restatement;
  - 52/53-week year; tag switch mid-series; zero/negative bases;
  - negative NI; NI near zero; acquisition guard; financial SIC 6022; shell SIC 6770.
- **Small HTML snippets** (≤ 300 KB each), covering:
  - TOC plus real Item 7 (longest-span);
  - 10-Q Part I vs Part II Item 2;
  - Exhibit-13 reference;
  - negation and forward-looking sentences;
  - renumbered-note pair (concept match);
  - glossary-only "Adjusted EBITDA" proxy (no flag);
  - iXBRL hidden header;
  - table rows.

---

## 11. Testing Strategy & Quality Gate
1. **Unit tests run with the network disabled:** an autouse fixture monkeypatches `socket.socket` to raise. Live tests are marked `@pytest.mark.live` and excluded by default.
2. **Required unit tests (minimum):**
   - growth with zero base;
   - negative NI and near-zero NI (A3);
   - Q4 derivation equals FY − 9M;
   - YTD-only cash-flow differencing;
   - dedup keeps latest `filed`;
   - fiscal year ending in January;
   - 53-week year duration class;
   - AR below the materiality floor → N/A;
   - NVDA seed is **not** CRITICAL on A1;
   - acquisition guard caps at WARNING;
   - escalation by a paired claim;
   - no escalation for acquisition-distorted checks;
   - SIC 6022 and SIC 6770 gating;
   - coverage < 60% → `score=None`;
   - score clamps at 0;
   - category caps;
   - A3 halving de-dup;
   - longest-span section location;
   - negation/forward-looking rejection;
   - note matching by concept despite renumbering;
   - normalization makes "$4.1 billion in 2024" ≡ "$4.3 billion in 2025";
   - modal softening detection;
   - hedging drift minimum-token rule;
   - glossary-only proxy metric not counted;
   - Citation invariant (§7.2.6) on every seed ticker;
   - determinism (two runs → identical JSON);
   - banned-words test (§0.1.8);
   - PDF has exactly 2 pages, contains the disclaimer, and contains each finding's accession.
3. **Seed-ticker smoke tests (offline):** every module for NVDA, WMT, TSLA, BA returns a non-`error` status; `streamlit.testing.v1.AppTest` renders each view for each seed without exceptions.
4. **Quality gate** (must pass at the end of every milestone):
   ```bash
   ruff check . && ruff format --check . && mypy src/veritas && pytest -q
   ```

---

## 12. Performance Targets (measured by `scripts/bench.py`, excluding browser render)
| Scenario | Target |
|---|---|
| Seed or warm-cache `analyze()` | ≤ 2 s |
| Cold live run (one ticker) | ≤ 60 s, with staged progress |
| Parse + preprocess one filing | ≤ 5 s |
| Diff one filing pair | ≤ 5 s |
| PDF build | ≤ 5 s |
| View switch | ≤ 1 s |

On a per-module timeout (30 s), mark the module `timeout` and continue. Cache derived artifacts so warm runs never re-parse HTML.

---

## 13. Repository Structure & Conventions
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

**Conventions:**
- Type hints everywhere (`mypy --strict` on `src/veritas/engine` and `models.py`).
- `logging`, never `print` (except in the CLI).
- No bare `except`.
- Pure functions in `engine/`: no I/O, no network. I/O only in `edgar/`, `export/`, and `ui/`.
- Bump `PARSER_VERSION` on any change to parsing or normalization.

---

## 14. Milestones (each ends with the quality gate green, `PROGRESS.md`, and a commit)

**M0 — Scaffold & contracts**
- Bootstrap (§4.1); `pyproject.toml` with pinned deps; `models.py`; `config.py`; `lexicons.py` stubs; network-blocking `conftest.py`; `DECISIONS.md`; `PROGRESS.md`; `.env.example`; CLI skeleton.
- *DoD:* `python -m veritas --help` works; the gate passes.

**M1 — Data layer + vertical slice (NVDA)**
- EDGAR client (rate limit, retry, circuit breaker); cache; resolve; filing selection; `xbrl_normalize`; tag fallbacks; gating; checks A1 and A3; scorer; seed builder.
- Run the seed builder for all 4 tickers (needs network). Verify Appendix D items.
- *DoD:*
  - `python -m veritas analyze NVDA --offline --json` produces a valid `AnalysisResult` from seed;
  - derived Q4 revenue = FY − 9M for NVDA and WMT;
  - FY revenue equals the value tagged in the 10-K;
  - all §7.0 / A1 / A3 / scorer unit tests pass.

**M2 — Remaining accounting checks + Ratios view**
- A2, A4, A5, acquisition guard, display-only metrics.
- Minimal Streamlit app: header, command bar, seed buttons, Ratios view.
- *DoD:* synthetic trigger tests pass (fire / no-fire / missing-data for every check); all 4 seeds analyze offline without exceptions; AppTest renders Ratios for all seeds.

**M3 — Narrative extraction + Overview view**
- `html_text`, `sections`, `narrative`, pairing, escalation.
- *DoD:* section-location, negation, and forward-looking tests pass; Citation invariant holds on all seeds; Overview renders.

**M4 — Footnote diff + Proxy + Redline / Proxy views**
- *DoD:* normalization, alignment, softening, material-edit, hedging, and proxy-context tests pass; every module is non-`error` on all seeds.

**M5 — PDF, polish, docs**
- PDF tear-sheet; all error/empty states (§2.3); README; `METHODOLOGY.md`; `bench.py`.
- *DoD:* 2-page PDF test passes for all seeds and the worst case; §12 targets met (record results in `PROGRESS.md`); banned-words and determinism tests pass.

---

## 15. Known Limitations (shown in the Methodology expander and abbreviated on PDF page 2)
- Heuristic text matching. It misses claims phrased unusually and can mis-pair sentences.
- Not modelled:
  - FX effects;
  - LIFO;
  - receivables factoring/securitization (which artificially lowers DSO);
  - segment mix;
  - dimensional XBRL data;
  - company-specific extension tags.
- 53-week fiscal years make one quarter about 7% longer. DSO and DIO adjust for this via `days(q)`; growth-based metrics do not.
- Restatements use as-restated values. Original values are flagged, not used.
- Proxy metrics that appear only in images or charts are not detected (`LOW_CONFIDENCE`).
- Financial issuers, foreign private issuers, funds, and shells are out of scope or limited.
- The score is uncalibrated and not comparable across sectors.

---

## Appendix A — XBRL Tag Fallbacks (`engine/tags.py`; us-gaap unless noted; first hit per period wins)
| Metric | Ordered tags |
|---|---|
| Revenue | `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, `RevenueFromContractWithCustomerIncludingAssessedTax`, `SalesRevenueNet`, `SalesRevenueGoodsNet` |
| COGS | `CostOfGoodsAndServicesSold`, `CostOfRevenue`, `CostOfGoodsSold` |
| AR | `AccountsReceivableNetCurrent`, `ReceivablesNetCurrent` |
| Inventory | `InventoryNet` |
| AP | `AccountsPayableCurrent`, `AccountsPayableTradeCurrent` |
| OCF | `NetCashProvidedByUsedInOperatingActivities`, `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` |
| Net income (for accruals; consistent with OCF incl. NCI) | `ProfitLoss`, `NetIncomeLoss` |
| CapEx | `PaymentsToAcquirePropertyPlantAndEquipment`, `PaymentsToAcquireProductiveAssets` |
| D&A | `DepreciationDepletionAndAmortization`, `DepreciationAndAmortization`, `Depreciation` |
| SBC | `ShareBasedCompensation`, `AllocatedShareBasedCompensationExpense` |
| R&D | `ResearchAndDevelopmentExpense` |
| Capitalized software | `PaymentsToDevelopSoftware`, `CapitalizedComputerSoftwareAdditions` |
| Goodwill | `Goodwill` |
| Intangibles | `IntangibleAssetsNetExcludingGoodwill`, `FiniteLivedIntangibleAssetsNet` |
| Assets | `Assets` |
| Equity | `StockholdersEquity`, `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Cash | `CashAndCashEquivalentsAtCarryingValue` |
| Operating income | `OperatingIncomeLoss` |
| Pre-tax income | `IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest`, `IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments` |
| Tax | `IncomeTaxExpenseBenefit` |
| Acquisitions | `PaymentsToAcquireBusinessesNetOfCashAcquired` |
| Restructuring | `RestructuringCharges`, `RestructuringCostsAndAssetImpairmentCharges`, `RestructuringAndRelatedCostIncurredCost` |
| Impairments (display) | `GoodwillImpairmentLoss`, `AssetImpairmentCharges`, `ImpairmentOfLongLivedAssetsHeldForUse` |
| Total debt (computed, labelled "approximate") | (`LongTermDebt`, or `LongTermDebtNoncurrent` + `LongTermDebtCurrent`) + `ShortTermBorrowings` + `CommercialPaper`. Missing components count as 0; if **all** are missing → `None`. |

## Appendix B — Lexicons (`lexicons.py`; compile with `re.I`; use `[\s-]+` between words in multi-word phrases)
- **DEMAND:** `\b(strong|robust|healthy|solid|record|accelerat\w+)[\s-]+(\w+[\s-]+){0,3}(demand|growth|orders|bookings|backlog|pipeline)\b|market[\s-]+share[\s-]+gains?`
- **MARGIN:** `pricing[\s-]+power|margin[\s-]+expansion|disciplined[\s-]+cost|cost[\s-]+discipline|operating[\s-]+leverage|operational[\s-]+efficienc\w+`
- **ONETIME:** `transitory|one[\s-]time|non[\s-]?recurring|isolated|unusual[\s-]+(item|charge)s?`
- **NEGATION** (within 6 tokens before the match): `no|not|n't|never|without|lack\w*|absence|weak\w*|soft\w*|declin\w+|lower|decreas\w+`
- **FORWARD:** `\b(expect\w*|anticipat\w*|believe\w*|intend\w*|plan\w*|forecast\w*|outlook|guidance|may|could|should|would|will|if)\b`
- **HEDGE:** `\b(may|might|could|possibly|potentially|uncertain\w*|believes?)\b|subject[\s-]+to|contingent[\s-]+(on|upon)|no[\s-]+assurance|cannot[\s-]+(predict|assure|estimate)|reasonably[\s-]+possible|substantial[\s-]+doubt`
- **Proxy aliases:**
  - ADJ_EBITDA `adjusted[\s-]+ebitda`
  - NONGAAP_EPS `(adjusted|non[\s-]?gaap)[\s-]+(diluted[\s-]+)?(eps|earnings[\s-]+per[\s-]+share)`
  - REVENUE `(net[\s-]+)?(revenue|sales)([\s-]+growth)?`
  - TSR `total[\s-]+shareholder[\s-]+return|\bTSR\b`
  - FCF `free[\s-]+cash[\s-]+flow`
  - ROIC `return[\s-]+on[\s-]+invested[\s-]+capital|\bROIC\b|\bROCE\b`
  - M_AND_A `acquisitions?[\s-]+completed|acquired[\s-]+revenue|deal[\s-]+volume`

## Appendix C — `DECISIONS.md` entry template
```markdown
### D-<nnn> <short title>  (<YYYY-MM-DD>, milestone M<k>)
- **Question:** what was ambiguous or unverified
- **Options considered:** …
- **Choice:** …
- **Rationale / evidence:** (e.g., "verified in WMT companyfacts: tag X present 2019–2025")
- **Revisit if:** …
```

## Appendix D — Facts the agent must verify against live data (record each in DECISIONS.md)
1. Every Appendix A tag exists in the us-gaap taxonomy and appears in at least one seed company's facts. Drop or replace any that do not, with evidence.
2. The `filings.recent` column names used in §6.4 (`form`, `accessionNumber`, `filingDate`, `reportDate`, `primaryDocument`, `items`).
3. `index.json` item fields and how exhibit types are exposed. If `type` is absent, fall back to the filename regex.
4. iXBRL `TextBlock` concept presence in the seed 10-Ks. If absent, the header fallback (§7.3.2) must succeed.
5. NVDA, WMT, TSLA, BA fiscal year ends and that the Q4 derivation reconciles to reported FY totals.
6. SEC's response to a missing or placeholder User-Agent (expected: 403). Confirm it is handled by the circuit breaker, not retried.
