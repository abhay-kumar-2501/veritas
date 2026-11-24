# DECISIONS

Entries follow PRD Appendix C. Per-area drafts live in `docs/decisions/` during parallel work and are merged here.

### D-001 Toolchain bootstrap  (2026-11-24, milestone M0)
- **Question:** The machine had only system Python 3.9 and no `uv`.
- **Options considered:** stop (PRD §4.1.3); install `uv`.
- **Choice:** Installed `uv` 0.13.9 (official release tarball, SHA-256 verified) into `~/.local/bin`, then `uv python install 3.11` → CPython 3.11.18.
- **Rationale / evidence:** PRD §4.1 step 1. No shell profile was modified; use `~/.local/bin/uv` or `.venv/bin/*`.
- **Revisit if:** the project moves to CI.

### D-002 Extra dev dependency `types-requests`  (2026-11-24, M0)
- **Question:** `mypy` needs stubs for `requests`.
- **Choice:** Added `types-requests` (dev only).
- **Rationale:** Required for a clean `mypy src/veritas` gate. Type stubs only, no runtime effect.
- **Revisit if:** requests ships inline types.

### D-003 `DocRef` hand-off record  (2026-11-24, M0)
- **Question:** Engine modules are pure (§13) yet must build `Citation`s that carry filing metadata.
- **Choice:** Added a frozen `DocRef` to `models.py` with a `cite()` helper. The EDGAR layer builds it; engines consume it.
- **Revisit if:** never; additive to the §5.2 contract.

### D-004 Extra contract fields  (2026-11-24, M0)
- **Question:** The Ratios view needs 12-quarter history per check, and the score header needs "k of 3 modules assessed".
- **Choice:** Added `CheckResult.history` (default empty) and `ScoreBreakdown.modules_assessed` / `unassessed` (defaults). All are additive with defaults; no PRD field is changed.

### D-005 Banned-words test scope  (2026-11-24, M0)
- **Question:** The mandated disclaimer (§3.4) contains "fraud", which §0.1.8 bans "about an issuer".
- **Choice:** The banned-words test exempts the exact `DISCLAIMER` constant and the `BANNED_WORDS` lexicon itself; every other string literal in `src/` and the rendered PDF text (minus the disclaimer) is checked.

### D-006 UI theming beyond the PRD palette  (2026-11-24, M0)
- **Question:** The owner asked for a highly interactive, cohesive, "Apple-like" UI within Streamlit (the owner chose to keep Streamlit).
- **Choice:** Keep the §8.1 palette tokens exactly. Add derived neutrals and accents only (documented in `src/veritas/ui/styles.css`). JetBrains Mono is bundled locally under `assets/fonts` (OFL licence) and served through Streamlit static serving, so there is no external font fetch. CSS and SVG animation only; no JavaScript keyboard capture (§0.1.7).
