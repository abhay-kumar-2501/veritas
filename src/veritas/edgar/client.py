"""requests-based SEC client (PRD §6.1, §6.2). Only www.sec.gov and data.sec.gov."""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from veritas.config import t, ti
from veritas.edgar.ratelimit import RateLimiter, get_limiter

log = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
_BACKOFFS = (1.0, 2.0, 4.0)
_RETRY_STATUSES = frozenset({429}) | frozenset(range(500, 600))


class EdgarError(Exception):
    """Base class for EDGAR data-layer errors."""


class SecBlocked(EdgarError):
    """HTTP 403 received, or the 10-minute circuit breaker is open."""


class SecUnavailable(EdgarError):
    """429/5xx/connection errors persisted after all retries."""


class SecHTTPError(EdgarError):
    """Unexpected non-retryable HTTP status (e.g. 400)."""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url


class CircuitBreaker:
    """Process-global breaker opened by a 403 (PRD §6.2)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._open_until = 0.0

    def trip(self, seconds: float | None = None) -> None:
        with self._lock:
            self._open_until = self._clock() + (
                seconds if seconds is not None else t("SEC_BREAKER_SECONDS")
            )

    def is_open(self) -> bool:
        with self._lock:
            return self._clock() < self._open_until

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self._open_until - self._clock())

    def reset(self) -> None:
        with self._lock:
            self._open_until = 0.0


_BREAKER = CircuitBreaker()


def get_breaker() -> CircuitBreaker:
    return _BREAKER


@dataclass(frozen=True)
class FetchResult:
    status: int  # 200 or 404 (other statuses raise)
    body: bytes
    truncated: bool = False  # True when the 40 MB cap was hit; body is then empty

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def assert_allowed_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme != "https" or p.hostname not in ALLOWED_HOSTS:
        raise AssertionError(f"Refusing non-SEC URL (PRD §0.1.3): {url}")


def decode_html(body: bytes) -> str:
    """Decode filing bytes: UTF-8 first, then cp1252 (legacy filings), never raising."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


class SecClient:
    def __init__(
        self,
        user_agent: str,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0.0, 0.5),
    ) -> None:
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._session = session if session is not None else requests.Session()
        self._limiter = limiter if limiter is not None else get_limiter()
        self._breaker = breaker if breaker is not None else _BREAKER
        self._sleep = sleep
        self._jitter = jitter
        self.request_count = 0

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def get(self, url: str, *, max_bytes: int | None = None) -> FetchResult:
        """GET with retry policy. Returns 200/404 results; raises otherwise."""
        assert_allowed_url(url)
        cap = max_bytes if max_bytes is not None else ti("SEC_MAX_DOC_BYTES")
        attempts = ti("SEC_RETRIES")
        last_error: str = "unknown"
        for attempt in range(attempts):
            if self._breaker.is_open():
                raise SecBlocked(
                    "SEC access temporarily blocked "
                    f"(circuit open {self._breaker.remaining():.0f}s more)"
                )
            retry_after: float | None = None
            try:
                with self._limiter.slot():
                    self.request_count += 1
                    resp = self._session.get(
                        url,
                        headers=self._headers,
                        timeout=(t("SEC_CONNECT_TIMEOUT"), t("SEC_READ_TIMEOUT")),
                        stream=True,
                    )
                    try:
                        status = resp.status_code
                        if status == 403:
                            self._breaker.trip()
                            raise SecBlocked(f"HTTP 403 from SEC for {url}; circuit breaker opened")
                        if status == 200:
                            return self._read(resp, cap)
                        if status == 404:
                            return FetchResult(404, b"")
                        if status in _RETRY_STATUSES:
                            last_error = f"HTTP {status}"
                            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                        else:
                            raise SecHTTPError(status, url)
                    finally:
                        resp.close()
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = type(exc).__name__
            if attempt < attempts - 1:
                delay = _BACKOFFS[min(attempt, len(_BACKOFFS) - 1)] + self._jitter()
                if retry_after is not None:
                    delay = max(delay, retry_after)
                log.warning(
                    "SEC fetch retry %d for %s (%s) in %.1fs", attempt + 1, url, last_error, delay
                )
                self._sleep(delay)
        raise SecUnavailable(f"SEC request failed after {attempts} attempts ({last_error}): {url}")

    @staticmethod
    def _read(resp: requests.Response, cap: int) -> FetchResult:
        chunks: list[bytes] = []
        size = 0
        for chunk in resp.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > cap:
                return FetchResult(200, b"", truncated=True)
            chunks.append(chunk)
        return FetchResult(200, b"".join(chunks))

    def get_json(self, url: str) -> tuple[int, Any]:
        """(status, parsed JSON) for 200; (404, None) for 404."""
        r = self.get(url, max_bytes=ti("SEC_MAX_DOC_BYTES"))
        if r.status == 404:
            return 404, None
        return r.status, r.json()


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None  # HTTP-date form is not honored; backoff applies
