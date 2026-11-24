"""All thresholds, weights and runtime settings (PRD §0.2.1, §4.3).

Engine code must import thresholds from here; never inline them. Any numeric
constant listed in `THRESHOLDS` can be overridden with an env var named
`VERITAS_<NAME>` (e.g. `VERITAS_DSO_WARN_DAYS=7`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

PARSER_VERSION = "1.0.0"
APP_VERSION = "0.1.0"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = PROJECT_ROOT / "seed_cache"

try:  # .env support (python-dotenv is a pinned runtime dependency)
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

# ---------------------------------------------------------------------------
# Numeric thresholds. Units are in the name: _PCT/_REL are fractions (0.10 = 10%),
# _PP are percentage points expressed as fractions (0.05 = 5 pp), _DAYS are days.
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, float] = {
    # §7.0 duration classes (days, inclusive)
    "DUR_3M_MIN": 80,
    "DUR_3M_MAX": 100,
    "DUR_6M_MIN": 170,
    "DUR_6M_MAX": 190,
    "DUR_9M_MIN": 260,
    "DUR_9M_MAX": 285,
    "DUR_FY_MIN": 350,
    "DUR_FY_MAX": 380,
    "YOY_MATCH_TOL_DAYS": 10,
    "RESTATED_REL": 0.01,
    "HISTORY_QUARTERS": 12,
    # A1 DSO / A2 DIO
    "DSO_WARN_DAYS": 5,
    "DSO_WARN_REL": 0.10,
    "DSO_CRIT_DAYS": 10,
    "DSO_CRIT_REL": 0.20,
    "AR_MATERIALITY_PCT": 0.02,
    "INV_MATERIALITY_PCT": 0.02,
    # Acquisition guard
    "ACQ_PAYMENTS_PCT_ASSETS": 0.05,
    "ACQ_GOODWILL_REL": 0.10,
    # A3 accruals
    "ACCRUAL_WARN": 0.05,
    "ACCRUAL_CRIT": 0.10,
    "CCR_WARN": 0.75,
    "CCR_CRIT": 0.50,
    "CCR_NI_MIN_PCT_REV": 0.02,
    "OCF_FALL_REL": 0.10,
    # A4 capitalization
    "CAPSW_MATERIALITY_PCT": 0.01,
    "CAPRATE_WARN_PP": 0.05,
    "CAPRATE_CRIT_PP": 0.10,
    # A5 restructuring
    "RESTRUCT_MATERIAL_PCT": 0.005,
    "RESTRUCT_WARN_COUNT": 3,
    "RESTRUCT_WARN_WINDOW": 4,
    "RESTRUCT_CRIT_COUNT": 6,
    "RESTRUCT_CRIT_WINDOW": 8,
    "RESTRUCT_CRIT_YEARS": 3,
    # Display-only
    "CAPEX_DA_NOTE": 2.0,
    "CAPEX_DA_NOTE_REL": 0.25,
    "DPO_NOTE_DAYS": 10,
    "DPO_NOTE_REL": 0.20,
    "TAX_RATE_MAX": 0.35,
    "TAX_RATE_DEFAULT": 0.21,
    # §7.2 narrative
    "MDA_10K_MIN_WORDS": 1500,
    "MDA_10Q_MIN_WORDS": 800,
    "SENTENCE_MIN_WORDS": 8,
    "SENTENCE_MAX_WORDS": 80,
    "NEGATION_WINDOW_TOKENS": 6,
    "MAX_QUOTES_PER_CHECK": 2,
    "QUOTE_DISPLAY_MAX_WORDS": 60,
    "HEADER_REPEAT_MAX_WORDS": 12,
    "HEADER_REPEAT_MIN_COUNT": 5,
    "DATA_ROW_NUMERIC_FRAC": 0.40,
    # §7.3 footnotes
    "NOTE_HEADER_MAX_WORDS": 15,
    "NOTE_TITLE_JACCARD": 0.5,
    "NOTE_BODY_COSINE": 0.6,
    "SENTENCE_MATCH_COSINE": 0.55,
    "RESIDUE_MAX_SENTENCES": 400,
    "MATERIAL_MIN_WORDS": 12,
    "MATERIAL_RATIO_MAX": 0.85,
    "HEDGE_MIN_TOKENS": 1500,
    "HEDGE_REL_INC": 0.15,
    "HEDGE_ABS_INC_PER_K": 1.5,
    "HEDGE_MIN_HIT_INC": 5,
    # §7.4 proxy
    "PROXY_ITEM11_MIN_WORDS": 1500,
    "PROXY_MIN_METRICS": 2,
    "PROXY_IMG_LOWCONF": 3,
    "PROXY_MAX_AGE_MONTHS": 18,
    "C1_SBC_REV": 0.10,
    "C1_SBC_FCF": 0.50,
    "C2_GW_ASSETS": 0.35,
    "C2_GW_PP": 0.03,
    # §7.5 scoring
    "PTS_ACCOUNTING_CRIT": 12,
    "PTS_ACCOUNTING_WARN": 5,
    "CAP_ACCOUNTING": 45,
    "PTS_FOOTNOTE": 5,
    "CAP_FOOTNOTES": 20,
    "PTS_INCENTIVE": 5,
    "CAP_INCENTIVES": 10,
    "PTS_HEDGING": 5,
    "CAP_HEDGING": 5,
    "COVERAGE_MIN": 0.60,
    # §6 SEC access
    "SEC_MAX_RPS": 5,
    "SEC_MAX_IN_FLIGHT": 2,
    "SEC_CONNECT_TIMEOUT": 5,
    "SEC_READ_TIMEOUT": 30,
    "SEC_RETRIES": 3,
    "SEC_BREAKER_SECONDS": 600,
    "SEC_MAX_DOC_BYTES": 40 * 1024 * 1024,
    "TTL_TICKERS_H": 24,
    "TTL_SUBMISSIONS_H": 6,
    "TTL_FACTS_H": 24,
    "CACHE_MAX_BYTES": 500 * 1024 * 1024,
    "MODULE_TIMEOUT_S": 30,
    "FILER_LOOKBACK_YEARS": 3,
    "EARNINGS_8K_GRACE_DAYS": 5,
    # §10
    "SEED_MAX_BYTES": 15 * 1024 * 1024,
}


def _load() -> dict[str, float]:
    out: dict[str, float] = {}
    for name, default in _DEFAULTS.items():
        raw = os.environ.get(f"VERITAS_{name}")
        if raw is None or raw.strip() == "":
            out[name] = float(default)
            continue
        try:
            out[name] = float(raw)
        except ValueError:
            logging.getLogger(__name__).warning("Ignoring non-numeric VERITAS_%s=%r", name, raw)
            out[name] = float(default)
    return out


THRESHOLDS: dict[str, float] = _load()


def t(name: str) -> float:
    """Return a threshold value by name. Raises KeyError for unknown names."""
    return THRESHOLDS[name]


def ti(name: str) -> int:
    """Integer-valued threshold."""
    return int(THRESHOLDS[name])


# ---------------------------------------------------------------------------
# Runtime settings (§4.3)
# ---------------------------------------------------------------------------
_PLACEHOLDER_MARKERS = ("yourdomain", "example.com", "@example")


@dataclass(frozen=True)
class Settings:
    user_agent: str | None
    offline: bool
    cache_dir: Path
    log_level: str

    @property
    def live_enabled(self) -> bool:
        return (not self.offline) and self.user_agent is not None


def validate_user_agent(raw: str | None) -> str | None:
    """Return the User-Agent if usable, else None (empty or placeholder)."""
    if raw is None:
        return None
    ua = raw.strip().strip('"').strip("'")
    if not ua:
        return None
    low = ua.lower()
    if any(m in low for m in _PLACEHOLDER_MARKERS):
        return None
    if "@" not in ua:
        return None
    return ua


def get_settings() -> Settings:
    cache_dir = Path(os.environ.get("VERITAS_CACHE_DIR", "./cache"))
    if not cache_dir.is_absolute():
        cache_dir = PROJECT_ROOT / cache_dir
    return Settings(
        user_agent=validate_user_agent(os.environ.get("VERITAS_SEC_USER_AGENT")),
        offline=os.environ.get("VERITAS_OFFLINE", "0").strip() == "1",
        cache_dir=cache_dir,
        log_level=os.environ.get("VERITAS_LOG_LEVEL", "INFO").upper(),
    )


SEED_TICKERS: tuple[str, ...] = ("NVDA", "WMT", "TSLA", "BA")

DISCLAIMER = (
    "Automated screening heuristics derived from public SEC filings. Flags indicate items "
    "warranting further review, not findings of misstatement, fraud, or wrongdoing. "
    "Heuristics can produce false positives; verify against the cited source filings. "
    "Not investment advice. Not audited. Not affiliated with the SEC."
)

SCORE_NAME = "Narrative–Numbers Consistency Score"

FINANCIAL_SCOPE_NOTE = "Financial issuer: limited scope; sector credit metrics not supported in v1."
