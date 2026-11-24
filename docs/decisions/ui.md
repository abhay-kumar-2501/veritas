# UI decisions (D-6xx)

Entries follow PRD Appendix C. They will be merged into `DECISIONS.md`.

### D-601 Design system: "forensic truth terminal"  (2026-11-24, M6)
- **Question:** The owner wants a cohesive, polished, Apple-product-page feel in Streamlit while keeping the PRD §8.1 tokens.
- **Choice:** Every colour is a CSS custom property in `src/veritas/ui/styles.css`, commented in place.
  - **Surfaces:** `--bg #0B0E14` and `--panel #131822` are exact (PRD). Three derived elevations keep the same blue cast (`--sunken #0E121A`, `--panel-2 #181E2A`, `--panel-3 #1E2533`), so depth reads as light, not as new colours.
  - **Severity (semantic only):** `#00E676` NO FLAG, `#FF3B30` CRITICAL, `#FFB300` WARNING, `#6B7280` N/A, plus 12–16% translucent fills of the same hues. They appear only on badges, the left "spine" of a finding card, the contradiction connector, threshold bands in charts, redline `<ins>`/`<del>` (green = inserted, red = deleted), and the score ring. Never on decoration.
  - **Accent:** `#5AC8FA`, a clear blue-cyan (Apple's system-cyan family). Blue-cyan reads as clarity, analysis and trust. It sits about 45° of hue from the NO FLAG green and far from amber/red, so "interactive" (links, focus, selected segment, chart series, progress) never reads as a verdict.
  - **Text:** one off-white `#E6EAF2` at 100/72/56/38% opacity. Measured contrast on `--panel`: 14.7:1, 8.1:1, 5.4:1 (all ≥ AA for body text), and 3.2:1 for the 38% step, which is used for decorative glyphs only. On `--bg` the body text is 16.0:1. Accent on panel is 9.4:1; CRITICAL text on panel is 5.0:1. The N/A grey (3.7:1) is never used as text; N/A badges use `--text-2` on a grey fill.
  - **Lines:** hairlines `rgba(255,255,255,.06/.10/.16)`.
- **Type:** no font files and no external fetch. Two voices:
  - Human: `-apple-system, "SF Pro Display", "Inter", system-ui` for headings and prose.
  - Evidence: the PRD mono stack for numbers, tickers, commands, citations, labels, and chart text, always with tabular numerals.
  - Scale: hero 56–88px (clamp, −0.035em tracking); display 40px; section 24px; card title 17px; body 15px; small 14px; data 13px; labels 11px uppercase with +0.12–0.16em tracking.
  - D-006 mentioned a bundled JetBrains Mono, but `assets/fonts` is empty, so the stack falls back to SF Mono/Menlo. Dropping a woff2 there would need a `@font-face` plus `[server] enableStaticServing`. That was not done (see Revisit).
- **Motion (CSS only; `prefers-reduced-motion` collapses all of it to 1 ms):**
  - staggered fade-and-rise (`--i` × 70 ms) for cards, sections, tiles and chips;
  - SVG score ring drawn in with `stroke-dashoffset`, glowing in its band colour;
  - CSS count-up of the score through a typed `@property --n` plus `counter()` (falls back to the final value);
  - deduction bars grow from the left;
  - card hover lift with an accent border glow;
  - a scan-line sweep across the landing hero;
  - a shimmer on the progress bar and a pulse on the active stage;
  - a pulsing contradiction connector (`≠`);
  - segmented-control transitions;
  - a gentle shake on command errors.
- **Revisit if:** the owner wants a bundled mono face; add `static/` and enable static serving.

### D-602 How HTML reaches the page in Streamlit 1.67  (2026-11-24, M6)
- **Finding:** `st.html` is **not** iframed in 1.67. It sanitises with DOMPurify `USE_PROFILES: {html: true}`, which **removes SVG**. DOMPurify also drops any element whose text contains `<` followed by a letter, so a `<style>` with `@property { syntax: "<integer>" }` is deleted wholesale. Style-only `st.html` goes to the event container and takes no layout space. `st.markdown(unsafe_allow_html=True)` uses rehype-raw with no sanitiser, so it keeps SVG and inline `<style>`. Separately, Markdown treats `$…$` as LaTeX and ends an HTML block at a blank line.
- **Choice:**
  - CSS is injected once per run with `st.html("<style>…</style>")`. `styles.css` must contain no `<` (noted in the file header).
  - Components are built by pure functions in `components.py` and rendered with `st.markdown(..., unsafe_allow_html=True)`.
  - Each component is collapsed to a single line (`one_line`).
  - The typed `@property` rule ships inside the score-ring markup.
  - No `<script>` anywhere (§0.1.7). The text-input "Press Enter" hint is hidden, and no `shortcut=` arguments are used.
- **Escaping:** `components.esc()` = `html.escape(quote=True)` plus `$` → `&#36;`. It is applied to every filing-derived string: quotes, titles, explanations, note text, tags, reasons, banners, company names, metric names and proxy sentences. Only `https://www.sec.gov/…` and `https://data.sec.gov/…` URLs become links (`safe_url`). Tests inject `<script>` into a quote, title, company name, banner and module reason, and assert it renders escaped in every view.

### D-603 State, commands and the pipeline hand-off  (2026-11-24, M6)
- **State:** the PRD keys `ticker, view, result, severity_filter, pdf_bytes, cmd_error` are always initialised. The internal helpers (`_pending`, `_run_error`, `_pdf_name`, `_nonce`, `_cmd`) hold only small dicts or strings, never HTML or filing text.
- **Views:** `st.radio(key="view", horizontal=True)`, styled as a segmented control. Commands set `view`/`severity_filter` before the widgets are created in the same run, which Streamlit allows.
- **Analysis:**
  - The command bar or a seed button queues `_pending`. The run then shows `st.status` with an 8-stage HTML panel driven by the `progress` callback (stage names from §2.2, with fuzzy matching for unknown messages), stores the result and calls `st.rerun()`.
  - `analyze` is wrapped in `st.cache_data(ttl=3600)`, keyed by `(target, offline, nonce, refresh)`; the callback is `_progress`, excluded from the key. The key is the typed target, not the CIK, because the CIK is unknown before resolution.
  - "Refresh live" bumps the nonce and passes `refresh=True`.
  - CIK commands are sent to the pipeline as `"CIK <digits>"`, the form `veritas.pipeline.analyze` documents.
- **Errors:**
  - `TickerNotFound` (with ≤ 5 suggestion buttons), `UnsupportedFiler`, `NoXBRL` and `ImportError` are matched by class name, so the UI has no import-time dependency on the pipeline.
  - Any other exception becomes "The analysis could not be completed", with Retry and Dismiss.
  - With no valid User-Agent, non-seed targets are refused before the pipeline runs ("Live mode is off: seed tickers only") and seed suggestion buttons are offered.
- **Keyword precedence:** `DIFF`, `RATIOS`, `PROXY`, `OVERVIEW`, `CONTRA` and `EXPORT` win over the bare-ticker regex. Real tickers with those exact names cannot be typed bare; `ANALYZE <T>` still reaches them.
- **Sample mode:** `VERITAS_UI_SAMPLE=1` loads `tests/fixtures/sample_result.sample_result()` for any target. It is off by default. When on, the UI shows a striped "SYNTHETIC SAMPLE" chip, a dashed "Synthetic" banner on every page, and the kicker "Synthetic sample · not a real issuer". Live refresh is disabled.

### D-604 Score ring colour bands  (2026-11-24, M6)
- **Question:** The owner asked for a ring whose colour reflects the score, but severity colours are semantic.
- **Choice:** Display bands are 85–100 → NO FLAG green, 60–84 → WARNING amber, 0–59 → CRITICAL red. INSUFFICIENT DATA gets a grey dashed ring with no number. The bands are visual only. No severity label is ever derived from the score, and the score header always says "Uncalibrated screening score; not comparable across sectors."
- **Revisit if:** the scorer defines its own bands.

### D-605 Redline rendering and chart bands  (2026-11-24, M6)
- **Redline:** for `MODIFIED` rows the UI computes a word-level diff (`difflib.SequenceMatcher(autojunk=False)` on whitespace tokens, each token escaped first), because the payload carries only the prior and current sentences. INSERTED/DELETED rows show the sentence fully `<ins>`/`<del>` against a hatched "— not present —" cell.
- **Note order:** category notes first, then by material count, then by title.
- **Counts:** taken from `note.counts`; otherwise derived from the rows.
- **Chart bands:** thresholds come from `config.t()`.
  - A1/A2: per-point zones at prior-year-same-quarter + max(abs, rel × prior). The CRITICAL zone shows magnitude only; the persistence test is stated in the caption.
  - A3: fixed accrual-ratio levels.
  - A4: prior-year + 5/10 pp.
  - A5: no band, with a caption explaining why.
- **Chart template:** Plotly charts use `theme=None` and one dark template: transparent background, accent series, x grid off, faint y grid, mono hover labels, mode bar hidden, a ring on the latest point.

### D-606 UI tests and the network guard  (2026-11-24, M6)
- **Question:** `AppTest` needs an asyncio loop, which opens a local `socketpair`. The global conftest guard blocks every `socket.socket`.
- **Choice:** `tests/unit/test_ui_app.py` overrides the autouse `_block_network` fixture for that module only. AF_UNIX sockets are allowed; AF_INET/AF_INET6 and `create_connection` still raise. A test in the same module asserts that Internet sockets remain blocked. `conftest.py` is unchanged.
