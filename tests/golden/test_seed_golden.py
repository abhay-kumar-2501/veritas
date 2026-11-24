"""Golden snapshot of seed-ticker results.

Any change to scores, check states/severities or scored findings must be deliberate:
regenerate tests/golden/seed_expected.json and record why in DECISIONS.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from veritas.config import SEED_DIR, SEED_TICKERS
from veritas.pipeline import analyze

GOLDEN = Path(__file__).parent / "seed_expected.json"
SCORED = ("accounting", "footnotes", "hedging", "incentives")

pytestmark = pytest.mark.skipif(
    not (SEED_DIR / "manifest.json").exists(), reason="seed cache not built"
)


@pytest.mark.parametrize("ticker", SEED_TICKERS)
def test_seed_matches_golden(ticker: str) -> None:
    expected = json.loads(GOLDEN.read_text())[ticker]
    r = analyze(ticker, offline=True)
    a = r.modules["A"]
    got = {
        "score": r.score.score,
        "label": r.score.label,
        "deductions": dict(r.score.deductions),
        "modules": {k: m.status for k, m in r.modules.items()},
        "checks": {
            c.check_id: [c.state, c.severity] for c in a.checks if c.check_id.startswith("A")
        },
        "findings_scored": [
            list(x)
            for x in sorted(
                (f.id, f.severity)
                for m in r.modules.values()
                for f in m.findings
                if f.category in SCORED
            )
        ],
    }
    assert got == expected
