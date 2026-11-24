# Synthetic XBRL fixtures (fictional issuer, CIK 9999999)

Hand-built `companyfacts` payloads. Nothing here is real company data. Every row
carries deliberately wrong `fy`/`fp` values (1999/Q9 unless noted) to prove the
normaliser never keys on them (PRD §7.0.2). `REV` below is
`RevenueFromContractWithCustomerExcludingAssessedTax`; all values in USD.

## q4_derivation_ytd.json — Q4 derivation, YTD-only cash flow, form/unit filters
| Fact | Period | Value | Form |
|---|---|---|---|
| REV 3M | 2023-01-01..03-31 | 100 | 10-Q |
| REV 3M / 6M | Q2 / 01-01..06-30 | 110 / 210 | 10-Q |
| REV 3M / 9M | Q3 / 01-01..09-30 | 120 / 330 | 10-Q |
| REV FY | 2023-01-01..12-31 | 460 | 10-K |
| REV FY (comparative) | 2022 | 400 | 10-K |
| REV 3M | Q1 2023 | 999 | **8-K → ignored** |
| REV 3M | Q1 2023 | 95 | **EUR unit → ignored** |
| OCF YTD | 3M / 6M / 9M / FY | 50 / 80 / 150 / 260 | 10-Q ×3, 10-K |

Expected: Q1 = 100 (reported); Q4 = 460 − 330 = **130**, `derived=True`, period
2023-10-01..12-31 (92 days), cited to the 10-K. OCF Q1 = 50 (reported), Q2 = 80 − 50 =
**30**, Q3 = 150 − 80 = **70**, Q4 = 260 − 150 = **110**, all `derived=True`. OCF TTM at
2023-12-31 = 50 + 30 + 70 + 110 = **260** (= FY). REV TTM = 100 + 110 + 120 + 130 = **460**.

## dedup_restatement.json — dedup keeps latest filed, restatement flag
* AR instant 2023-12-31: 1000 (filed 2024-02-20), 1030 (filed 2025-02-20) → **1030**,
  `restated=True` (3% > 1%), accession `0009999999-25-000010`.
* AR instant 2022-12-31: 900 → 905 (0.56%) → **905**, `restated=False`.
* REV Q1 2023: 100 (10-Q, 2023-05-05), 120 (10-Q/A, 2023-06-01), 120 (10-Q, 2024-05-03),
  777 (S-1, ignored) → **120**, `restated=True` (earliest 100 vs latest 120), form `10-Q`.

## fy_52_53_week.json — NVDA-like 52/53-week calendar (last Sunday of January)
* FY2023: 2022-01-31..2023-01-29 (**364 days**); quarters of 91 days each.
  REV Q1 800, Q2 900 (6M 1700), Q3 950 (9M 2650), FY 3600 → derived Q4 = **950**.
* FY2024 (53 weeks): 2023-01-30..2024-02-04 (**371 days**, FY class 350–380).
  REV Q1 1000, Q2 1200 (6M 2200), Q3 1500 (9M 3700), FY 5400 → derived Q4 = **1700**,
  period 2023-10-30..2024-02-04 = **98 days** (3M class 80–100).
* Prior-year quarter end of 2024-02-04 is 2023-01-29 (gap 371 days, inside 365 ± 10).
* AR: 500 at 2023-01-29, 800 at 2024-02-04.
  DSO now = 800 / 1700 × 98 = 46.118; prior = 500 / 950 × 91 = 47.895.

## fy_january.json — WMT-like fiscal year ending January 31
* FY2024: 2023-02-01..2024-01-31. REV Q1 150 (02-01..04-30), Q2 160 (6M 310),
  Q3 165 (9M 475), FY 650 → derived Q4 = **175**, 2023-11-01..2024-01-31 (92 days).
* FY2023 (FY fact only): 2022-02-01..2023-01-31 = 600.
* `fiscal_years()` = [(2023-02-01, 2024-01-31), (2022-02-01, 2023-01-31)].

## factory.py
Programmatic builder used by the check tests (A1–A5, display metrics, tag switch,
acquisition guard, gating inputs). Each test documents its inputs and the
hand-computed expected values in its docstring.
