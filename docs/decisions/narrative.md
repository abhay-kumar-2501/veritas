# Text extraction & narrative-claim decisions

### D-301 HTML-to-text details the PRD leaves open  (2026-11-24, M2)
- **Question:** §7.2.1 lists the block tags, the normalization set, and the drop rules, but not empty table cells, `<head>`, comments, or uncommon space and dash characters.
- **Options considered:** keep empty cells (rows like `Revenue | | $ | | 4,100`); drop them.
- **Choice:** `engine/html_text.py`:
  - Row serialization keeps only the non-empty normalized cells, joined with ` | `.
  - The extra block tags are `table/thead/tbody/ul/ol/section/blockquote/...`, so no text from two blocks merges into one line.
  - `<head>/<title>/<meta>`, comments and PIs emit nothing. A removed element's tail text is kept.
  - Beyond the PRD set, normalization also maps thin, figure and narrow no-break spaces to a space; maps U+2010-2015 and U+2212 to `-`; maps U+201A/201E/2032 to quotes; and deletes BOM/ZWJ/ZWNJ.
  - The repeated-header rule counts words without the `|` separators.
  - ix tags are recognized both as `ix:*` (how lxml.html reports them) and as `{http://www.xbrl.org/2013/inlineXBRL}*`.
- **Rationale / evidence:** EDGAR tables use spacer cells heavily. Keeping them would put empty cells into every row and weaken the data-row ratio. Verified on all seed 10-K, 10-Q and 8-K documents: the hidden `ix:header` facts are absent, and visible `ix:nonNumeric` and `ix:nonFraction` text is kept (fixture `ixbrl_hidden_header.htm`).
- **Revisit if:** a module needs column positions (it would need a structured table API, not text).

### D-302 MD&A heading regexes accept a cell separator  (2026-11-24, M2)
- **Question:** the WMT 10-Ks (FY2025, FY2026) lay out item headings as two-cell table rows. After §7.2.1 row serialization they read `ITEM 7. | MANAGEMENT'S DISCUSSION AND ANALYSIS ...`, and the PRD start pattern `^item\s*7\s*[.:\-]?\s*management` never matches. Without a fix, MD&A is NOT_FOUND for WMT.
- **Choice:** in `lexicons.py` (marked `# narrative`), `MDA_10K_START`, `MDA_10Q_START` and the `item 4 controls` branch of `MDA_10Q_END` allow an optional `(?:\|\s*)?` after the item number. Nothing else changed.
- **Rationale / evidence:** after the fix, all 8 seed 10-K and 10-Q filings locate MD&A (results below). The 10-Q title requirement is unchanged, so Part II Item 2 is still excluded.
- **Revisit if:** another layout appears. Note for other modules: `ITEM8_START`, `TEN_Q_PART1_ITEM1_START/END` and `PART_III_ITEM11_START` have the same weakness for table-laid-out headings.

### D-303 Data-row filter does not alter the cached section text  (2026-11-24, M2)
- **Question:** §7.2.1 drops data rows "for narrative scanning only", but citation offsets must refer to the cached section text.
- **Choice:**
  - `Section.text` is the exact slice of the normalized document, with no rows dropped.
  - `extract_claims` skips lines where `is_data_row(line)` is true, so offsets stay valid. `drop_data_rows(text)` is exported for other modules.
  - Sentences never cross a line (block) boundary.
  - The "paragraph" for the boilerplate rule is the line (block element) that holds the sentence.
- **Revisit if:** filers split one paragraph across several block elements often enough to miss boilerplate.

### D-304 Section statuses and spans without an end  (2026-11-24, M2)
- **Choice:**
  - A start match with no end match after it is not a candidate span. This avoids running into the financial statements.
  - NOT_FOUND carries `reason` and empty text, and never falls back to the whole document.
  - `TRUNCATED` is in the `Section` type but no locator produces it, because §7.2.2 defines no length limit. It is reserved for callers whose input was cut, for example by `SEC_MAX_DOC_BYTES`. `extract_claims` accepts OK and TRUNCATED sections.
  - The Exhibit 13 fallback applies the 10-K minimum of 1,500 words to the EX-13 span.
  - Without `ex13_text`, a filing that needs EX-13 returns NOT_FOUND with the label `EX-13 MD&A` and the reason "incorporated by reference to Exhibit 13; exhibit unavailable".

### D-305 Earnings-release cut point  (2026-11-24, M2)
- **Choice:** "first data table" means the first multi-cell row (contains ` | `) that `is_data_row`. The cut goes before any contiguous multi-cell header rows directly above it, such as `(in millions) | Q4 FY25 | Q4 FY24`. The forward-looking cut is the first line that contains `forward-looking statements`. Single-cell numeric lines do not end the release.
- **Revisit if:** releases with a highlights table at the very top become common. The BA seed releases are cut after about 100 words for this reason. Extended by D-312.

### D-306 Pairing outputs and scoring safety  (2026-11-24, M2)
- **Question:** claims come from several filings with different periods (10-K at fiscal year end, 10-Q at the latest quarter). §7.5 scores A1-A5 findings, and two findings for one check would double the deduction.
- **Choice:** `pair_findings(checks_by_period, claims, latest_checks)` works as follows:
  - Each latest check that is scored, COMPUTED and at WARNING or above yields exactly one `category="accounting"` finding. It is either a contradiction (claims with `period_end == check.period_end`, escalated WARNING→CRITICAL unless annotated `acquisition-distorted`) or an "Unaddressed divergence".
  - Claims at any other period P are matched only with `checks_by_period(id, P)`. The result must have `period_end == P`, be scored and be COMPUTED; otherwise the claims are dropped.
  - A NO_FLAG check yields `category="claims_consistent"` with severity NO_FLAG.
  - A flagged check at a non-latest period yields an **unscored** `category="accounting_prior_period"` contradiction, with escalation applied for display.
  - `contradiction=True` whenever a flagged check has paired claims, including acquisition-distorted checks, which are not escalated.
- **Revisit if:** the scorer should count prior-period contradictions.

### D-307 Claim and quote selection details  (2026-11-24, M2)
- **Choice:**
  - Each (sentence, cluster) pair yields at most one claim, so one sentence can be a DEMAND claim and a MARGIN claim.
  - A cluster counts if any of its matches in the sentence is not negated.
  - The forward-looking test rejects the whole sentence.
  - Quotes per finding: claims are de-duplicated by (accession, doc, char_start, char_end), so an A2 sentence that is both DEMAND and MARGIN counts once. They are then ordered by (8-K last, char_start, filing_date, accession, doc, cluster), and the first `MAX_QUOTES_PER_CHECK` are kept.
  - `Finding.citations` = the selected claim citations followed by the check's citations.
  - Finding ids are `A:<check_id>:<period ISO>:<contradiction|unaddressed|prior|consistent>`.
  - Findings are ordered by that kind, then id.
- **Rationale:** deterministic, and independent of the order of the input claims and checks (tested).

### D-308 Claims need a period  (2026-11-24, M2)
- **Choice:** `extract_claims` returns `()` when `doc.report_date is None`. Without a period a claim cannot be aligned (§7.2.5), and `Claim.period_end` must not be invented. For an 8-K, the caller passes a DocRef whose `report_date` is the paired periodic filing's period.

### D-309 Abbreviation masking  (2026-11-24, M2)
- **Choice:** `ABBREVIATION_RE` in `lexicons.py` (marked `# narrative`) is built from `ABBREVIATIONS`, longest first. It is case-sensitive and must not be preceded by a letter or a period. Matched periods and `\d\.\d` periods are replaced with a same-length placeholder (`\x00`) in a copy of the line, so split offsets apply unchanged to the original.
- **Consequence:** a sentence that ends with an abbreviation (e.g. "... in the U.S. Revenue grew") is not split there. This is accepted per §7.2.3.

### D-311 INTENT rejection: goals and strategy are not claims  (2026-11-24, M2, audit fix)
- **Question:** Appendix B rejects only negated and FORWARD sentences. An audit found that TSLA's A2 check escalated to CRITICAL on boilerplate, "...while focusing on further cost reductions and operational efficiencies ... to enable increased deliveries". It found the same for WMT's "accelerating eCommerce sales growth ... that complement our strategy". Neither sentence asserts an achieved result.
- **Options considered:**
  - keep Appendix B only (false contradictions);
  - the lead's pattern including `strateg\w*`;
  - the same pattern limited to the nouns *strategy* and *strategies*.
- **Choice:** `INTENT` in `lexicons.py` (marked `# narrative`): `\b(focus\w*\s+on|aim\w*\s+to|seek\w*\s+to|strateg(y|ies)|in\s+order\s+to|to\s+enable|priorit\w*|goal\s+(is|of))\b`. It is applied like FORWARD: the whole sentence is tagged INTENT and never becomes a claim. This is a deliberate addition beyond Appendix B under §0.3.
- **Rationale / evidence:** with `strateg\w*`, the adjective also removed WMT's achieved-result sentence "The increase for fiscal 2026 was primarily due to operational efficiencies ... reorganization charges related to strategic supply chain decisions". The noun-only form keeps that sentence and still rejects the strategy sentence. Known false negatives, accepted because false positives escalate scores:
  - BA "...similar solid demand as governments prioritize security";
  - WMT "disciplined cost controls ... strategic growth priorities".
- **Revisit if:** reviewers find achieved-result claims lost to `priorit\w*` or `focus on`.

### D-312 Earnings-release cut for slide decks  (2026-11-24, M2, audit fix)
- **Question:** the TSLA "Update" EX-99.1 is a slide deck with letter-spaced headings and no `|` tables. `locate_earnings_release` ran to about 7,400 words.
- **Choice:** the release also ends at the first of:
  - a line starting with a financial or operational summary heading. `EARNINGS_SUMMARY_HEADING` allows `\s*` between letters, so "F I N A N C I A L S U M M A R Y" matches. A contents line that mentions "Financial Summary" mid-line does not match.
  - a run of `EARNINGS_DATA_RUN_MIN = 3` consecutive numeric lines (single-cell or multi-cell). The cut goes at the start of the run.
- **Note:** `EARNINGS_DATA_RUN_MIN` is a module constant in `sections.py` because `config.py` is owned by another agent. It should move to `THRESHOLDS`.
- **Evidence:** the TSLA releases are now 496 and 497 words (highlights only). The NVDA, WMT and BA releases are unchanged except for a few words.

### D-313 No "claims consistent" without numbers  (2026-11-24, M2, audit fix)
- **Question:** BA A5 is NO_FLAG with "no restructuring tags reported". Its two ONETIME quotes appeared as "consistent with numbers" when there are no numbers.
- **Choice:** `pair_findings` emits no `claims_consistent` finding when the NO_FLAG check has no data. That is the case when any of these holds:
  - the reason or an annotation contains "no restructuring tags reported";
  - every input is None, or the inputs are empty;
  - its `Restructuring*` inputs are all None and `outputs["quarters_with_data_last8"] == 0`. This is the NVDA and WMT A5 case, where `Revenue_q` is present but restructuring data never is.
- The claims are dropped silently. Flag findings are unaffected.
- **Revisit if:** the accounting-check module adds an explicit no-data flag. Use that instead of the output-key heuristic.

### D-314 Earnings citations recognized by form or exhibit type  (2026-11-24, M2, audit fix)
- **Choice:** `_is_8k` treats a citation as an earnings release when its form starts with `8-K` or `EX-99`, or its section is `8-K EX-99.1`. `extract_claims` never depended on form. A seed test runs extraction with DocRef form `8-K` and with `EX-99.1`.

### D-310 Seed results (parser 1.0.0, seed built 2026-11-24)
| Ticker | Filing | MD&A words | Claims |
|---|---|---|---|
| NVDA | 10-K FY2026 / FY2025 | 5,365 / 5,989 | 2 / 2 |
| NVDA | 10-Q Q2 FY27 / Q2 FY26 | 6,204 / 5,438 | 0 / 0 |
| NVDA | 8-K EX-99.1 x2 | 305 / 267 | 0 / 0 |
| WMT | 10-K FY2026 / FY2025 | 9,348 / 9,499 | 4 / 2 |
| WMT | 10-Q Q2 FY27 / Q2 FY26 | 7,728 / 7,485 | 2 / 1 |
| WMT | 8-K EX-99.1 x2 | 624 / 682 | 1 / 1 |
| TSLA | 10-K 2025 / 2024 | 8,563 / 7,616 | 0 / 0 |
| TSLA | 10-Q Q3 2026 / Q3 2025 | 6,340 / 6,196 | 0 / 0 |
| TSLA | 8-K EX-99.1 x2 | 496 / 497 | 0 / 0 |
| BA | 10-K 2025 / 2024 | 13,589 / 13,657 | 4 / 3 |
| BA | 10-Q Q3 2026 / Q3 2025 | 7,745 / 8,841 | 0 / 0 |
| BA | 8-K EX-99.1 x2 | 98 / 72 | 0 / 0 |

(Updated after D-311 to D-314.)

The only NVDA 10-Q match ("We believe these AI cloud partners have strong customer demand ...") is rejected as forward-looking, as the PRD requires.
