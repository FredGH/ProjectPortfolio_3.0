"""A per-source token-bucket rate limiter, driven by config/sources.yml
(PLAN.md Step 3) — the runner owns one of these per connector so a
connector's own code never has to think about pacing itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    """A simple token-bucket limiter: `capacity` calls per `refill_period_seconds`.

    Attributes:
        capacity: Maximum tokens the bucket can hold (and the number of
            calls allowed in one refill period before blocking).
        refill_period_seconds: How long a full refill takes.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_period_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialise the bucket, full.

        Args:
            capacity: Maximum tokens (and calls per period).
            refill_period_seconds: Seconds for the bucket to fully refill
                from empty.
            clock: Injectable monotonic clock, for deterministic tests.
            sleep: Injectable sleep function, for deterministic tests.
        """
        self.capacity = capacity
        self.refill_period_seconds = refill_period_seconds
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(capacity)
        self._last_refill = clock()

    def _refill(self) -> None:
        """Add tokens proportional to elapsed time, capped at capacity."""
        now = self._clock()
        elapsed = now - self._last_refill
        self._last_refill = now
        rate = self.capacity / self.refill_period_seconds
        self._tokens = min(self.capacity, self._tokens + elapsed * rate)

    def acquire(self) -> None:
        """Consume one token, sleeping first if none are available."""
        self._refill()
        if self._tokens < 1:
            rate = self.capacity / self.refill_period_seconds
            wait_seconds = (1 - self._tokens) / rate
            self._sleep(wait_seconds)
            self._refill()
        self._tokens -= 1
