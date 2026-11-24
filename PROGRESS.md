# PROGRESS

## M0 — Scaffold & contracts (2026-11-24) ✅
- uv + Python 3.11.18 bootstrapped; deps pinned in `pyproject.toml` and locked in `uv.lock`.
- `models.py` (§5.2 + DocRef), `config.py` (all thresholds, env overrides), `lexicons.py` (Appendix B + section regexes).
- Network-blocking `conftest.py`; CLI skeleton (`python -m veritas --help` works).
- Gate: ruff ✔ format ✔ mypy ✔ pytest 5 passed.
