# Decisions — numeric engine (xbrl_normalize, gating, accounting_checks, scorer)

Draft entries to merge into `DECISIONS.md` (Appendix C template).

### D-201 XBRL citations  (2026-11-24, milestone M2)
- **Question:** §0.2.2 requires a Citation per number. XBRL facts carry no document name, and `DocRef` needs one.
- **Options considered:** look up the primary document (needs I/O, which engine code may not do); build `models.Citation` directly.
- **Choice:** Build `Citation` directly: `section="XBRL"`, `doc_name=""`, `url=https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/` (the filing folder), `form`/`accession`/`filing_date` from the MetricPoint, `xbrl_tag` set, `quote_raw`/offsets None, and `report_date=None`. companyfacts gives no filing reportDate, and the fact's period end is not the filing's report date when the fact is a comparative. Derived quarters and TTM points cite every source fact (the 9M and FY facts for a Q4, for example), with one citation per (accession, tag), ordered by filing date.
- **Revisit if:** the pipeline passes a map from accession to primary document into the engine.

### D-202 Dedup and restatement details  (2026-11-24, M2)
- **Question:** §7.0.4 does not define the base of "differs by more than 1%" or how to break ties.
- **Choice:** `restated = |latest − earliest| > RESTATED_REL × |earliest|`; if the earliest value is 0, any non-zero latest value counts as restated. Ties on `filed` are broken by accession, then by value, so the result is deterministic. Forms outside §7.0.3 (e.g. `DEF 14A` pay-versus-performance rows, `8-K`, `S-1`) are dropped **before** dedup. Each metric reads only the `USD` unit (every Appendix A metric is a money amount), so units are never mixed.

### D-203 Gating order for financial issuers without revenue tags  (2026-11-24, M2)
- **Question:** §7.6 checks "TTM revenue missing/0 → shell" before "SIC 6000–6799 → financial". Many banks do not use any Appendix A revenue tag, so a bank would be labelled a shell.
- **Choice:** No 10-K → unsupported; SIC 6770 → shell; SIC 6000–6799 → financial, even when revenue is missing; otherwise TTM revenue missing or ≤ 0 → shell (negative revenue is treated like 0); else ordinary. Shell and unsupported issuers get `score=None` with the label `"NOT SCORED"`, and every check is NOT_APPLICABLE. `Gating` is a frozen dataclass in `engine/gating.py`, not in `models.py`, because it is not part of the AnalysisResult contract.
- **Revisit if:** the PRD owner wants strict §7.6 ordering.

### D-204 A1/A2 persistence ("previous quarter end that has data")  (2026-11-24, M2)
- **Choice:** Walk the flow metric's discrete quarter ends before P, newest first, but only those less than 355 days (365 − tolerance) before P, so the prior-year quarter itself is never used. Take the first one where ΔDSO/ΔDIO and rel are computable, and test the raw WARNING condition there (no materiality gate). If no such quarter exists, or WARNING did not hold there, a CRITICAL-threshold result is reported as WARNING with an explicit reason.

### D-205 Order of A1/A2 gates; "no inventory tag" → NOT_APPLICABLE  (2026-11-24, M2)
- **Choice:** Checks run in this order: (A2 only) no inventory tag in any period → NOT_APPLICABLE "no inventory tag reported" (PRD §7.1 A2 text); missing flow or balance at P → NOT_COMPUTABLE; flow ≤ 0 → NOT_COMPUTABLE; TTM flow missing → NOT_COMPUTABLE; materiality → NOT_APPLICABLE; prior-year quarter or balance missing, or prior flow ≤ 0 → NOT_COMPUTABLE. Materiality is tested before the prior-year inputs, so an immaterial balance (WMT AR) is always NOT_APPLICABLE.
- **Note:** the coordinator asked for BA A2 to be NOT_COMPUTABLE ("inventory not tagged"). PRD §7.1 explicitly says "no inventory tag reported at all → NOT_APPLICABLE", and the PRD is binding, so BA A2 is NOT_APPLICABLE. Switching it is a one-line change in `_check_days` if the PRD owner decides otherwise. Either way, A2 is not in the coverage numerator.

### D-206 Acquisition guard inputs  (2026-11-24, M2)
- **Choice:** Prior-year assets and goodwill are instants at the matched prior-year quarter end (± 10 days). Missing inputs never trigger the guard. Goodwill growth follows the global growth rule, so it is undefined when the prior goodwill is ≤ 0, and that leg does not fire. TTM acquisitions follow §7.0.9 strictly: if the tag is reported only annually, Q4 cannot be derived and that leg cannot fire. The annotation `acquisition-distorted` is added whenever the guard is active on a COMPUTED A1/A2, not only when it caps a CRITICAL.

### D-207 Evaluation period and history  (2026-11-24, M2)
- **Choice:** `latest_period` is the newest discrete Revenue quarter end, or the newest quarter end of any duration metric if Revenue has none. All A1–A5 and X_* checks are evaluated at that one P, so the A3 de-dup and narrative pairing compare like with like. `history` holds the last 12 such quarter ends, oldest first, as `{"period_end", "value"}`. The value is the primary output: A1 `dso`, A2 `dio`, A3 `accrual_ratio`, A4 `cap_rate`, A5 `material_quarters_last4`, `capex_da`, `dpo`, `fcf`, `fcf_sbc`, `roic`, `restated_metric_count`. It is None wherever the check is not computed there.

### D-208 A3 edge cases  (2026-11-24, M2)
- **Choice:** CCR is n/m (with an annotation) when NI_TTM ≤ 0 (`loss-making: cash conversion n/m`), when NI_TTM < 2% of TTM revenue, or when TTM revenue is missing. The "NI up YoY while OCF down ≥ 10%" CRITICAL leg belongs to the secondary test, so it is evaluated only when CCR is computed. OCF growth needs a prior OCF_TTM > 0.

### D-209 A5 counting  (2026-11-24, M2)
- **Choice:** The windows are the last 4 and 8 Revenue quarter ends ≤ P. A quarter with no restructuring value counts as "not material", since issuers do not tag a charge they did not incur; `quarters_with_data_*` exposes this. The fiscal-year leg uses the 3 latest FY periods ending ≤ P, comparing the FY restructuring fact to FY revenue. When restructuring is tagged only in 10-Ks, an annotation says quarterly counts are unavailable. No restructuring tag in any period gives COMPUTED/NO_FLAG with the reason "no restructuring tags reported" (§7.1). Impairments are shown in `inputs` only.

### D-210 Normalization details  (2026-11-24, M2)
- **Choice:**
  - A derived quarter must itself fall in the 3M class; otherwise it is dropped.
  - Several 3M facts with one end date: the latest filed wins.
  - TTM chains quarters backwards by contiguity (previous end = start − 1 day, else the closest quarter end within ± 10 days), and the 4-quarter span must fall in the FY class.
  - A TTM point takes its tag and provenance from its latest quarter. `derived`/`restated` are true if any component is.
  - `instant(metric, end)` returns the closest instant within ± `YOY_MATCH_TOL_DAYS`. Per-period fallback means the first tag with any instant in the window wins.
  - `fiscal_years()` uses Revenue FY facts, or the union over all duration metrics if Revenue has none.
  - Series are chronological.
  - `metrics()` keys are `"<Metric>.<instant|quarter|fy|ttm>"` and list non-empty series only.

### D-211 Display-only metrics  (2026-11-24, M2)
- **Choice:** "n/m" (D&A ≤ 0, IC ≤ 0) is reported as NOT_COMPUTABLE with an "n/m: …" reason. A computed display metric has severity NO_FLAG and `scored=False`. Notes such as "payables stretch supporting OCF" are annotations. Total debt follows Appendix A (missing components count as 0, all missing → None) and carries the annotation "total debt approximate". ROIC needs TTM pre-tax income; if it is > 0, TTM tax is also required. `X_RESTATED` lists restated quarter/FY/instant points (the tag chosen per period) over the last 12 quarters.

### D-212 Scorer details  (2026-11-24, M2)
- **Choice:**
  - Module A: only `category == "accounting"` findings with severity ≥ WARNING are scored. `accounting_prior_period` and `claims_consistent` are unscored. Findings whose check has `scored=False` (A3 for financials) are also unscored.
  - At most one finding per check_id is scored, as a guard against double deduction: the highest severity, then the latest period.
  - A3 is halved (`//2`) when an A1 or A2 finding fires with the same `check.period_end`.
  - B/C count only when status is ok or partial. Footnote and hedging findings score 5 each regardless of severity (per the §7.5 table). `footnotes_10q` is unscored.
  - `deductions` lists only the categories of assessed modules, in the order accounting, footnotes, hedging, incentives.
  - Module A counts as assessed when its status is ok or partial. `unassessed` holds "Module A: accounting", "Module B: footnotes & hedging" and "Module C: proxy incentives".
  - Coverage counts only scored `A*` checks.

### D-213 Appendix D #5 verification (fiscal years, Q4 reconciliation)  (2026-11-24, M2)
- **Evidence (seed_cache, built 2026-11-24):** NVDA FY ends 2026-01-25, 2025-01-26, 2024-01-28 (364-day years; earlier years include 53-week years). WMT FY ends Jan 31; TSLA and BA FY ends Dec 31. For the 4 latest FYs of every seed ticker, derived Q4 Revenue + 9M = the reported FY exactly. Examples: NVDA FY2026 68,127M + 147,811M = 215,938M; WMT FY2026 188,913M + 517,500M = 706,413M. TTM at FY end = FY. Enforced by `tests/unit/test_accounting_seed.py`. NVDA uses `Revenues` for recent periods; WMT/TSLA use `RevenueFromContractWithCustomerExcludingAssessedTax`, which for WMT is net sales and excludes membership income, as the Appendix A order dictates.
