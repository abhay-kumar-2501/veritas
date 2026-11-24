"""DataSource abstraction: LiveSource (client + runtime cache) and SeedSource (seed_cache/).

PRD §2.3, §6.5, §10.1. Engines and the pipeline only ever talk to a `DataSource`.
"""

from __future__ import annotations

import gzip
import html
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from veritas.config import SEED_DIR, SEED_TICKERS, get_settings, t, ti
from veritas.edgar.cache import RuntimeCache
from veritas.edgar.client import (
    EdgarError,
    SecBlocked,
    SecClient,
    SecUnavailable,
    decode_html,
)

log = logging.getLogger(__name__)

BANNER_BLOCKED = "SEC access temporarily blocked; showing cached data"
BANNER_STALE = "Stale data: SEC unreachable, showing previously cached data"
BANNER_NO_UA = (
    "Live SEC access is disabled: set VERITAS_SEC_USER_AGENT in .env to "
    '"Your Name your.email@yourdomain.com" (a real contact). Showing seed tickers only.'
)
BANNER_OFFLINE = "Offline mode: showing seed tickers only (no network access)."

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DOCS_KEY = "_veritas_documents"  # added to index.json dicts (see parse_index_headers)


class DataUnavailable(EdgarError):
    """The requested resource is not available from this source (seed miss / no cache)."""


class DataSource(Protocol):
    source: Literal["live", "cache", "seed"]
    banners: list[str]
    fetched_at: datetime | None

    def company_tickers(self) -> dict[str, Any]: ...
    def submissions(self, cik: int) -> dict[str, Any]: ...
    def companyfacts(self, cik: int) -> dict[str, Any] | None: ...
    def document(self, url: str) -> str | None: ...
    def filing_index(self, cik: int, accession: str) -> dict[str, Any] | None: ...


# ---------------------------------------------------------------------------
# URL helpers (PRD §6.1)
# ---------------------------------------------------------------------------
def cik10(cik: int) -> str:
    return f"{int(cik):010d}"


def acc_nodash(accession: str) -> str:
    return accession.replace("-", "")


def submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik10(cik)}.json"


def submissions_page_url(name: str) -> str:
    return f"https://data.sec.gov/submissions/{name}"


def companyfacts_url(cik: int) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10(cik)}.json"


def archive_dir_url(cik: int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash(accession)}"


def document_url(cik: int, accession: str, name: str) -> str:
    return f"{archive_dir_url(cik, accession)}/{name}"


def index_json_url(cik: int, accession: str) -> str:
    return f"{archive_dir_url(cik, accession)}/index.json"


def index_headers_url(cik: int, accession: str) -> str:
    return f"{archive_dir_url(cik, accession)}/{accession}-index-headers.html"


_ARCHIVE_RE = re.compile(r"^https://www\.sec\.gov/Archives/edgar/data/(\d+)/(\d{18})/([^/?#]+)$")


def parse_archive_url(url: str) -> tuple[int, str, str] | None:
    """(cik, accession_nodash, filename) for an Archives document URL."""
    m = _ARCHIVE_RE.match(url)
    if not m:
        return None
    return int(m.group(1)), m.group(2), m.group(3)


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------
_DOC_BLOCK_RE = re.compile(
    r"&lt;DOCUMENT&gt;\s*&lt;TYPE&gt;(?P<type>[^\r\n]*?)\s*\r?\n"
    r"(?:&lt;SEQUENCE&gt;(?P<seq>[^\r\n]*)\r?\n)?"
    r"(?:&lt;FILENAME&gt;(?P<name>[^\r\n]*)\r?\n)?"
    r"(?:&lt;DESCRIPTION&gt;(?P<desc>[^\r\n]*)\r?\n)?"
)


def parse_index_headers(text: str) -> list[dict[str, Any]]:
    """Parse `{acc}-index-headers.html` into [{seq, type, name, description}].

    index.json's own `type` field is an icon name ("text.gif"), not the document type
    (Appendix D item 3), so exhibit types come from this file.
    """
    out: list[dict[str, Any]] = []
    for m in _DOC_BLOCK_RE.finditer(text):
        seq_raw = (m.group("seq") or "").strip()
        out.append(
            {
                "seq": int(seq_raw) if seq_raw.isdigit() else None,
                "type": html.unescape(m.group("type")).strip(),
                "name": html.unescape(m.group("name") or "").strip(),
                "description": html.unescape(m.group("desc") or "").strip(),
            }
        )
    return out


def merge_submission_pages(main: dict[str, Any], pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Return `main` with the columnar arrays of older pages appended to filings.recent."""
    if not pages:
        return main
    merged = json.loads(json.dumps(main))
    recent = merged["filings"]["recent"]
    for page in pages:
        for key, values in page.items():
            if key in recent and isinstance(values, list):
                recent[key] = recent[key] + values
    out: dict[str, Any] = merged
    return out


def pages_needed(main: dict[str, Any], today: date) -> list[str]:
    """Names of older submissions pages that can contain filings within the lookback window."""
    cutoff = (today - timedelta(days=365 * (ti("FILER_LOOKBACK_YEARS") + 1))).isoformat()
    return [
        f["name"]
        for f in main.get("filings", {}).get("files", [])
        if str(f.get("filingTo", "")) >= cutoff
    ]


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# LiveSource
# ---------------------------------------------------------------------------
@dataclass
class _Got:
    status: int
    body: bytes
    fetched_at: float
    truncated: bool = False


class LiveSource:
    """SEC client + SQLite runtime cache. `source` is "live" once any request hit the network,
    otherwise "cache"."""

    def __init__(
        self,
        client: SecClient,
        cache: RuntimeCache,
        banners: list[str] | None = None,
    ) -> None:
        self._client = client
        self._cache = cache
        self.banners: list[str] = list(banners or [])
        self.source: Literal["live", "cache", "seed"] = "cache"
        self.fetched_at: datetime | None = None
        self.doc_status: dict[str, str] = {}  # url -> "ok" | "TRUNCATED" | "unavailable"
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- plumbing -----------------------------------------------------------
    def _banner(self, msg: str) -> None:
        if msg not in self.banners:
            self.banners.append(msg)

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _note_time(self, ts: float) -> None:
        dt = datetime.fromtimestamp(ts, UTC)
        if self.fetched_at is None or dt < self.fetched_at:
            self.fetched_at = dt

    def _fetch(
        self,
        url: str,
        ttl_seconds: float | None,
        lock_key: str,
        *,
        force_refresh: bool = False,
    ) -> _Got:
        with self._lock_for(lock_key):
            if not force_refresh:
                fresh = self._cache.get_fresh(url, ttl_seconds)
                if fresh is not None:
                    self._note_time(fresh.fetched_at)
                    return _Got(fresh.status, fresh.body, fresh.fetched_at)
            stale = self._cache.get(url)
            try:
                res = self._client.get(url)
            except SecBlocked:
                if stale is not None:
                    self._banner(BANNER_BLOCKED)
                    self._note_time(stale.fetched_at)
                    return _Got(stale.status, stale.body, stale.fetched_at)
                raise
            except SecUnavailable:
                if stale is not None:
                    self._banner(BANNER_STALE)
                    self._note_time(stale.fetched_at)
                    return _Got(stale.status, stale.body, stale.fetched_at)
                raise
            self.source = "live"
            if res.truncated:
                self._note_time(_utcnow().timestamp())
                return _Got(res.status, b"", _utcnow().timestamp(), truncated=True)
            now = _utcnow().timestamp()
            self._cache.put(url, res.status, res.body, now)
            self._note_time(now)
            return _Got(res.status, res.body, now)

    def _json(self, url: str, ttl: float | None, lock_key: str) -> Any | None:
        got = self._fetch(url, ttl, lock_key)
        if got.status == 404:
            return None
        return json.loads(got.body.decode("utf-8"))

    # -- API ----------------------------------------------------------------
    def company_tickers(self) -> dict[str, Any]:
        data = self._json(TICKERS_URL, t("TTL_TICKERS_H") * 3600, "tickers")
        if data is None:
            raise DataUnavailable("company_tickers.json returned 404")
        return dict(data)

    def submissions(self, cik: int) -> dict[str, Any]:
        ttl = t("TTL_SUBMISSIONS_H") * 3600
        main = self._json(submissions_url(cik), ttl, f"cik{cik}")
        if main is None:
            raise DataUnavailable(f"No submissions for CIK {cik} (HTTP 404)")
        pages = []
        for name in pages_needed(main, _utcnow().date()):
            page = self._json(submissions_page_url(name), ttl, f"cik{cik}")
            if page is not None:
                pages.append(page)
        return merge_submission_pages(main, pages)

    def _newer_periodic_filing_than(self, cik: int, ts: float) -> bool:
        """True if cached submissions show a 10-K/10-Q filed after `ts` (companyfacts stale)."""
        entry = self._cache.get(submissions_url(cik))
        if entry is None or entry.status != 200:
            return False
        try:
            r = json.loads(entry.body.decode("utf-8"))["filings"]["recent"]
        except (KeyError, ValueError):
            return False
        for i, form in enumerate(r.get("form", [])):
            if form not in ("10-K", "10-KT", "10-Q"):
                continue
            acc = r.get("acceptanceDateTime", [None] * (i + 1))[i]
            try:
                if acc:
                    filed = datetime.fromisoformat(acc.replace("Z", "+00:00")).timestamp()
                else:
                    filed = (
                        datetime.fromisoformat(r["filingDate"][i]).replace(tzinfo=UTC).timestamp()
                    )
            except ValueError:
                continue
            if filed > ts:
                return True
        return False

    def companyfacts(self, cik: int) -> dict[str, Any] | None:
        url = companyfacts_url(cik)
        ttl = t("TTL_FACTS_H") * 3600
        entry = self._cache.get_fresh(url, ttl)
        refresh = entry is not None and self._newer_periodic_filing_than(cik, entry.fetched_at)
        got = self._fetch(url, ttl, f"cik{cik}", force_refresh=refresh)
        if got.status == 404:
            return None
        return dict(json.loads(got.body.decode("utf-8")))

    def document(self, url: str) -> str | None:
        got = self._fetch(url, None, url)
        if got.truncated:
            self.doc_status[url] = "TRUNCATED"
            self._banner(
                f"Document exceeds {ti('SEC_MAX_DOC_BYTES') // (1024 * 1024)} MB "
                f"and was skipped: {url}"
            )
            return None
        if got.status == 404 or not got.body:
            self.doc_status[url] = "unavailable"
            return None
        self.doc_status[url] = "ok"
        return decode_html(got.body)

    def filing_index(self, cik: int, accession: str) -> dict[str, Any] | None:
        got = self._fetch(index_json_url(cik, accession), None, f"cik{cik}")
        if got.status == 404:
            return None
        idx: dict[str, Any] = json.loads(got.body.decode("utf-8"))
        if DOCS_KEY not in idx:
            try:
                h = self._fetch(index_headers_url(cik, accession), None, f"cik{cik}")
            except EdgarError:
                h = None
            if h is not None and h.status == 200 and not h.truncated:
                idx[DOCS_KEY] = parse_index_headers(decode_html(h.body))
        return idx


# ---------------------------------------------------------------------------
# SeedSource
# ---------------------------------------------------------------------------
def _read_gz_json(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


class SeedSource:
    """Reads `seed_cache/` only. Never touches the network."""

    def __init__(self, seed_dir: Path | None = None, banners: list[str] | None = None) -> None:
        self.dir = Path(seed_dir) if seed_dir is not None else SEED_DIR
        self.banners: list[str] = list(banners or [])
        self.source: Literal["live", "cache", "seed"] = "seed"
        self.doc_status: dict[str, str] = {}
        self.manifest: dict[str, Any] = {}
        mf = self.dir / "manifest.json"
        if mf.exists():
            self.manifest = json.loads(mf.read_text(encoding="utf-8"))
        self._by_cik: dict[int, str] = {int(e["cik"]): e["ticker"] for e in self.entries}
        times = [datetime.fromisoformat(e["fetched_at"]) for e in self.entries]
        self.fetched_at: datetime | None = min(times) if times else None

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self.manifest.get("entries", []))

    @property
    def tickers(self) -> list[str]:
        return [e["ticker"] for e in self.entries]

    def _ticker_dir(self, cik: int) -> Path:
        ticker = self._by_cik.get(int(cik))
        if ticker is None:
            raise DataUnavailable(
                f"CIK {cik} is not in the seed cache (seed tickers: {self.tickers})"
            )
        return self.dir / ticker

    def company_tickers(self) -> dict[str, Any]:
        p = self.dir / "company_tickers.json"
        if not p.exists():
            raise DataUnavailable("seed_cache/company_tickers.json is missing")
        return dict(json.loads(p.read_text(encoding="utf-8")))

    def submissions(self, cik: int) -> dict[str, Any]:
        return dict(_read_gz_json(self._ticker_dir(cik) / "submissions.json.gz"))

    def companyfacts(self, cik: int) -> dict[str, Any] | None:
        p = self._ticker_dir(cik) / "companyfacts.json.gz"
        if not p.exists():
            return None
        return dict(_read_gz_json(p))

    def document(self, url: str) -> str | None:
        parsed = parse_archive_url(url)
        if parsed is None:
            return None
        cik, accnd, name = parsed
        if cik not in self._by_cik:
            return None
        p = self.dir / self._by_cik[cik] / "docs" / accnd / f"{name}.gz"
        if not p.exists():
            self.doc_status[url] = "unavailable"
            return None
        self.doc_status[url] = "ok"
        return decode_html(gzip.decompress(p.read_bytes()))

    def filing_index(self, cik: int, accession: str) -> dict[str, Any] | None:
        if int(cik) not in self._by_cik:
            return None
        p = self._ticker_dir(cik) / "index" / f"{acc_nodash(accession)}.json.gz"
        if not p.exists():
            return None
        return dict(_read_gz_json(p))


# ---------------------------------------------------------------------------
# Factory (PRD §2.3)
# ---------------------------------------------------------------------------
def get_source(offline: bool = False, ticker_hint: str | None = None) -> DataSource:
    """Live (with runtime cache) when a valid User-Agent is configured and not offline;
    otherwise the seed cache, with a banner explaining why."""
    s = get_settings()
    if offline or s.offline:
        banners = [BANNER_OFFLINE]
        reason_off = True
    elif s.user_agent is None:
        banners = [BANNER_NO_UA]
        reason_off = True
    else:
        reason_off = False
        banners = []
    if reason_off:
        seed = SeedSource(banners=banners)
        if ticker_hint:
            hint = ticker_hint.strip().upper().replace(".", "-").replace("/", "-")
            if hint not in SEED_TICKERS and hint not in seed.tickers and not hint.startswith("CIK"):
                seed.banners.append(
                    f"{hint} is not available without live SEC access; seed tickers: "
                    f"{', '.join(seed.tickers or SEED_TICKERS)}."
                )
        return seed
    assert s.user_agent is not None
    cache = RuntimeCache(s.cache_dir / "http_cache.sqlite")
    return LiveSource(SecClient(s.user_agent), cache)
