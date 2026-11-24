## What the score is and is not

The **Narrative–Numbers Consistency Score** is a capped, coverage-aware screening score from 0 to 100. It summarizes how many automated checks raised flags on public SEC data, and how strongly, for one filer.

- **It is a screen.** It points to items that warrant further review. It is not an audit opinion, not an investment recommendation, and not a finding about any filing.
- **It is uncalibrated.** The point values and thresholds are fixed heuristics. They have not been fitted to outcomes.
- **It is not comparable across sectors.** Different industries use the same checks with different normal ranges. A score of 72 for one company is not directly comparable with a score of 72 for another.
- **Absence of a flag is not a clean bill of health.** A check that does not fire may simply have no usable data, or may not cover the issue at all. The coverage figure shows how many checks actually ran.

Severity labels are `CRITICAL`, `WARNING`, `NO FLAG`, and `N/A`. Flags use neutral, descriptive names. A flag is a prompt to read the cited source, not a conclusion.

## Data sources

- **SEC EDGAR only.** VERITAS reads public filings and structured data from two hosts: `https://data.sec.gov` (submissions and XBRL company facts) and `https://www.sec.gov` (ticker file, filing documents, and filing indexes).
- **No other sources.** No market or price data, no third-party financial database, and no language model is used. All text analysis is rule-based pattern matching.
- **Filings used.** The latest 10-K and the prior 10-K, the latest four 10-Qs and the year-earlier 10-Q for the same fiscal quarter, the newest DEF 14A filed within 18 months, and an earnings-release exhibit (EX-99.1) from an 8-K with Item 2.02 where one exists.
- **Citations.** Every number and every quote shown in the app or the PDF carries a citation to its accession number, filing date, section, and source URL.
- **Determinism.** The same inputs and the same parser version produce identical results.

## How the financial data is normalized (XBRL)

Company facts are XBRL data that companies file with their reports. The app works from the reported values for each period, not from the labels on each filing.

- **Keys.** A fact is identified by its tag, unit, start date, and end date. The `fy`, `fp`, and `frame` fields describe the filing that reported the fact, not the period, and are never used as keys.
- **Accepted forms.** `10-K`, `10-K/A`, `10-Q`, `10-Q/A`, `10-KT`, and `10-QT`.
- **Units.** `USD` for money, `shares`, and `USD/shares`. Units are never mixed.
- **Dedup and restatements.** For an identical tag, unit, start, and end, the row with the latest `filed` date is kept, so the most recent restated value is used. A point is flagged `restated` when the earliest-filed value differs from the latest by more than 1%. Original values are flagged, not used.

### Discrete quarters

Most cash-flow items in 10-Qs are reported year to date, so a quarter must be derived.

| Duration class | Length in days |
|---|---|
| 3M | 80–100 |
| 6M | 170–190 |
| 9M | 260–285 |
| FY | 350–380 |

Any other duration is discarded.

- If a 3M fact is present, it is used.
- Otherwise: Q2 = 6M − 3M, Q3 = 9M − 6M, and Q4 = FY − 9M. All components must share the same fiscal-year start and the same tag. The result is marked `derived`.
- If any component is missing, the value is `None`.
- 10-Q cash-flow items are year-to-date only, so they are always derived.

For example, Q4 revenue equals fiscal-year revenue minus nine-month revenue.

### Fiscal calendar

Fiscal years are not assumed to end in December. A fiscal year is defined by the `(start, end)` of its FY fact. Periods are labelled by end date, for example "FQ ending 2025-01-26". Walmart (year ending January 31) and NVIDIA (last Sunday of January, with 52- and 53-week years) are the reference cases.

### Year-over-year comparison

The prior-year comparison is the same duration class with an end date 365 ± 10 days earlier. For instants (balance-sheet values), it is the instant at that prior end date, within ± 10 days.

### Trailing twelve months (TTM)

TTM is the sum of the last four discrete quarters. All four quarters must be present; otherwise TTM is `None`.

### Tag fallbacks

Companies sometimes change which XBRL tag they use for the same item. For each period, the tags are tried in the order listed. The first tag with a value wins, and the tag used is recorded. If the current and comparison periods used different tags, the check is marked `tag changed` and the annotation is shown.

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
| Total debt (computed, labelled "approximate") | (`LongTermDebt`, or `LongTermDebtNoncurrent` + `LongTermDebtCurrent`) + `ShortTermBorrowings` + `CommercialPaper`. Missing components count as 0; if all are missing, the result is `None`. |

Company-specific extension tags and dimensional (segment) facts are excluded.

### Global numeric rules

- Growth is `growth(x) = (x_t − x_{t−1}) / x_{t−1}`. It is defined only when `x_{t−1} > 0`. Otherwise the result is `None` and the check is `NOT_COMPUTABLE`.
- Thresholds on differences of ratios are in percentage points (pp). Thresholds on relative change are written as rel %.
- Sequential quarters are never compared. Each quarter is compared with the same fiscal quarter one year earlier, which removes seasonality.
- `days(period) = (end − start).days + 1`.

## Module A: accounting checks

Each check is evaluated at a discrete quarter end `P`. The primary result is the latest quarter with data. Series for the last 12 quarters are kept for charts.

Each check returns one of three states:

| State | Meaning |
|---|---|
| `COMPUTED` | Inputs were available and the rules were applied. Severity is set. |
| `NOT_APPLICABLE` | A materiality or sector gate failed. The reason is shown. |
| `NOT_COMPUTABLE` | An input is missing after fallback. The specific reason is shown, for example "AR not tagged" or "prior-year quarter missing". |

### A1: Receivables outpacing sales (DSO), scored

- `DSO_q = AR_end / Revenue_q × days(q)`
- `ΔDSO = DSO_q − DSO_{q,−1y}` (days)
- `rel = ΔDSO / DSO_{q,−1y}`

**Gates**

- `Revenue_q ≤ 0`, or prior-year data missing: `NOT_COMPUTABLE`.
- `AR_end < 2%` of TTM revenue: `NOT_APPLICABLE` ("receivables immaterial"). This is expected for Walmart.

**Severity**

| Severity | Condition |
|---|---|
| WARNING | ΔDSO ≥ +5 days **and** rel ≥ +10% |
| CRITICAL | ΔDSO ≥ +10 days **and** rel ≥ +20% **and** the WARNING condition also held at the previous quarter end |
| NO FLAG | Otherwise |

The rule uses DSO rather than a difference in growth rates. NVIDIA's fiscal 2024 revenue rose about 126% while receivables rose about 161%, a +35 pp "divergence", yet quarter-based DSO fell. A growth-difference rule would raise a false CRITICAL. A test confirms that the NVIDIA seed is not CRITICAL on A1.

### A2: Inventory outpacing cost of sales (DIO), scored

- `DIO_q = Inventory_end / COGS_q × days(q)`
- The ΔDIO and relative-change thresholds and the persistence rule are the same as for A1.

**Gates**

- `COGS_q ≤ 0`: `NOT_COMPUTABLE`.
- `Inventory_end < 2%` of TTM COGS, or no inventory tag reported at all: `NOT_APPLICABLE`.

### Acquisition guard (applies to A1 and A2)

If either condition holds, the guard applies:

- TTM `PaymentsToAcquireBusinessesNetOfCashAcquired` is ≥ 5% of prior-year `Assets`; or
- `Goodwill` rose ≥ 10% year over year.

When the guard applies, the severity is capped at WARNING and the annotation `acquisition-distorted` is added.

### A3: Accruals and cash conversion, scored

**Primary measure (Sloan accrual ratio)**

- `AccrualRatio = (NI_TTM − OCF_TTM) / avg(Assets_P, Assets_{P−1y})`
- WARNING at ≥ 5%. CRITICAL at ≥ 10%.

**Secondary measure (cash conversion ratio)**

- `CCR = OCF_TTM / NI_TTM`
- Computed only if `NI_TTM > 0` **and** `NI_TTM ≥ 2%` of TTM revenue. Otherwise the display reads "n/m".
- WARNING if CCR < 0.75.
- CRITICAL if CCR < 0.5, **or** if `NI_TTM` rose year over year while `OCF_TTM` fell ≥ 10% rel.

**Loss-making companies** (`NI_TTM ≤ 0`): only the accrual ratio is used, with the note "loss-making: cash conversion n/m".

**Combined severity:** the higher of the primary and secondary severities. The check counts once.

### A4: Capitalization intensity, scored

- `CapSW` = `PaymentsToDevelopSoftware`, or else `CapitalizedComputerSoftwareAdditions` (TTM).
- `CapRate = CapSW_TTM / (CapSW_TTM + ResearchAndDevelopmentExpense_TTM)`

**Gates**

- Tags absent: `NOT_COMPUTABLE`. This is expected for many issuers.
- `CapSW_TTM < 1%` of revenue: `NOT_APPLICABLE`.

**Severity**

- WARNING: CapRate up ≥ 5 pp year over year.
- CRITICAL: CapRate up ≥ 10 pp year over year.

### A5: Recurring "one-time" restructuring, scored

- `R_q` = `RestructuringCharges`, or else `RestructuringCostsAndAssetImpairmentCharges`, or else `RestructuringAndRelatedCostIncurredCost`. Each is a discrete quarter.
- A quarter **counts** if `|R_q| ≥ 0.5%` of that quarter's revenue.

**Severity**

- WARNING: at least 3 of the last 4 quarters count.
- CRITICAL: at least 6 of the last 8 quarters count, **or** a material charge in each of the last 3 fiscal years. The PRD does not define a separate fiscal-year materiality test beyond this wording.

If no restructuring tag is reported in any period, the result is NO FLAG with the note "no restructuring tags reported".

Impairments (`GoodwillImpairmentLoss`, `AssetImpairmentCharges`, `ImpairmentOfLongLivedAssetsHeldForUse`) are displayed alongside but never trigger A5.

### Display-only metrics

These appear on the Ratios view. They are never scored.

| Metric | Definition | Note shown |
|---|---|---|
| `X_CAPEX_DA` | CapEx_TTM / D&A_TTM | Shown when the ratio is ≥ 2.0 and up ≥ 25% rel. If D&A ≤ 0, the value is n/m. |
| `X_DPO` | AP_end / COGS_q × days | "payables stretch supporting OCF" when up ≥ 10 days and ≥ 20% rel. |
| `X_FCF` | `OCF_TTM − CapEx_TTM` | None |
| `X_FCF_SBC` | `FCF − SBC_TTM` | None |
| `X_ROIC` | See below | IC ≤ 0 gives n/m (expected for BA). |
| `X_RESTATED` | Lists the metrics with `restated=True` points | None |

ROIC is computed as follows:

- `NOPAT = OperatingIncomeLoss_TTM × (1 − t)`
- `t = clamp(IncomeTaxExpenseBenefit_TTM / PreTaxIncome_TTM, 0, 0.35)`. If pre-tax income ≤ 0, `t = 0.21`.
- `IC = avg(TotalDebt + StockholdersEquity − CashAndCashEquivalentsAtCarryingValue)` over `P` and `P−1y`.
- `IC ≤ 0` gives n/m.

## Module A: narrative claims and pairing

Module A also reads management language. It looks for claims in MD&A and earnings releases, then pairs each claim with the numeric check for the same period.

### Where text is read

HTML is parsed, and non-content elements (hidden inline-XBRL headers, scripts, and styles) are removed. Block elements become lines, and table rows are joined with ` | `. Common Unicode punctuation and spacing are normalized, and page headers and footers are dropped.

For narrative scanning only, "data table" rows are dropped where 40% or more of the non-empty cells match `^[\$\(\)\d,.\-%\s]+$`.

**Section location**

Where more than one start match exists, the match with the longest span is chosen. This avoids table-of-contents and cross-reference hits.

- **10-K MD&A:** start `(?im)^item\s*7\s*[.:\-]?\s*management'?s?\s+discussion`, end at the next `(?im)^item\s*(7a|8)\b`. At least 1,500 words are required.
- **10-Q MD&A:** start `(?im)^item\s*2\s*[.:\-]?\s*management'?s?\s+discussion` (the title is mandatory, which excludes Part II Item 2). End at `(?im)^(item\s*3\b|item\s*4\s*[.:\-]?\s*controls|part\s+ii\b)`. At least 800 words are required.
- **Exhibit 13 fallback:** if the span is too short and contains `incorporated\s+(herein\s+)?by\s+reference`, the EX-13 document (typed `EX-13*` in the filing index) is opened. The span runs from the heading `management'?s\s+discussion\s+and\s+analysis` to the heading `(report of independent registered|consolidated (balance sheets?|statements? of))`.
- **8-K earnings release:** the text of EX-99.1 up to the first data table or the paragraph containing "forward-looking statements", whichever comes first.
- **Failure:** the section status is `NOT_FOUND`, and the app shows "Narrative unavailable for this filing". Numeric checks still render. Whole-document text is never used as a fallback.

Item 1A (Risk Factors) is never scanned.

### Sentences

- Periods are masked in `Inc. Corp. Co. Ltd. U.S. No. vs. e.g. i.e. Mr. Ms. Dr. approx.`, in month abbreviations, and in `\d\.\d`.
- Sentences are split with `(?<=[.!?])\s+(?=["(]?[A-Z])`.
- Only sentences of 8 to 80 words are kept.

### Claim clusters

A sentence that matches a cluster pattern is a candidate claim. The three clusters are:

- **DEMAND:** `\b(strong|robust|healthy|solid|record|accelerat\w+)[\s-]+(\w+[\s-]+){0,3}(demand|growth|orders|bookings|backlog|pipeline)\b|market[\s-]+share[\s-]+gains?`
- **MARGIN:** `pricing[\s-]+power|margin[\s-]+expansion|disciplined[\s-]+cost|cost[\s-]+discipline|operating[\s-]+leverage|operational[\s-]+efficienc\w+`
- **ONETIME:** `transitory|one[\s-]time|non[\s-]?recurring|isolated|unusual[\s-]+(item|charge)s?`

All patterns are case-insensitive.

### Rejection rules

A candidate sentence is **not** a claim if any of these hold:

- **Negation:** a negation token appears within the 6 tokens before the match. Negation tokens: `no|not|n't|never|without|lack\w*|absence|weak\w*|soft\w*|declin\w+|lower|decreas\w+`
- **Forward-looking:** the sentence contains a forward-looking marker. Forward-looking marker pattern: `\b(expect\w*|anticipat\w*|believe\w*|intend\w*|plan\w*|forecast\w*|outlook|guidance|may|could|should|would|will|if)\b`. The PRD labels this rejection `FORWARD` and notes that the tag is never used.
- **Boilerplate:** the paragraph mentions "forward-looking statements" or "Private Securities Litigation Reform".
- **Out of scope:** the sentence lies outside the located section.

### Pairing with numeric checks

| Check | Paired clusters |
|---|---|
| A1 DSO | DEMAND |
| A2 DIO | DEMAND, MARGIN |
| A3 Accruals | none (numbers only) |
| A4 CapRate | MARGIN |
| A5 Restructuring | ONETIME |

**Period alignment.** A claim from a filing whose report date is `P` pairs only with that check evaluated at quarter end `P`. For a 10-K, `P` is the fiscal year end (Q4, derived). An 8-K pairs with the period of the 10-Q or 10-K it precedes.

**Severity and escalation**

1. Start from the check's numeric severity.
2. If the numeric severity is WARNING or higher **and** at least one paired claim exists for the same `P`, set `contradiction = True` and escalate WARNING to CRITICAL.
3. Exception: a check annotated `acquisition-distorted` is never escalated above WARNING.

A numeric flag with no paired claim is shown as "Unaddressed divergence", with no quote. A claim whose paired check is NO FLAG is shown under "Claims consistent with numbers". This is informational and unscored.

**Quotes.** At most 2 quotes are shown per check. MD&A is preferred over 8-K text, and then the earliest position in the text is preferred. Quotes longer than 60 words are shortened for display only, at a word boundary, with "…".

**Citation check.** Every quote must be an exact substring of the cached section text at the recorded character offsets. This is tested for every seed ticker.

## Module B: footnote redline and hedging drift

### Filing pairs

- The 10-K is compared with the prior-year 10-K (default).
- The latest 10-Q is compared with the 10-Q for the same fiscal quarter one year earlier.
- Filings are never compared across form types. Amendments are ignored.

### Segmenting the notes

- **Primary:** each inline-XBRL non-numeric block whose name ends in `TextBlock` is one segment, keyed by its concept name. Continuation chains are followed. Note-level blocks are preferred, and `*PolicyTextBlock` segments are nested inside their note.
- **Fallback:** within Item 8 (10-K) or Part I Item 1 (10-Q), after the longest-span match of `notes\s+to\s+(the\s+)?(condensed\s+)?(consolidated\s+)?financial\s+statements`. A note header is a line of 15 words or fewer that matches `^(note\s+)?\d{1,2}\s*[.:\-]\s*[A-Z]`.

### Matching notes across years

Notes are matched in this order:

1. identical concept name;
2. title-token Jaccard similarity ≥ 0.5;
3. TF-IDF cosine similarity of the note bodies ≥ 0.6.

Note numbers are never used for matching. Notes that cannot be matched are shown as `NEW NOTE` or `REMOVED NOTE`. These are displayed but not scored.

### Categories

A note is a category note when its title or concept matches one of these keywords:

- `revenue`
- `debt|borrowing|credit`
- `inventor`
- `commitment|contingenc|legal|litigation`
- `goodwill|intangible`
- `related part`

All other notes are displayed only.

### Normalization

Normalization is applied to a comparison copy only. The original text is always displayed. The steps run in this order:

1. Dates become `<DATE>`: `(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b`
2. Amounts become `<AMT>`: `\(?\$\s?[\d,]+(\.\d+)?\)?(\s?(thousand|million|billion|[MB])\b)?`
3. Percentages become `<PCT>`: `[\d,.]+\s?(%|percent|basis points|bps)`
4. Years become `<YEAR>`: `\b(19|20)\d{2}\b`
5. Other numbers become `<NUM>`: `\b[\d,]+(\.\d+)?\b`
6. Text is lowercased.

### Alignment within a matched note

1. Sentences whose normalized form appears on both sides are removed. This also absorbs moved text.
2. On the remaining sentences, TF-IDF cosine similarity (word 1- and 2-grams) is computed. A greedy one-to-one match at ≥ 0.55 is `MODIFIED`. Unmatched sentences are `INSERTED` (current side) or `DELETED` (prior side).
3. For `MODIFIED` pairs only, a word-level redline is produced with `difflib.SequenceMatcher(autojunk=False)`.
4. At most 400 remaining sentences are compared per side. Beyond that, the note is marked `TRUNCATED`, is displayed, and is not scored.

### Material edits (scored)

A changed sentence is a **material edit** only if all of these hold:

- it is in a category note;
- it has at least 12 words;
- it contains one of: `recogni[sz]|covenant|default|waiver|reserve|allowance|impair|probable|reasonably possible|estimat|useful li(fe|ves)|going concern|restat|material weakness|related part|guarantee`;
- **and** one of the following:
  - it is `INSERTED` or `DELETED`;
  - it is `MODIFIED` with a SequenceMatcher ratio below 0.85;
  - it is a modal softening (below).

Sentences containing `ASU|accounting standards update|recently (issued|adopted)` are excluded.

### Modal softening

A `MODIFIED` pair is a modal softening when the strongest modal among the deleted tokens is stronger than the strongest modal among the inserted tokens. Modal strengths:

| Strength | Phrases |
|---|---|
| 3 | `will`, `shall`, `must`, `is required to`, `are required to` |
| 2 | `expects? to`, `intends? to`, `plans? to` |
| 1 | `may`, `might`, `could`, `seeks? to`, `intends? to seek`, `may not be able to`, `no assurance` |

Longer phrases are matched first.

### Hedging drift (10-K pair only)

- **Corpus:** the matched category notes, concatenated and normalized.
- **Rate:** hedge hits per 1,000 tokens. Hedge pattern: `\b(may|might|could|possibly|potentially|uncertain\w*|believes?)\b|subject[\s-]+to|contingent[\s-]+(on|upon)|no[\s-]+assurance|cannot[\s-]+(predict|assure|estimate)|reasonably[\s-]+possible|substantial[\s-]+doubt`
- **Fires only if all of these hold:**
  - both sides have at least 1,500 tokens;
  - relative increase is greater than 15%;
  - absolute increase is at least 1.5 per 1,000 tokens;
  - the hit count is up by at least 5.
- Otherwise the panel shows "No significant drift" or "Insufficient text", as applicable.

## Module C: proxy incentive X-ray

### Source

- The DEF 14A selected under the filing rules is used.
- If there is none, the 10-K Part III Item 11 is used, when it has at least 1,500 words.
- Otherwise the status is `unavailable`, with no deduction.

### Compensation Discussion and Analysis (CD&A) location

- **Start:** `compensation\s+discussion\s+(and|&)\s+analysis`
- **End:** `compensation\s+committee\s+report|summary\s+compensation\s+table`
- The longest-span rule is applied.
- If the start is not found, the span runs from the heading `executive\s+compensation` to the end pattern.

### Metric detection

Metric aliases:

- ADJ_EBITDA: `adjusted[\s-]+ebitda`
- NONGAAP_EPS: `(adjusted|non[\s-]?gaap)[\s-]+(diluted[\s-]+)?(eps|earnings[\s-]+per[\s-]+share)`
- REVENUE: `(net[\s-]+)?(revenue|sales)([\s-]+growth)?`
- TSR: `total[\s-]+shareholder[\s-]+return|\bTSR\b`
- FCF: `free[\s-]+cash[\s-]+flow`
- ROIC: `return[\s-]+on[\s-]+invested[\s-]+capital|\bROIC\b|\bROCE\b`
- M_AND_A: `acquisitions?[\s-]+completed|acquired[\s-]+revenue|deal[\s-]+volume`

Rules for a hit to count:

- **Incentive context:** the sentence or table row must also contain `weight\w*|target|threshold|maximum|payout|performance\s+(metric|measure|goal)|annual\s+incentive|bonus|PSU|performance\s+share`.
- Hits under headings matching `non-gaap|reconciliation|definitions?|appendix|annex` are excluded, as are sentences containing `is defined as`, `we define`, or `\bmeans\b`.
- A glossary-only mention of a non-GAAP metric is not counted.

### Weights

- Weight = `(\d{1,3})\s?%` in the same table row as the alias, in a table whose header row contains "weight".
- Otherwise the weight is `None`, and the app shows "weight not parsed".

### LOW_CONFIDENCE

The result is marked `LOW_CONFIDENCE` when either of these holds:

- fewer than 2 metrics are detected; or
- the CD&A contains 3 or more images and fewer than 2 metrics are detected.

For `LOW_CONFIDENCE`, the extracted text is shown, no flags are raised, and no deduction is made. Metrics that appear only in images or charts are not detected.

### Flags

Both flags are WARNING and scored. Points are set in the scoring table below.

**C1: SBC against non-GAAP pay.** Fires if an ADJ_EBITDA or NONGAAP_EPS metric is detected **and** any of these holds:

- `SBC_TTM / Revenue_TTM ≥ 10%`;
- `SBC_TTM ≥ 50%` of `FCF_TTM` (when FCF > 0);
- `FCF_TTM > 0` and `FCF − SBC < 0`.

**C2: Acquisition-heavy goodwill.** Fires if a REVENUE or M_AND_A metric is detected **and** `Goodwill/Assets ≥ 35%` **and** either of these holds:

- Goodwill/Assets is up at least 3 pp year over year; or
- the acquisition guard (see Module A) is tripped.

**Note (unscored):** "ROIC/FCF not identified among incentive metrics". This is shown only if at least 2 other metrics were detected and neither ROIC nor FCF appears anywhere in the CD&A. The note does not claim that any metric carries zero weight.

## Scoring

### Points, caps, and categories

| Category | Source | Per CRITICAL | Per WARNING | Category cap |
|---|---|---|---|---|
| `accounting` | A1–A5 findings (post-escalation) | 12 | 5 | 45 |
| `footnotes` | Material edits (see Module B), 10-K pair only | — | 5 each | 20 |
| `incentives` | C1, C2 | — | 5 each | 10 |
| `hedging` | Hedging drift fired | — | 5 | 5 |

Modules never assign points. Only the scorer does.

### De-duplication

If A3 fires together with A1 or A2 at the same `P`, A3's points are halved, rounded down.

### Score

- Each category's deductions are capped at its category cap.
- `score = max(0, 100 − Σ capped category deductions)`

### Coverage

- Coverage = (scored accounting checks in state `COMPUTED`) / (scored accounting checks not `NOT_APPLICABLE`).
- If the denominator is 0, or coverage is below 60%, the score is `None` and the label is "INSUFFICIENT DATA".
- Otherwise the label is "Score {n} · coverage {num}/{den}".

### Modules and missing data

- Modules B and C contribute only when their status is `ok` or `partial`.
- The app shows "{k} of 3 modules assessed" next to the score.
- An unassessed module deducts nothing, and it is listed as unassessed. Missing data is never rewarded.

## Sector and filer-type gating

The gates run before any check.

1. **No 10-K in the last 3 years:** the filer is unsupported and the analysis stops.
2. **SIC 6770, or TTM revenue missing or zero:** the filer is a shell or pre-revenue company. The Module A numeric checks are `NOT_APPLICABLE`, and no score is given.
3. **SIC 6000–6799 (financial issuer):**
   - A1, A2, and A4 are `NOT_APPLICABLE` ("financial issuer").
   - A3 is computed but `scored=False`, and is labelled "low reliability for financials".
   - A5 runs normally. Modules B and C run normally.
   - The scope note reads "Financial issuer: limited scope; sector credit metrics not supported in v1."
4. **Ordinary filer:** all checks run.

Foreign private issuers (20-F, 40-F, 6-K), funds and ETFs, and legacy `.txt`-only filings are unsupported.

## Known limitations

- **Heuristic text matching.** It misses claims phrased unusually and can pair sentences incorrectly.
- **Not modelled:**
  - FX effects;
  - LIFO;
  - receivables factoring or securitization, which artificially lowers DSO;
  - segment mix;
  - dimensional XBRL data;
  - company-specific extension tags.
- **53-week fiscal years** make one quarter about 7% longer. DSO and DIO adjust for this through `days(q)`. Growth-based metrics do not.
- **Restatements** use the as-restated values. Original values are flagged, not used.
- **Image-only proxy metrics** are not detected, which produces `LOW_CONFIDENCE`.
- **Financial issuers, foreign private issuers, funds, and shells** are out of scope or limited.
- **The score is uncalibrated** and not comparable across sectors.

## Disclaimer

> *Automated screening heuristics derived from public SEC filings. Flags indicate items warranting further review, not findings of misstatement, fraud, or wrongdoing. Heuristics can produce false positives; verify against the cited source filings. Not investment advice. Not audited. Not affiliated with the SEC.*
