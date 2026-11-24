"""Rate limiter, retry/backoff, circuit breaker, cache and LiveSource (PRD §6.2, §6.5)."""

from __future__ import annotations

import gzip
import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import requests

from veritas.config import t
from veritas.edgar import client as client_mod
from veritas.edgar.cache import RuntimeCache
from veritas.edgar.client import (
    CircuitBreaker,
    SecBlocked,
    SecClient,
    SecHTTPError,
    SecUnavailable,
    assert_allowed_url,
    decode_html,
)
from veritas.edgar.ratelimit import RateLimiter, TokenBucket
from veritas.edgar.source import BANNER_BLOCKED, BANNER_STALE, LiveSource, SeedSource, get_source

UA = "Test Suite test@testsuite.org"
SUB_URL = "https://data.sec.gov/submissions/CIK0000000001.json"


class FakeResp:
    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size: int = 65536) -> Iterator[bytes]:
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kw: Any) -> FakeResp:
        self.calls.append({"url": url, **kw})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, FakeResp)
        return item


def make_client(
    script: list[Any], sleeps: list[float] | None = None
) -> tuple[SecClient, FakeSession]:
    sess = FakeSession(script)
    log = sleeps if sleeps is not None else []
    c = SecClient(
        UA,
        session=sess,  # type: ignore[arg-type]
        limiter=RateLimiter(max_rps=1000, max_in_flight=2),
        breaker=CircuitBreaker(),
        sleep=log.append,
        jitter=lambda: 0.0,
    )
    return c, sess


# ---------------------------------------------------------------------------
# rate limiter
# ---------------------------------------------------------------------------
def test_token_bucket_limits_to_five_per_second() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        now[0] += s

    b = TokenBucket(5, clock=lambda: now[0], sleep=sleep)
    for _ in range(5):  # initial burst of one second of budget
        b.acquire()
    assert sleeps == []
    for _ in range(10):
        b.acquire()
    assert now[0] == pytest.approx(2.0)  # 10 more tokens at 5/s
    assert t("SEC_MAX_RPS") <= 5


def test_limiter_caps_in_flight_at_two() -> None:
    lim = RateLimiter(max_rps=10000, max_in_flight=2)
    assert lim.max_in_flight <= 2
    active = 0
    peak = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal active, peak
        with lim.slot():
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for th in ts:
        th.start()
    for th in ts:
        th.join()
    assert peak == 2


def test_default_limiter_respects_config() -> None:
    lim = RateLimiter()
    assert lim.bucket.rate == t("SEC_MAX_RPS") <= 5
    assert lim.max_in_flight == int(t("SEC_MAX_IN_FLIGHT")) <= 2


# ---------------------------------------------------------------------------
# client: headers, hosts, retry policy
# ---------------------------------------------------------------------------
def test_headers_timeouts_and_stream() -> None:
    c, sess = make_client([FakeResp(200, b"{}")])
    c.get(SUB_URL)
    call = sess.calls[0]
    assert call["headers"] == {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
    assert call["timeout"] == (5.0, 30.0)
    assert call["stream"] is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/x",
        "http://www.sec.gov/files/company_tickers.json",
        "https://sec.gov/files/x.json",
        "https://www.sec.gov.evil.com/x",
        "https://api.openai.com/v1",
    ],
)
def test_only_sec_hosts_allowed(url: str) -> None:
    with pytest.raises(AssertionError):
        assert_allowed_url(url)
    c, sess = make_client([])
    with pytest.raises(AssertionError):
        c.get(url)
    assert sess.calls == []


def test_retry_on_429_then_success_honors_retry_after() -> None:
    sleeps: list[float] = []
    c, sess = make_client(
        [FakeResp(429, headers={"Retry-After": "7"}), FakeResp(503), FakeResp(200, b"ok")], sleeps
    )
    r = c.get(SUB_URL)
    assert r.status == 200 and r.body == b"ok"
    assert len(sess.calls) == 3
    assert sleeps == [7.0, 2.0]  # Retry-After beats 1 s; second backoff is 2 s


def test_backoff_schedule_and_exhaustion() -> None:
    sleeps: list[float] = []
    c, sess = make_client([FakeResp(500), FakeResp(502), FakeResp(504)], sleeps)
    with pytest.raises(SecUnavailable):
        c.get(SUB_URL)
    assert len(sess.calls) == 3  # 3 attempts
    assert sleeps == [1.0, 2.0]  # no sleep after the final attempt


def test_connection_errors_are_retried() -> None:
    sleeps: list[float] = []
    c, sess = make_client(
        [requests.ConnectionError("boom"), requests.Timeout("slow"), FakeResp(200, b"x")], sleeps
    )
    assert c.get(SUB_URL).body == b"x"
    assert len(sess.calls) == 3


def test_other_4xx_not_retried() -> None:
    c, sess = make_client([FakeResp(400)])
    with pytest.raises(SecHTTPError):
        c.get(SUB_URL)
    assert len(sess.calls) == 1


def test_404_returned_not_raised_and_not_retried() -> None:
    c, sess = make_client([FakeResp(404)])
    r = c.get(SUB_URL)
    assert r.status == 404 and r.body == b""
    assert len(sess.calls) == 1


def test_403_opens_breaker_and_is_not_retried() -> None:
    c, sess = make_client([FakeResp(403), FakeResp(200, b"never")])
    with pytest.raises(SecBlocked):
        c.get(SUB_URL)
    assert len(sess.calls) == 1
    assert c.breaker.is_open()
    assert c.breaker.remaining() > 590  # 10-minute breaker (SEC_BREAKER_SECONDS=600)
    with pytest.raises(SecBlocked):  # next call is refused locally: no new request
        c.get(SUB_URL)
    assert len(sess.calls) == 1


def test_breaker_closes_after_window() -> None:
    now = [0.0]
    b = CircuitBreaker(clock=lambda: now[0])
    b.trip(600)
    assert b.is_open()
    now[0] = 601
    assert not b.is_open()


def test_process_global_breaker_shared_by_default() -> None:
    assert client_mod.get_breaker() is SecClient(UA, session=FakeSession([])).breaker  # type: ignore[arg-type]


def test_stream_aborts_above_cap_as_truncated() -> None:
    c, _ = make_client([FakeResp(200, b"a" * 100_000)])
    r = c.get(
        "https://www.sec.gov/Archives/edgar/data/1/000000000100000001/x.htm", max_bytes=50_000
    )
    assert r.truncated and r.body == b""


def test_default_doc_cap_is_40mb() -> None:
    assert t("SEC_MAX_DOC_BYTES") == 40 * 1024 * 1024


def test_decode_html_fallback() -> None:
    assert decode_html("café".encode()) == "café"
    assert decode_html("café".encode("cp1252")) == "café"


# ---------------------------------------------------------------------------
# runtime cache
# ---------------------------------------------------------------------------
def test_cache_roundtrip_gzip_ttl_and_wal(tmp_path: Path) -> None:
    c = RuntimeCache(tmp_path / "c.sqlite")
    c.put("u", 200, b"hello" * 100, now=1000.0)
    e = c.get("u")
    assert e is not None and e.body == b"hello" * 100 and e.status == 200
    assert c.get_fresh("u", 60, now=1030.0) is not None
    assert c.get_fresh("u", 60, now=1100.0) is None  # expired
    assert c.get_fresh("u", None, now=9e9) is not None  # never expires
    import sqlite3

    con = sqlite3.connect(tmp_path / "c.sqlite")
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    raw = con.execute("SELECT body FROM http_cache").fetchone()[0]
    assert gzip.decompress(raw) == b"hello" * 100
    con.close()


def test_cache_derived_and_lru_eviction(tmp_path: Path) -> None:
    c = RuntimeCache(tmp_path / "c.sqlite", max_bytes=3000)
    import os

    for i in range(10):
        c.put(f"u{i}", 200, os.urandom(800), now=1000.0 + i)  # incompressible
        time.sleep(0.002)
    assert c.total_bytes() <= 3000
    assert c.get("u9") is not None  # newest kept
    assert c.get("u0") is None  # oldest evicted
    c.put_derived("k", b"derived")
    assert c.get_derived("k") == b"derived"


# ---------------------------------------------------------------------------
# LiveSource: caching, stale-on-error, 404, breaker
# ---------------------------------------------------------------------------
class ScriptedClient:
    """Stands in for SecClient inside LiveSource."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.urls: list[str] = []

    def get(self, url: str, **_: Any) -> client_mod.FetchResult:
        self.urls.append(url)
        out = self.handler(url)
        if isinstance(out, Exception):
            raise out
        assert isinstance(out, client_mod.FetchResult)
        return out


def ok(obj: Any) -> client_mod.FetchResult:
    return client_mod.FetchResult(200, json.dumps(obj).encode())


def sub_payload() -> dict[str, Any]:
    return {"name": "X", "filings": {"recent": {"form": []}, "files": []}}


def test_live_source_caches_then_serves_from_cache(tmp_path: Path) -> None:
    sc = ScriptedClient(lambda u: ok(sub_payload()))
    ds = LiveSource(sc, RuntimeCache(tmp_path / "c.sqlite"))  # type: ignore[arg-type]
    assert ds.submissions(1)["name"] == "X"
    assert ds.source == "live" and ds.fetched_at is not None
    ds2 = LiveSource(sc, RuntimeCache(tmp_path / "c.sqlite"))  # type: ignore[arg-type]
    assert ds2.submissions(1)["name"] == "X"
    assert ds2.source == "cache"
    assert len(sc.urls) == 1


def test_ttl_expiry_triggers_refetch(tmp_path: Path) -> None:
    cache = RuntimeCache(tmp_path / "c.sqlite")
    sc = ScriptedClient(lambda u: ok(sub_payload()))
    old = time.time() - 7 * 3600  # older than 6 h submissions TTL
    cache.put(SUB_URL, 200, json.dumps(sub_payload()).encode(), now=old)
    ds = LiveSource(sc, cache)  # type: ignore[arg-type]
    ds.submissions(1)
    assert sc.urls == [SUB_URL]


def test_stale_on_error_serves_expired_entry_with_banner(tmp_path: Path) -> None:
    cache = RuntimeCache(tmp_path / "c.sqlite")
    cache.put(SUB_URL, 200, json.dumps(sub_payload()).encode(), now=time.time() - 30 * 3600)
    sc = ScriptedClient(lambda u: SecUnavailable("down"))
    ds = LiveSource(sc, cache)  # type: ignore[arg-type]
    assert ds.submissions(1)["name"] == "X"
    assert BANNER_STALE in ds.banners
    assert ds.fetched_at is not None and ds.fetched_at < datetime.now(UTC)


def test_blocked_serves_cache_with_banner_and_raises_without_cache(tmp_path: Path) -> None:
    cache = RuntimeCache(tmp_path / "c.sqlite")
    cache.put(SUB_URL, 200, json.dumps(sub_payload()).encode(), now=time.time() - 30 * 3600)
    ds = LiveSource(ScriptedClient(lambda u: SecBlocked("403")), cache)  # type: ignore[arg-type]
    assert ds.submissions(1)["name"] == "X"
    assert BANNER_BLOCKED in ds.banners
    empty = LiveSource(
        ScriptedClient(lambda u: SecBlocked("403")),  # type: ignore[arg-type]
        RuntimeCache(tmp_path / "other.sqlite"),
    )
    with pytest.raises(SecBlocked):
        empty.submissions(1)


def test_companyfacts_404_is_none_and_cached(tmp_path: Path) -> None:
    sc = ScriptedClient(lambda u: client_mod.FetchResult(404, b""))
    ds = LiveSource(sc, RuntimeCache(tmp_path / "c.sqlite"))  # type: ignore[arg-type]
    assert ds.companyfacts(1) is None
    assert ds.companyfacts(1) is None
    assert len(sc.urls) == 1


def test_document_none_when_missing_or_truncated(tmp_path: Path) -> None:
    url_missing = "https://www.sec.gov/Archives/edgar/data/1/000000000100000001/a.htm"
    url_big = "https://www.sec.gov/Archives/edgar/data/1/000000000100000001/b.htm"

    def handler(u: str) -> client_mod.FetchResult:
        if u == url_missing:
            return client_mod.FetchResult(404, b"")
        return client_mod.FetchResult(200, b"", truncated=True)

    ds = LiveSource(ScriptedClient(handler), RuntimeCache(tmp_path / "c.sqlite"))  # type: ignore[arg-type]
    assert ds.document(url_missing) is None
    assert ds.document(url_big) is None
    assert ds.doc_status[url_big] == "TRUNCATED" and ds.doc_status[url_missing] == "unavailable"
    assert any("exceeds" in b for b in ds.banners)


def test_document_cached_forever(tmp_path: Path) -> None:
    url = "https://www.sec.gov/Archives/edgar/data/1/000000000100000001/a.htm"
    cache = RuntimeCache(tmp_path / "c.sqlite")
    cache.put(url, 200, b"<html>hi</html>", now=1.0)  # ancient
    sc = ScriptedClient(lambda u: pytest.fail("must not fetch"))
    assert LiveSource(sc, cache).document(url) == "<html>hi</html>"  # type: ignore[arg-type]


def test_submissions_merges_older_pages(tmp_path: Path) -> None:
    today = datetime.now(UTC).date().isoformat()
    main = {
        "name": "X",
        "filings": {
            "recent": {"form": ["10-K"], "accessionNumber": ["a1"]},
            "files": [{"name": "CIK0000000001-submissions-001.json", "filingTo": today}],
        },
    }
    page = {"form": ["10-Q"], "accessionNumber": ["a0"]}

    def handler(u: str) -> client_mod.FetchResult:
        return ok(page if "submissions-001" in u else main)

    ds = LiveSource(ScriptedClient(handler), RuntimeCache(tmp_path / "c.sqlite"))  # type: ignore[arg-type]
    rec = ds.submissions(1)["filings"]["recent"]
    assert rec["form"] == ["10-K", "10-Q"] and rec["accessionNumber"] == ["a1", "a0"]


def test_old_pages_outside_lookback_not_fetched(tmp_path: Path) -> None:
    main = {
        "name": "X",
        "filings": {
            "recent": {"form": []},
            "files": [{"name": "old.json", "filingTo": "2001-01-01"}],
        },
    }
    sc = ScriptedClient(lambda u: ok(main))
    LiveSource(sc, RuntimeCache(tmp_path / "c.sqlite")).submissions(1)  # type: ignore[arg-type]
    assert sc.urls == [SUB_URL]


# ---------------------------------------------------------------------------
# get_source (PRD §2.3)
# ---------------------------------------------------------------------------
def test_get_source_without_user_agent_is_seed_with_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERITAS_SEC_USER_AGENT", "")
    ds = get_source(offline=False, ticker_hint="AAPL")
    assert isinstance(ds, SeedSource) and ds.source == "seed"
    assert any("VERITAS_SEC_USER_AGENT" in b for b in ds.banners)
    assert any("AAPL" in b for b in ds.banners)


def test_get_source_placeholder_user_agent_is_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERITAS_SEC_USER_AGENT", "Your Name you@yourdomain.com")
    assert isinstance(get_source(False, None), SeedSource)


def test_get_source_offline_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERITAS_SEC_USER_AGENT", UA)
    ds = get_source(offline=True, ticker_hint="NVDA")
    assert isinstance(ds, SeedSource)
    assert not any("NVDA" in b for b in ds.banners)


def test_get_source_live_with_valid_user_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VERITAS_SEC_USER_AGENT", UA)
    monkeypatch.setenv("VERITAS_OFFLINE", "0")
    monkeypatch.setenv("VERITAS_CACHE_DIR", str(tmp_path))
    ds = get_source(offline=False, ticker_hint=None)
    assert isinstance(ds, LiveSource)
    assert ds.source == "cache"
