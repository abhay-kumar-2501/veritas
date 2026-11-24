"""Ticker / CIK resolution (PRD §6.3, §2.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from veritas.edgar.client import EdgarError
from veritas.edgar.source import DataSource, DataUnavailable

_CIK_RE = re.compile(r"^CIK[\s:#-]*(\d{1,10})$", re.I)
MAX_SUGGESTIONS = 5


@dataclass(frozen=True)
class CompanyRef:
    cik: int
    ticker: str
    title: str


class TickerNotFound(EdgarError):
    def __init__(self, target: str, suggestions: list[str] | None = None, detail: str = "") -> None:
        self.target = target
        self.suggestions = list(suggestions or [])
        msg = "Ticker not found (delisted/renamed tickers unsupported)"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


def normalize_ticker(raw: str) -> str:
    """Strip, uppercase, replace '.' and '/' with '-' (SEC uses BRK-B)."""
    return raw.strip().upper().replace(".", "-").replace("/", "-")


def _entries(tickers: dict[str, Any]) -> list[dict[str, Any]]:
    return [v for v in tickers.values() if isinstance(v, dict) and "ticker" in v]


def suggest(prefix: str, entries: list[dict[str, Any]], limit: int = MAX_SUGGESTIONS) -> list[str]:
    """Up to `limit` tickers starting with `prefix`; the prefix is shortened (min 2 chars)
    until at least one match exists."""
    tick = sorted({str(e["ticker"]).upper() for e in entries})
    p = prefix
    while len(p) >= 2:
        hits = [x for x in tick if x.startswith(p)]
        if hits:
            return hits[:limit]
        p = p[:-1]
    return []


def resolve(target: str, ds: DataSource) -> CompanyRef:
    """Resolve a ticker (`BRK.B`, `brk-b`) or `CIK 320193` to a CompanyRef."""
    raw = (target or "").strip()
    if not raw:
        raise TickerNotFound(target, detail="empty input")
    entries = _entries(ds.company_tickers())

    m = _CIK_RE.match(raw)
    if m:
        cik = int(m.group(1))
        match = [e for e in entries if int(e["cik_str"]) == cik]
        if match:
            return CompanyRef(cik, str(match[0]["ticker"]).upper(), str(match[0]["title"]))
        try:
            sub = ds.submissions(cik)
        except DataUnavailable as exc:
            raise TickerNotFound(target, detail=f"CIK {cik} not found") from exc
        tickers = sub.get("tickers") or []
        if not tickers:
            raise TickerNotFound(target, detail=f"CIK {cik} has no listed ticker")
        return CompanyRef(cik, str(tickers[0]).upper(), str(sub.get("name", "")))

    norm = normalize_ticker(raw)
    for e in entries:
        if str(e["ticker"]).upper() == norm:
            return CompanyRef(int(e["cik_str"]), norm, str(e["title"]))
    raise TickerNotFound(target, suggest(norm, entries))
