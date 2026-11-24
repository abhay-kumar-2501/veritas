"""Command-bar grammar (PRD §8.3) as a pure parser.

`parse_command` never touches Streamlit; `layout.apply_command` maps the
resulting `Command` onto session state. Matching is case-insensitive and
whitespace-trimmed. Keywords win over the bare-ticker rule, so `DIFF` is the
command, never a ticker lookup (docs/decisions/ui.md D-605).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

CommandKind = Literal["analyze", "cik", "view", "contra", "export", "empty", "invalid"]

VIEW_OVERVIEW = "[1] Overview"
VIEW_REDLINE = "[2] Redline"
VIEW_RATIOS = "[3] Ratios"
VIEW_PROXY = "[4] Proxy"
VIEW_LABELS: tuple[str, ...] = (VIEW_OVERVIEW, VIEW_REDLINE, VIEW_RATIOS, VIEW_PROXY)

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-/]{0,9}$")
_ANALYZE_RE = re.compile(r"^ANALYZE\s+(\S+)$")
_CIK_RE = re.compile(r"^CIK\s*(\d{1,10})$")

_VIEW_KEYWORDS: dict[str, str] = {
    "DIFF": VIEW_REDLINE,
    "RATIOS": VIEW_RATIOS,
    "PROXY": VIEW_PROXY,
    "OVERVIEW": VIEW_OVERVIEW,
}

VALID_COMMANDS: tuple[str, ...] = (
    "ANALYZE <TICKER>",
    "<TICKER>",
    "CIK <digits>",
    "DIFF",
    "CONTRA",
    "RATIOS",
    "PROXY",
    "OVERVIEW",
    "EXPORT",
)


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    arg: str | None = None  # ticker, CIK digits (no leading zeros), or view label
    raw: str = ""
    error: str | None = None

    @property
    def runs_analysis(self) -> bool:
        return self.kind in ("analyze", "cik")


def _invalid(raw: str, message: str) -> Command:
    return Command(kind="invalid", raw=raw, error=message)


def parse_command(raw: str) -> Command:
    """Parse one command-bar submission. Pure and total: never raises."""
    text = " ".join((raw or "").split())  # trim + collapse inner whitespace
    if not text:
        return Command(kind="empty", raw=raw or "")
    up = text.upper()

    if up in _VIEW_KEYWORDS:
        return Command(kind="view", arg=_VIEW_KEYWORDS[up], raw=text)
    if up == "CONTRA":
        return Command(kind="contra", arg=VIEW_OVERVIEW, raw=text)
    if up == "EXPORT":
        return Command(kind="export", raw=text)

    m = _CIK_RE.match(up)
    if m:
        digits = m.group(1).lstrip("0")
        if not digits:
            return _invalid(text, "CIK must be a positive number, e.g. CIK 320193.")
        return Command(kind="cik", arg=digits, raw=text)
    if up == "CIK" or up.startswith("CIK "):
        return _invalid(text, "CIK takes digits only, e.g. CIK 320193.")

    m = _ANALYZE_RE.match(up)
    if m:
        ticker = m.group(1)
        if TICKER_RE.match(ticker):
            return Command(kind="analyze", arg=ticker, raw=text)
        return _invalid(text, f"“{ticker}” is not a valid ticker symbol.")
    if up == "ANALYZE":
        return _invalid(text, "ANALYZE needs a ticker, e.g. ANALYZE NVDA.")

    if TICKER_RE.match(up):
        return Command(kind="analyze", arg=up, raw=text)

    return _invalid(text, f"Unknown command “{text}”.")
