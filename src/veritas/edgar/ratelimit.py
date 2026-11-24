"""Process-global, thread-safe rate limiter (PRD §6.2).

A token bucket (<= SEC_MAX_RPS requests per second) combined with a semaphore
(<= SEC_MAX_IN_FLIGHT concurrent requests). The SEC cap is 10 req/s per IP; we
stay at half of it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from veritas.config import t, ti


class TokenBucket:
    """Classic token bucket. `capacity == rate` so bursts never exceed one second of budget."""

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else rate)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.capacity
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until one token is available. Returns the seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0 - 1e-9:  # tolerance: float drift must not spin forever
                    self._tokens = max(0.0, self._tokens - 1.0)
                    return waited
                need = (1.0 - self._tokens) / self.rate
            self._sleep(need)
            waited += need


class RateLimiter:
    """Token bucket plus an in-flight cap. Use `with limiter.slot(): do_request()`."""

    def __init__(
        self,
        max_rps: float | None = None,
        max_in_flight: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bucket = TokenBucket(
            max_rps if max_rps is not None else t("SEC_MAX_RPS"), None, clock, sleep
        )
        self.max_in_flight = max_in_flight if max_in_flight is not None else ti("SEC_MAX_IN_FLIGHT")
        self._sem = threading.BoundedSemaphore(self.max_in_flight)

    @contextmanager
    def slot(self) -> Iterator[None]:
        self._sem.acquire()
        try:
            self.bucket.acquire()
            yield
        finally:
            self._sem.release()


_GLOBAL: RateLimiter | None = None
_GLOBAL_LOCK = threading.Lock()


def get_limiter() -> RateLimiter:
    """The single process-wide limiter shared by every client instance."""
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            _GLOBAL = RateLimiter()
        return _GLOBAL
