# PDF tear-sheet decisions

### D-501 Fixed two-page canvas with a detail ladder  (2026-11-24, M5)
- **Question:** how to guarantee exactly 2 pages with unbounded findings and long filing text.
- **Options considered:** `SimpleDocTemplate` with a `PageBreak` (can spill to page 3); `BaseDocTemplate` with frames; drawing measured flowables onto a canvas.
- **Choice:** Platypus flowables are measured with `wrap()` and drawn onto a `reportlab.pdfgen` canvas, one frame per page. Each page has a ladder of progressively smaller detail levels (fewer findings, rows, shorter quotes). The first level that fits is used. If none fits, the lowest level is wrapped in `KeepInFrame(mode="shrink")`.
- **Rationale:** there is no code path that can create a third page. Per-block caps print "+N more in app".
- **Revisit if:** a block needs to flow across pages (not a v1 requirement).

### D-502 Glyph sanitising for built-in fonts  (2026-11-24, M5)
- **Question:** Helvetica/Courier (WinAnsi) cannot draw U+2212, >=, <=, arrows.
- **Choice:** `_safe()` maps them to ASCII (`-`, `>=`, `<=`, `->`). The deleted marker is therefore `[-deleted-]`, not `[−deleted−]`. Anything else outside cp1252 becomes `?`. All filing text is also XML-escaped before it reaches a Paragraph.
- **Revisit if:** an embedded TTF is allowed (PRD §9 forbids font files).

### D-503 Findings selection and ordering  (2026-11-24, M5)
- **Choice:** only CRITICAL and WARNING findings from modules A, B, C are ranked: severity first, then PRD §7.5 points (from `config`; A: 12 or 5, B: 5, C: 5). Ties keep module/engine order (stable sort). NO FLAG findings are never listed as findings. Quote = first claim quote, clipped to 240 chars with an ellipsis. Source line = up to 2 distinct (accession, section) citations.

### D-504 Check table values  (2026-11-24, M5)
- **Choice:** the three columns are the last three `CheckResult.history` entries by `period_end`. When history is empty, only the latest value is shown, and the other columns show a dash. The headline metric is the first non-None entry in `outputs`. Non-COMPUTED checks print "Not assessed: {reason}" with an N/A pill. A missing A1-A5 check prints "Not assessed: check not available".

### D-505 Footnote edit rendering  (2026-11-24, M5)
- **Choice:** the top 3 `material` rows across all pairs, with softened rows first. MODIFIED rows get a word-level diff computed here (`difflib`, no engine dependency). Long unchanged runs are collapsed with an ellipsis. Hedging drift prints as one line.

### D-506 Determinism  (2026-11-24, M5)
- **Choice:** `Canvas(invariant=1)` fixes creation date and document ID, so identical results give identical bytes (tested). `build_pdf` reads no clock.
