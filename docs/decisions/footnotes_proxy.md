# Decisions: Modules B and C (footnotes, proxy)

### D-401 Table rows are not "sentences" for the material-edit rule  (2026-11-24, milestone M3)
- **Question:** §7.3.5 speaks of changed sentences, but note text contains table rows ("a | b | c").
- **Options considered:** score rows; ignore rows for materiality only.
- **Choice:** Rows (" | ") still align and appear in the redline but are never `material`. Word counts for the 12-word rule ignore "|".
- **Rationale / evidence:** NVDA 10-Q seed: table header rows ("... | Estimated Fair Value | ...") matched `estimat` and produced false material edits.
- **Revisit if:** a covenant table needs scoring.

### D-402 iXBRL parsed as XML, not HTML  (2026-11-24, M3)
- **Question:** `lxml.html` mis-nests `ix:continuation`/`ix:nonNumeric` that wrap block elements, so policy blocks looked top-level (NVDA: 42 segments instead of 22).
- **Choice:** Parse as XML (recover) first; use the HTML parser only if XML recovery sees fewer TextBlocks than the raw text. Top-level segments are TextBlocks with no TextBlock or continuation ancestor. Because a note's `continuedAt` chain can pass through sibling policy blocks, a block whose element holds another chain's continuation (or whose continuation lies inside another chain) is nested; of two entangled chains the later in document order is the nested one. Continuation text is appended to its owner. iXBRL is used when >= 3 segments are found, else the header fallback, else whatever iXBRL found.
- **Evidence:** NVDA, WMT, TSLA, BA seeds segment to 18-29 notes per filing; fallback cross-check recovers 12-24 notes from text alone.

### D-403 Header fallback details  (2026-11-24, M3)
- Notes-heading must sit on a line of <= 12 words (avoids in-sentence references). Headers must continue the numbering (n = last + 1) or restart at a "Note 1" line; the title must start with an uppercase letter (the PRD `[A-Z]` is case-insensitive in `lexicons.NOTE_HEADER`). Duplicate titles (index listing vs body) keep the longest body. If Item 8 / Part I Item 1 only references F-pages, the earliest qualifying notes heading in the whole filing is used. Table-row headings (`ITEM 8. | FINANCIAL ...`) are accepted via an optional `| ` (same fix as D-302).

### D-404 Matching details  (2026-11-24, M3)
- Title tokens are lower-case alphabetic words minus "note"/digits (no stopword removal). Concept match pairs duplicates in document order. Greedy matching breaks ties by index. Body TF-IDF uses word 1-2-grams on the comparison copy. A matched pair takes its category from the current note, else the prior. Titles come from the first line when it is <= 15 words, has no " | " and does not end in punctuation; else from the concept name.

### D-405 Display text and data rows  (2026-11-24, M3)
- Note display text = `element_text` of the block (+ continuation chain) passed through `drop_data_rows` (numeric table rows removed, as for narrative scanning). Offsets in citations index this text; it is stored whole in `extra["note_texts"]`. Deleted sentences cite the prior filing (`DocRef` of the prior side) with offsets into the prior note text; rows carry `prior_span`/`current_span`.

### D-406 Word-level redline and modal softening  (2026-11-24, M3)
- The SequenceMatcher runs on whitespace tokens grouped into units: date/amount/percent/year/number spans (same patterns and priority as §7.3.3) collapse to one unit so "$4.1 billion"/"$4.3 billion" compare equal. The ratio uses unit keys. In the display `ops`, an "equal" unit whose original text differs is shown as delete+insert, so numeric changes stay visible without affecting materiality.
- Softening: modal phrases (longest first, matched spans consumed) are located in each full sentence; a phrase counts as deleted/inserted when it overlaps a deleted/inserted unit. Softened = max deleted strength > max inserted strength.
- Materiality word/term/ASU tests apply to either side of a MODIFIED pair.

### D-407 Hedging payload  (2026-11-24, M3)
- Status values: `FIRED`, `NO_DRIFT`, `INSUFFICIENT_TEXT`, `NOT_ASSESSED` (no 10-K pair), plus a display `label`. Corpus = comparison-normalized text of matched category notes; tokens via `lexicons.TOKEN`. Prior rate 0 with current hits is treated as an infinite relative increase.

### D-408 CD&A span heuristics  (2026-11-24, M3)
- Start/end matches must sit on heading-like lines (<= 15 words; fallback start <= 8 words), or the start must be a self-reference ("This Compensation Discussion and Analysis ..."). Table-row hits (glossary "CD&A | Compensation ...", TOC page numbers) count only if no plain heading line exists. Without this, in-sentence cross references stretched the literal longest span (BA: 138k chars of governance text; NVDA: a glossary row). With no end heading the span runs to the end of the text. Item 11 requires an Item 12 end.
- A heading (for the exclusion rule) is a line of <= 12 words that does not end in `.;,`, is not a table row and contains no metric alias; the most recent heading decides whether hits are excluded.

### D-409 Incentive context and weights  (2026-11-24, M3)
- A table row also takes incentive context from the first 3 rows (header) of its table, so "Adjusted EBITDA | 40%" under a "Weighting/Target" header counts. Weights come only from a table whose first 3 rows contain "weight": the percent in the weight column when the cell count matches the header, else the single distinct percent in the row, else `None`. Sentences never yield a weight.
- Flags use fractions for goodwill/assets (0.35 = 35%). Each flag records state NO_FLAG / FIRED / NOT_APPLICABLE / NOT_COMPUTABLE in `extra["checks"]`; unavailable inputs never fire.

### D-410 Known proxy limitation  (2026-11-24, M3)
- TSLA's performance award lists "$50 billion of Adjusted EBITDA" milestones without any §7.4.3 context word in the same sentence or row, so Module C returns `low_confidence` (no flags) by the PRD rule.

### D-411 Category by title, not concept  (2026-11-24, M3, audit fix)
- Category is read from the heading title; the concept is used only when no heading title exists. "Cash Equivalents and Marketable Securities" (an `InvestmentsInDebt...` concept), BA "Investments" and "Allowances for Losses on Financial Assets" are no longer "debt". This removed false hedge hits: NVDA hedging is now NO_DRIFT (7 -> 11 hits).

### D-412 Material-term, softening and sentence fixes  (2026-11-24, M3, audit fix)
- `MATERIAL_TERMS` uses `(?<!federally )recogni[sz]` ("federally recognized tribes" is not an accounting recognition). Softening requires a weaker modal to replace a stronger one (`max_ins >= 1 and max_del > max_ins`); a bare deleted modal is not softening. Hard-wrapped lines (no terminal punctuation, next line starts lower-case, neither a table row) are joined before sentence splitting, so a quote may contain a newline but remains a verbatim slice. "St." is masked as an abbreviation. Explanations name the matched term.

### D-413 Non-note TextBlocks excluded  (2026-11-24, M3, audit fix)
- iXBRL blocks with `dei`, `ecd` or `cyd` prefixes (cover page, cybersecurity, insider-trading) are not financial-statement notes. Issuer-extension prefixes are kept only between the first and last us-gaap TextBlock in document order; `us-gaap`, `srt` and `ifrs-full` are always kept.

### D-414 Proxy hit filters  (2026-11-24, M3, audit fix)
- A hit whose every alias match has a negation (lexicon `NEGATION_TOKEN` in the 6 tokens before, or "does not believe/consider/use/rely") is ignored. Sentences about peer-group selection/appropriateness/adjustment (`PROXY_PEER_SELECTION`) are ignored. The metrics table keeps one row per (metric, context): a weighted row wins, else the earliest. LOW_CONFIDENCE and the ROIC/FCF note use all hits, not the deduplicated table.
- `NONGAAP_EPS` also matches "core EPS" / "core earnings (loss) per share" (BA). C1 "not applicable" now reads "Adjusted EBITDA / non-GAAP EPS not detected"; other non-GAAP measures (NVDA Non-GAAP Operating Income) are outside the PRD alias list.

### D-415 Exclude consultant-analysis sentences from proxy metrics  (2026-11-24, re-audit)
- **Question:** WMT's CD&A mentions "analyses of the correlation between incentive plan performance measures and total shareholder return", a compensation-consultant analysis, not an incentive metric. It was counted as a TSR metric.
- **Choice:** Sentences or rows matching `analys\w+ of the (correlation|relationship|alignment)` or `correlation between` (lexicons `PROXY_ANALYSIS_CONTEXT`) do not count as metric hits.
- **Consequence:** WMT now has one distinct metric (REVENUE), so Module C is `low_confidence` under §7.4.3 (fewer than 2 metrics) and deducts nothing. WMT's score is unchanged (95). The golden snapshot was regenerated deliberately.
- **Revisit if:** a CD&A uses "correlation" language to describe an actual payout formula.
