"""SQLite runtime cache (PRD §6.5b).

Tables: `http_cache(url, fetched_at, status, body gzip BLOB, last_access)` and
`derived(key, created_at, body)`. A new connection is opened per call (thread safe),
WAL mode, busy_timeout=5000. LRU eviction above CACHE_MAX_BYTES.
"""

from __future__ import annotations

import gzip
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from veritas.config import ti

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache(
    url TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    status INTEGER NOT NULL,
    body BLOB NOT NULL,
    last_access REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS derived(
    key TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    body BLOB NOT NULL,
    last_access REAL NOT NULL
);
"""


@dataclass(frozen=True)
class CacheEntry:
    url: str
    fetched_at: float  # unix seconds
    status: int
    body: bytes  # decompressed

    def age(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) - self.fetched_at


class RuntimeCache:
    def __init__(self, path: Path, max_bytes: int | None = None) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes if max_bytes is not None else ti("CACHE_MAX_BYTES")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5.0)
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    # -- http_cache ---------------------------------------------------------
    def get(self, url: str) -> CacheEntry | None:
        """Return the entry regardless of age (stale-on-error callers apply TTL themselves)."""
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT fetched_at, status, body FROM http_cache WHERE url=?", (url,)
            ).fetchone()
            if row is None:
                return None
            con.execute("UPDATE http_cache SET last_access=? WHERE url=?", (time.time(), url))
            con.commit()
        return CacheEntry(url, row[0], row[1], gzip.decompress(row[2]))

    def get_fresh(
        self, url: str, ttl_seconds: float | None, now: float | None = None
    ) -> CacheEntry | None:
        """Entry if younger than the TTL; `ttl_seconds=None` means it never expires."""
        entry = self.get(url)
        if entry is None:
            return None
        if ttl_seconds is None or entry.age(now) <= ttl_seconds:
            return entry
        return None

    def put(self, url: str, status: int, body: bytes, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        blob = gzip.compress(body, compresslevel=6)
        with closing(self._connect()) as con:
            con.execute(
                "INSERT OR REPLACE INTO http_cache(url, fetched_at, status, body, last_access) "
                "VALUES (?,?,?,?,?)",
                (url, ts, status, blob, ts),
            )
            con.commit()
        self.evict()

    # -- derived ------------------------------------------------------------
    def get_derived(self, key: str) -> bytes | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT body FROM derived WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            con.execute("UPDATE derived SET last_access=? WHERE key=?", (time.time(), key))
            con.commit()
        return gzip.decompress(row[0])

    def put_derived(self, key: str, body: bytes) -> None:
        ts = time.time()
        with closing(self._connect()) as con:
            con.execute(
                "INSERT OR REPLACE INTO derived(key, created_at, body, last_access) "
                "VALUES (?,?,?,?)",
                (key, ts, gzip.compress(body, compresslevel=6), ts),
            )
            con.commit()
        self.evict()

    # -- eviction -----------------------------------------------------------
    def total_bytes(self) -> int:
        with closing(self._connect()) as con:
            a = con.execute("SELECT COALESCE(SUM(LENGTH(body)),0) FROM http_cache").fetchone()[0]
            b = con.execute("SELECT COALESCE(SUM(LENGTH(body)),0) FROM derived").fetchone()[0]
        return int(a) + int(b)

    def evict(self) -> int:
        """Delete least-recently-used rows until stored bytes <= max_bytes. Returns rows deleted."""
        deleted = 0
        with closing(self._connect()) as con:
            while True:
                total = (
                    con.execute("SELECT COALESCE(SUM(LENGTH(body)),0) FROM http_cache").fetchone()[
                        0
                    ]
                    + con.execute("SELECT COALESCE(SUM(LENGTH(body)),0) FROM derived").fetchone()[0]
                )
                if total <= self.max_bytes:
                    break
                row = con.execute(
                    "SELECT 'http_cache', url, last_access FROM http_cache "
                    "UNION ALL SELECT 'derived', key, last_access FROM derived "
                    "ORDER BY last_access ASC LIMIT 1"
                ).fetchone()
                if row is None:
                    break
                col = "url" if row[0] == "http_cache" else "key"
                con.execute(f"DELETE FROM {row[0]} WHERE {col}=?", (row[1],))  # noqa: S608
                con.commit()
                deleted += 1
        return deleted
