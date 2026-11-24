# Product Requirements Document (PRD)
## Project Name: VERITAS — Forensic Narrative-vs-Numbers Terminal
**Version:** 1.0
**Target Domain:** Investment Banking, Equity Research, Private Equity Due Diligence, Management Consulting
**Core Philosophy:** Zero-cost public APIs (SEC EDGAR), deterministic forensic accounting math, institutional dark-mode terminal UX, zero fragile dependencies.

---

## 1. Executive Summary & Problem Statement
### 1.1 The Everyday Industry Bottleneck
Every day across Wall Street and strategy consulting, analysts spend hours reading glossy MD&A narratives, earnings releases, and 150-page SEC filings—frequently missing subtle financial deterioration buried in footnotes or distorted by non-GAAP adjustments. Existing market terminals provide raw filings and standardized financials, but they do not automatically **cross-examine management's narrative claims against underlying forensic accounting reality** or link executive pay incentives to accounting red flags.

### 1.2 The Solution
**VERITAS** is a keyboard-driven, institutional dark-mode forensic terminal that ingests live public filings directly from the **SEC EDGAR API** (free, no API key required) for any US public ticker and executes four automated cross-examinations:
1. **Narrative-vs-Numbers Contradiction Radar:** Pairs management's optimistic MD&A language against hard forensic accounting divergences (e.g., claiming "strong demand" while Accounts Receivable grows 3x faster than Revenue).
2. **Stealth Footnote Redline Diff:** Strips boilerplate HTML from consecutive 10-K/10-Q filings to isolate materially altered sentences in Revenue Recognition, Debt Covenants, Inventory Reserves, and Legal Contingencies.
3. **Executive Incentive Misalignment X-Ray:** Parses the latest DEF 14A Proxy Statement to expose what executive bonuses are tied to (e.g., Adjusted EBITDA) versus actual GAAP Free Cash Flow generation.
4. **Forensic Integrity Scorecard & Tear-Sheet Export:** Generates a 0–100 Narrative Integrity Score and exports an executive-ready **2-Page Forensic Due Diligence Memo (`.pdf` / `.docx`)**.

---

## 2. Target Users & Core User Flows
### 2.1 Primary Personas
* **Equity Research / IB / PE Analyst:** Screening a public company before an earnings call, pitchbook, or take-private diligence memo.
* **Management Consultant (Strategy / Turnaround):** Running a rapid outside-in diagnostic on a client or competitor's operational health and management credibility.

### 2.2 Core User Flows
1. **Instant Ticker Lookup (Zero Friction):** User types any US ticker (e.g., `NVDA`, `WMT`, `TSLA`, `BA`, `NKE`) into the top command bar (`> ANALYZE [TICKER]`) or clicks one of 4 pre-cached flagship demo tickers (for instantaneous zero-latency loading during interviews).
2. **Automated Multi-Filing Ingestion:** System resolves Ticker → CIK via SEC EDGAR `company_tickers.json`, fetches the latest `10-K`, last 4 `10-Q`s, latest `8-K` (earnings release), and latest `DEF 14A` (proxy).
3. **Interactive Terminal Exploration:** User navigates via keyboard shortcuts (`1` Overview & Contradictions, `2` Footnote Redline Diff, `3` Forensic Ratios & Earnings Quality, `4` Proxy Incentive X-Ray) or mouse clicks.
4. **One-Click IC / Client Memo Export:** User clicks `EXPORT TEAR-SHEET` to download a cleanly formatted 2-page PDF forensic report containing exact filing citations.

---

## 3. Data Architecture & SEC EDGAR Integration (100% Free)
### 3.1 SEC EDGAR API Rules & Compliance
* **Authentication:** Requires zero API keys. Must pass a compliant custom HTTP header on every request:
  `User-Agent: VeritasForensicTerminal/1.0 (contact@yourdomain.com)`
* **Rate Limiting:** Enforce a strict token-bucket rate limiter (`<= 8 requests/second`, staying safely under SEC's 10 req/sec threshold) plus local SQLite/disk caching (`cache/` directory) so repeated lookups of the same ticker load in `< 0.2 seconds`.

### 3.2 Endpoints Used
1. **Ticker-to-CIK Mapping:** `https://sec.gov`
2. **Company Submissions History:** `https://sec.gov{cik_10_digits}.json`
3. **Structured XBRL Financial Facts (Deterministic Accounting Data):**
   `https://sec.gov{cik_10_digits}.json`
   *(Extracts exact historical quarterly/annual time series for Revenue, Accounts Receivable, Inventory, Accounts Payable, Operating Cash Flow, CapEx, R&D Expense, Goodwill, Intangibles, Total Debt, SBC, and Net Income without fragile HTML table scraping).*
4. **Raw Filing Documents (For Narrative & Footnote Diffing):**
   `https://sec.gov{cik_no_zeros}/{accession_no_no_dashes}/{primary_document}`

---

## 4. Core Analytical & Forensic Engine Specifications

### 4.1 Module A: Narrative-vs-Numbers Contradiction Radar
* **Step 1 — Narrative Extraction:** Parse Item 7 (MD&A in 10-K) or Item 2 (MD&A in 10-Q) and recent 8-K earnings releases. Extract sentences containing high-signal claim clusters:
  * *Demand/Growth Claims:* `"strong demand"`, `"accelerating growth"`, `"robust pipeline"`, `"market share gains"`
  * *Pricing/Margin Claims:* `"pricing power"`, `"disciplined cost"`, `"margin expansion"`, `"operational efficiency"`
  * *One-Time/Temporary Claims:* `"transitory"`, `"one-time"`, `"non-recurring"`, `"isolated"`
* **Step 2 — Deterministic Forensic Cross-Checks (Computed from XBRL `companyfacts`):**
  1. **Channel Stuffing / Receivables Bloat Flag:**
     AR Divergence = YoY AR Growth % − YoY Revenue Growth %
     *Trigger:* If `AR Divergence > +10%` while narrative claims *"strong demand"*.
  2. **Inventory Pile-Up / Obsolescence Flag:**
     Inventory Divergence = YoY Inventory Growth % − YoY COGS Growth %
     *Trigger:* If `Inventory Divergence > +12%` while narrative claims *"healthy demand"* or *"margin expansion"*.
  3. **Cash Conversion / Accrual Quality Divergence (Sloan-style check):**
     Cash Conversion Ratio = Operating Cash Flow / Net Income
     *Trigger:* If `Net Income` grows YoY while `Operating Cash Flow` declines or `Cash Conversion Ratio < 0.75`.
  4. **Capitalization & CapEx Bloat Flag:**
     *Trigger:* Sudden spike (`> +25% YoY`) in Capitalized Software / Intangibles or CapEx-to-Depreciation ratio while claiming *"operating leverage"* or *"cost discipline"*.
  5. **Serial "One-Time" Restructuring Flag:**
     *Trigger:* Restructuring or impairment tags appear in `>= 3` of the last `4` quarters while MD&A calls charges *"non-recurring"*.
* **Output:** Side-by-side **"What Management Said"** (verbatim quote + filing section citation) vs. **"What the Numbers Show"** (exact metric deltas + severity badge: `CRITICAL`, `WARNING`, `VERIFIED`).

### 4.2 Module B: Stealth Footnote Redline Diff Engine
* **Section Segmentation:** Split current and prior filings into logical sections using regex/DOM header detection (`Note 1: Basis of Presentation`, `Revenue Recognition`, `Debt & Credit Facilities`, `Commitments & Contingencies`, `Goodwill & Intangibles`, `Related Party Transactions`).
* **Boilerplate & Number-Noise Filtering:**
  * Normalize dates and pure period-over-period dollar updates so routine number changes (e.g., changing "$4.1B in 2024" to "$4.3B in 2025") do not drown out **structural policy edits**.
  * Use `difflib.SequenceMatcher` + TF-IDF sentence similarity to align paragraphs and surface **newly inserted clauses**, **deleted commitments**, and **softened modal verbs** (e.g., changing *"will maintain"* to *"intends to seek"* or *"may not be able to"*).
* **Hedging & Obfuscation Drift Score:** Quantify YoY change in uncertainty/hedging words (`approximately`, `certain`, `primarily`, `subject to`, `contingent`) per 1,000 words in critical footnotes.

### 4.3 Module C: Executive Incentive Misalignment X-Ray (`DEF 14A`)
* Parse the Compensation Discussion & Analysis (CD&A) section of the latest `DEF 14A` proxy filing.
* Extract key incentive metrics (e.g., `Adjusted EBITDA`, `Non-GAAP EPS`, `Revenue Growth`, `TSR`, `Free Cash Flow`) and flag **Governance Red Flags**:
  * Bonus tied to **Adjusted EBITDA / Non-GAAP EPS** while Stock-Based Compensation (SBC) exceeds `15%` of Revenue.
  * Zero weighting on **Return on Invested Capital (ROIC)** or **GAAP Free Cash Flow**.
  * Heavy M&A volume incentives alongside rising Goodwill-to-Assets (`> 35%`).

### 4.4 Module D: Narrative Integrity Score (0–100)
* Weighted composite score starting at `100` with deterministic deductions:
  * `-15 pts` per Critical Contradiction (AR/Inventory divergence, Cash Conversion breakdown).
  * `-10 pts` per Material Stealth Footnote Policy Edit.
  * `-10 pts` per Proxy Incentive Misalignment Flag.
  * `-5 pts` for High Hedging Drift (`> +15%` increase in vague qualifiers).

---

## 5. UI/UX & Institutional Terminal Design
* **Theme:** High-contrast institutional dark mode (`#0B0E14` obsidian background, `#131822` card panels, `#00E676` verified emerald, `#FF3B30` forensic crimson, `#FFB300` warning amber, monospace tabular numbers `JetBrains Mono` / `Fira Code`).
* **Command Bar:** Persistent top terminal bar accepting keyboard commands (`/` to focus):
  * `ANALYZE AAPL` — Run full forensic scan
  * `DIFF FOOTNOTES` — Jump to redline view
  * `CONTRA` — Filter only Critical Contradictions
  * `EXPORT` — Generate 2-page PDF Forensic Tear-Sheet
* **Pre-Loaded Cache Mode:** Include 4 pre-fetched, cached tickers (`NVDA`, `WMT`, `TSLA`, `BA`) bundled in the repo so anyone opening the deployed app gets instant `<0.2s` demo performance even if SEC EDGAR is momentarily slow.

---

## 6. Recommended Tech Stack & Repository Structure
* **Language:** Python 3.11+
* **Frontend:** `Streamlit` (with custom CSS for institutional dark-mode terminal styling, monospace typography, and clean multi-column grids) + `Plotly` (for interactive XBRL divergence charts).
* **Parsing & NLP:** `requests`, `beautifulsoup4`, `lxml`, `difflib`, `scikit-learn` (TF-IDF paragraph alignment).
* **Storage & Export:** `sqlite3` (local filing cache), `reportlab` or `fpdf2` (for crisp 2-page PDF Forensic Tear-Sheet generation).

```text
veritas-forensic-terminal/
├── app.py                     # Main dark-mode terminal UI & command bar router
├── config.py                  # SEC User-Agent, rate-limit settings, forensic thresholds
├── data/
│   ├── edgar_client.py        # Rate-limited SEC EDGAR submissions, XBRL & HTML fetcher
│   ├── cache_manager.py       # SQLite + JSON local cache for instant repeat/demo loads
│   └── seed_cache/            # Pre-cached filings for 4 flagship demo tickers
├── engine/
│   ├── xbrl_forensics.py      # Deterministic accounting ratios & divergence detectors
│   ├── narrative_extractor.py # MD&A & 8-K claim extraction + contradiction matcher
│   ├── footnote_diff.py       # Section splitter, boilerplate filter & redline engine
│   ├── proxy_xray.py          # DEF 14A CD&A incentive extractor & misalignment scorer
│   └── integrity_scorer.py    # Composite 0-100 Narrative Integrity Score calculator
├── export/
│   └── pdf_tearsheet.py       # 2-page institutional Forensic Due Diligence PDF builder
└── tests/
    └── test_forensics.py      # Unit tests for XBRL math, diff alignment & edge cases
```

## 7. Edge Cases & Quality Guardrails
1. **Financial Institutions (Banks/Insurance):** Detect SIC codes `6000–6799` (where Inventory/COGS do not apply) and automatically switch forensic checks to bank-relevant metrics (Loan Loss Provision vs. Non-Performing Loans, Net Interest Margin vs. Deposit Beta).
2. **Missing XBRL Tags:** Companies use varying GAAP taxonomy tags (e.g., `Revenues` vs. `RevenueFromContractWithCustomerExcludingAssessedTax`). Implement a prioritized fallback tag dictionary for every core financial line item.
3. **Large HTML Filings:** Cap regex/DOM parsing cleanly on files `> 15MB` by extracting targeted Item sections first to keep UI latency under `3 seconds`.

## 8. Phased Build Milestones (For Claude Code Execution)
* **Milestone 1 (Foundation & Data):** Implement `edgar_client.py`, `cache_manager.py`, and `xbrl_forensics.py`. Verify XBRL extraction and divergence ratios on `AAPL`, `WMT`, and `TSLA`.
* **Milestone 2 (Text & Diff Engines):** Implement `narrative_extractor.py`, `footnote_diff.py`, and `proxy_xray.py` with clean HTML stripping and redline highlighting.
* **Milestone 3 (Terminal UI & Visuals):** Build `app.py` with dark-mode terminal CSS, command bar parser, interactive Plotly divergence charts, and side-by-side redline viewer.
* **Milestone 4 (PDF Export & Demo Polish):** Implement `pdf_tearsheet.py`, populate `seed_cache/` with 4 flagship companies, write unit tests, and prepare `README.md` with methodology documentation.
