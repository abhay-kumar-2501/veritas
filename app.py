"""VERITAS Streamlit entry point: `streamlit run app.py` (PRD §8)."""

from __future__ import annotations

import sys
from pathlib import Path

# Hosted deploys (Streamlit Community Cloud) may install dependencies without
# installing this package; make `src/` importable either way.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from veritas.ui.layout import run  # noqa: E402

run()
