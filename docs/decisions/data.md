# Data-layer decisions (EDGAR client, cache, seed)

Verified live on 2026-11-24 against www.sec.gov / data.sec.gov (PRD Appendix D). Format follows PRD Appendix C.

### D-101 index.json does not expose document types  (2026-11-24, data layer)
- **Question:** Appendix D item 3: which `index.json` item fields exist, and is the exhibit type (EX-99.1, EX-13) among them?
- **Evidence:** `directory.item[]` entries have exactly `{last-modified, name, type, size}`. `type` is an *icon name* (`text.gif`, `image2.gif`, `compressed.gif`), never a document type. Real press-release exhibits have names such as `q2fy27pr.htm` (NVDA), `earningsreleasefy27q2.htm` (WMT), `exhibit991.htm` (TSLA), `a202609sep308kprex991.htm` (BA), so the PRD filename regex `/ex-?99[._-]?1/i` would match only TSLA and BA.
- **Options considered:** (a) filename regex only; (b) the filing's `-index.htm` HTML table; (c) `{acc}-index-headers.html` (SGML header rendered as HTML, ~6 KB, lists `<TYPE>`, `<SEQUENCE>`, `<FILENAME>` for every document).
- **Choice:** (c). `LiveSource.filing_index()` returns the `index.json` dict plus an extra key `_veritas_documents: [{seq, type, name, description}]` parsed from `index-headers.html` (one extra request per filing index, cached forever). `find_exhibit()` / `find_earnings_exhibit()` use the real types first (EX-99.1, else first EX-99*, by sequence), and fall back to the PRD filename regex over `index.json` names if the headers page is unavailable. Both URLs are on `www.sec.gov/Archives/...`; no full-submission `.txt` is downloaded.
- **Revisit if:** SEC starts exposing real types in `index.json`.

### D-102 filings.recent columns and older pages  (2026-11-24, data layer)
- **Evidence (Appendix D item 2):** `filings.recent` for NVDA, WMT, TSLA, BA contains `accessionNumber, filingDate, reportDate, acceptanceDateTime, act, form, fileNumber, filmNumber, items, core_type, size, isXBRL, isInlineXBRL, isXBRLNumeric, primaryDocument, primaryDocDescription`. All six names the PRD uses (`form, accessionNumber, filingDate, reportDate, primaryDocument, items`) exist. `items` is a comma-separated string (`"2.02,9.01"`) and `reportDate` is `""` when absent. `filings.files[]` = `{name, filingCount, filingFrom, filingTo}`. NVDA `recent` held 1005 filings back to 2020-09 (so no older page was needed); older pages are fetched only when `filingTo >= today - (FILER_LOOKBACK_YEARS+1) years`.
- **Choice:** `submissions()` returns the main payload with older-page columns appended to `filings.recent` (`merge_submission_pages`). `select_filings` raises `KeyError` if a required column is missing.
- **Gotcha:** DEF 14A `reportDate` is the *meeting date* (NVDA: 2026-06-24 for a filing made 2026-05-12), not a period end. Never use it as a period.

### D-103 Seed cache layout, manifest and size  (2026-11-24, data layer)
- **Choice:** `seed_cache/manifest.json = {version, built_at, strip_ix_header, entries:[{ticker, cik, title, fetched_at, accessions[], roles{}, sic, fiscal_year_end, notes[], files[{path, sha256, bytes}], bytes}]}` (one entry per ticker; the PRD's single-ticker shape is wrapped in `entries`). Per ticker: `submissions.json.gz` (merged), `companyfacts.json.gz` (us-gaap trimmed to `engine.tags.all_tags()` plus the whole `dei` block, which only has `EntityCommonStockSharesOutstanding` and `EntityPublicFloat`), `docs/<acc_nodash>/<name>.gz` (decoded UTF-8 text of each document, gzip level 9, mtime 0), `index/<acc_nodash>.json.gz`. Top level `company_tickers.json` is the 4-entry subset. `fetched_at` is the build time of each ticker.
- **Documents stored per ticker (7):** latest 10-K, prior 10-K, 10-Q pair (latest + same quarter a year earlier), DEF 14A, EX-99.1 for the latest 10-K period, EX-99.1 for the latest 10-Q period. No EX-13 exists in any seed ticker's two 10-Ks (checked via index-headers types), so none is stored.
- **Size (measured):** NVDA 0.755 MB, WMT 1.017 MB, TSLA 0.822 MB, BA 1.031 MB, total directory 3,638,940 bytes (3.64 MB) against the 15 MB budget (`SEED_MAX_BYTES`). Because it is far under budget nothing was stripped: `--strip-ix-header` exists in the build script but was **not** used (`strip_ix_header: false`), so every byte of every document is kept.
- **Selected accessions** are listed in `manifest.json` `roles` (see also the table in the final report).
- **Revisit if:** more seed tickers push the total above 15 MB; then use `--strip-ix-header` (documented, never silent) before dropping anything else.

### D-104 Appendix A tag existence (Appendix D item 1)  (2026-11-24, data layer)
- **Evidence:** number of fact rows per seed company in the trimmed `companyfacts` (NVDA / WMT / TSLA / BA; `-` = tag absent):
  - Revenue: `RevenueFromContractWithCustomerExcludingAssessedTax` 28/120/49/3; `Revenues` 280/293/286/317; `RevenueFromContractWithCustomerIncludingAssessedTax` none; `SalesRevenueNet` -/189/-/-; `SalesRevenueGoodsNet` -/-/98/121.
  - COGS: `CostOfGoodsAndServicesSold` 25/-/-/-; `CostOfRevenue` 280/309/206/317; `CostOfGoodsSold` -/-/98/121.
  - AR: `AccountsReceivableNetCurrent` 138/-/126/140; `ReceivablesNetCurrent` -/193/-/-. Inventory: `InventoryNet` 138/193/126/- (BA reports `InventoryNetOfAllowancesCustomerAdvancesAndProgressBillings`, not in Appendix A; BA inventory is therefore NOT_COMPUTABLE under the current tags). AP: `AccountsPayableCurrent` 138/193/126/138; `AccountsPayableTradeCurrent` none.
  - OCF: `NetCashProvidedByUsedInOperatingActivities` 161/157/106/157; `...ContinuingOperations` -/-/36/-. NetIncome: `ProfitLoss` -/342/130/187; `NetIncomeLoss` 314/319/294/317.
  - CapEx: `PaymentsToAcquirePropertyPlantAndEquipment` 55/157/142/157; `PaymentsToAcquireProductiveAssets` 35/-/-/-. D&A: `DepreciationDepletionAndAmortization` 45/21/91/146; `DepreciationAndAmortization` 150/96/66/9; `Depreciation` 48/3/104/25.
  - SBC: `ShareBasedCompensation` 193/-/142/157; `AllocatedShareBasedCompensationExpense` 91/42/195/39 (WMT has only the Allocated tag). R&D: `ResearchAndDevelopmentExpense` 225/-/206/229.
  - CapSW: `PaymentsToDevelopSoftware` none; `CapitalizedComputerSoftwareAdditions` none.
  - Goodwill 138/209/68/160; Intangibles: `IntangibleAssetsNetExcludingGoodwill` 138/-/74/140, `FiniteLivedIntangibleAssetsNet` 92/-/46/-; Assets 138/207/126/140; Equity: `StockholdersEquity` 248/193/140/140, `...IncludingPortionAttributableToNoncontrollingInterest` -/294/165/309; Cash 219/229/246/245; OperatingIncomeLoss 225/229/206/317.
  - Pre-tax: `...ExtraordinaryItemsNoncontrollingInterest` 65/65/206/205; `...MinorityInterestAndIncomeLossFromEquityMethodInvestments` 147/164/-/26. Tax 231/229/209/232. Acquisitions 97/101/61/98.
  - Restructuring: `RestructuringCharges` 43/4/2/-; `RestructuringCostsAndAssetImpairmentCharges` none; `RestructuringAndRelatedCostIncurredCost` none. Impairments: `GoodwillImpairmentLoss` 26/-/21/-; `AssetImpairmentCharges` -/10/-/153; `ImpairmentOfLongLivedAssetsHeldForUse` none.
  - Debt: `LongTermDebt` 98/52/82/52; `LongTermDebtNoncurrent` 48/193/28/-; `LongTermDebtCurrent` 40/193/15/-; `ShortTermBorrowings` -/187/-/-; `CommercialPaper` 22/6/-/12.
- **Seven tags appear in no seed company:** `RevenueFromContractWithCustomerIncludingAssessedTax`, `AccountsPayableTradeCurrent`, `PaymentsToDevelopSoftware`, `CapitalizedComputerSoftwareAdditions`, `RestructuringCostsAndAssetImpairmentCharges`, `RestructuringAndRelatedCostIncurredCost`, `ImpairmentOfLongLivedAssetsHeldForUse`. To show they are real us-gaap elements I checked untrimmed companyfacts of 15 other filers (AAPL MSFT ORCL CSCO F GM PFE IBM ADBE CRM KO XOM GE HON INTC): present in GM/CRM/GE (`...IncludingAssessedTax`), KO/XOM/GE (`AccountsPayableTradeCurrent`), AAPL/CRM (`CapitalizedComputerSoftwareAdditions`), CSCO/F/PFE/ADBE/KO/GE/HON/INTC (`RestructuringAndRelatedCostIncurredCost`), F/PFE/IBM/CRM/XOM/GE/INTC (`ImpairmentOfLongLivedAssetsHeldForUse`). `PaymentsToDevelopSoftware` and `RestructuringCostsAndAssetImpairmentCharges` were in none of those 15, so I queried the frames API (`data.sec.gov/api/xbrl/frames/us-gaap/<tag>/USD/CY2023|CY2024.json`): 200 with 246/235 and 169/159 reporting entities respectively; a nonexistent control tag returned 404.
- **Choice:** keep all Appendix A tags (all exist). No replacements.
- **Engine hints (not blockers):** NVDA tags `Revenues` for almost every period but `RevenueFromContractWithCustomerExcludingAssessedTax` for 28 rows (mid-series tag switch); BA has only 3 rows of the contract-revenue tag. Companyfacts rows can carry `form = "DEF 14A"` (pay-versus-performance data in proxies, e.g. NVDA `NetIncomeLoss` FY rows with `filed=2026-05-12`); the normalizer must filter to `form in {10-K, 10-Q}` (and 10-K405/10-KT as needed) before choosing "latest filed" per period.

### D-105 iXBRL TextBlock presence (Appendix D item 4)  (2026-11-24, data layer)
- **Evidence:** count of `<ix:nonNumeric name="...TextBlock">` start tags (names ending `TextBlock`, any prefix) in each seed 10-K:

| Ticker | latest 10-K | prior 10-K |
|---|---|---|
| NVDA | 76 (of 178 nonNumeric) | 78 (of 188) |
| WMT | 81 (of 197) | 82 (of 199) |
| TSLA | 92 (of 256) | 91 (of 237) |
| BA | 123 (of 259) | 121 (of 256) |

  Counts include the `cyd:` cybersecurity TextBlocks (Item 1C) and company-extension blocks (e.g. `ba:AircraftValuationPolicyTextBlock`); 18-22 per filing are `*Policy*TextBlock`. Some TextBlocks repeat (WMT/TSLA have duplicates by name), so de-duplicate by (name, contextRef) when segmenting.
- **Choice:** TextBlocks are present in all four seed 10-Ks, so the primary segmentation can use them; the §7.3.2 header fallback is still required for filers/years without them.

### D-106 Fiscal year ends and Q4 derivation (Appendix D item 5)  (2026-11-24, data layer)
- **Evidence:** submissions `fiscalYearEnd`: NVDA `0131`, WMT `0131`, TSLA `1231`, BA `1231`. NVDA's actual year ends on the last Sunday of January (FY ending 2025-01-26 and 2026-01-25, both 364 days; FY2021 ended 2021-01-31 and lasted 371 days, a 53-week year that still falls inside DUR_FY 350-380). WMT ends 01-31 exactly.
- **Q4 reconciliation (companyfacts, latest two fiscal years per ticker, `form in {10-K,10-Q}`):** for `Revenues`/contract revenue and `NetIncomeLoss`, the three 3-month discrete 10-Q values sum exactly to the reported 9-month YTD value in every case (difference 0), so `Q4 = FY - 9M YTD` reconciles by construction. Examples: NVDA revenue FY2026 215,938 M = 9M 147,811 M + Q4 68,127 M; BA NI FY2025 2,235 M = 9M -5,985 M + Q4 8,220 M; TSLA NI FY2025 3,794 M = 9M 2,954 M + Q4 840 M; WMT NI FY2026 21,893 M = 9M 17,656 M + Q4 4,237 M. Operating cash flow is reported YTD only; Q4 derived the same way (NVDA FY2026: 102,718 M - 66,530 M = 36,188 M).
- **Choice:** the PRD's Q4 derivation is valid for all four seeds. `FilingSelection.fiscal_year_end` is the raw `MMDD` string.

### D-107 Missing / placeholder User-Agent response (Appendix D item 6)  (2026-11-24, data layer)
- **Evidence:** exactly one request to `https://www.sec.gov/files/company_tickers.json` with `User-Agent: ""` (empty) and `Accept-Encoding: gzip, deflate` returned **HTTP 403** (`text/html`, 1,924 bytes). A placeholder-looking string is rejected before any request is made, by `config.validate_user_agent`.
- **Choice:** 403 is handled by the process-global 10-minute circuit breaker (`client.CircuitBreaker`): the 403 is raised as `SecBlocked`, never retried, and every later live request is refused locally until the breaker window ends (unit tested). `LiveSource` then serves cached data with the banner "SEC access temporarily blocked; showing cached data", or re-raises if nothing is cached.

### D-108 Cache details beyond §6.5  (2026-11-24, data layer)
- `http_cache` and `derived` carry an extra `last_access` column so LRU eviction (above `CACHE_MAX_BYTES`, deleting the least-recently-used rows across both tables) is exact. Bodies are gzip BLOBs; every call opens a new connection (WAL, `busy_timeout=5000`).
- TTLs: documents and `index.json`/`index-headers` never expire; tickers 24 h; submissions (and older pages) 6 h; companyfacts 24 h *or* earlier when the cached submissions show a 10-K/10-Q accepted after the cached facts were fetched. A 404 companyfacts response is cached for the facts TTL. Derived artifacts are not written by this layer (engines key them by `(accession, PARSER_VERSION)` through `RuntimeCache.get_derived/put_derived`).
- Per-CIK in-process locks (`LiveSource._lock_for`) prevent duplicate concurrent fetches. `LiveSource.source` is `"cache"` until the first network fetch, then `"live"`; `fetched_at` is the *oldest* timestamp among the data actually served, so staleness is never understated.
- Documents are decoded UTF-8 first, then cp1252 (legacy filings); decoded text is what the seed stores.

### D-109 Filing-selection details  (2026-11-24, data layer)
- Reference date for "last 3 years" and "within 18 months" is `as_of` if given, else `ds.fetched_at` (so the seed gives the same answer forever), else today.
- Earnings press releases are looked up only for the latest 10-K and the latest 10-Q (the documents the narrative module pairs against), newest in-window 2.02 8-K first, at most 3 candidates; every missing item adds an explicit entry to `FilingSelection.notes` and nothing is guessed.
- A latest/prior 10-K whose primary document is not `.htm/.html` becomes `None` plus a "document unavailable" note; it does not fall through to an older filing (the "prior 10-K" is always the next-older 10-K by form).
- `CIK n` input that is absent from the ticker file resolves through `submissions.tickers[0]`; if there is no ticker it raises `TickerNotFound` rather than inventing a label.
