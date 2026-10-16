"""Thread-safe token-bucket rate limiter (SEC allows at most 10 requests/second)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

DEFAULT_RATE = 8.0


class TokenBucket:
    """Allows ``rate`` acquisitions per second with bursts of up to ``capacity``.

    The default capacity of 1 spaces requests evenly, so no one-second window ever sees more
    than ``rate`` + 1 requests (a full bucket of N plus N refills would allow ~2N).
    """

    def __init__(
        self,
        rate: float,
        *,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = capacity if capacity is not None else 1.0
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.capacity
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._updated = now

    def acquire(self) -> float:
        """Take one token, sleeping until one is available. Returns the seconds slept."""
        with self._lock:
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0
            wait = (1 - self._tokens) / self.rate
            self._sleep(wait)
            self._refill()
            self._tokens = max(0.0, self._tokens - 1)
            return wait


_process_limiter: TokenBucket | None = None
_process_lock = threading.Lock()


def process_limiter(rate: float) -> TokenBucket:
    """The limiter shared by every client in this process (created on first use)."""
    global _process_limiter
    with _process_lock:
        if _process_limiter is None:
            _process_limiter = TokenBucket(rate)
        return _process_limiter
