from __future__ import annotations

import unittest

from core.ingestion.rate_limiter import TokenBucket


class _FakeClock:
    """A controllable clock so tests never depend on real elapsed time."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward."""
        self.now += seconds


class TestTokenBucket(unittest.TestCase):
    """Tests for TokenBucket's capacity, refill, and blocking behaviour."""

    def test_allows_calls_up_to_capacity_without_sleeping(self) -> None:
        """The first `capacity` acquires never sleep."""
        clock = _FakeClock()
        sleeps: list[float] = []
        bucket = TokenBucket(
            capacity=3, refill_period_seconds=60.0, clock=clock, sleep=sleeps.append
        )
        bucket.acquire()
        bucket.acquire()
        bucket.acquire()
        self.assertEqual(sleeps, [])

    def test_blocks_once_capacity_is_exhausted(self) -> None:
        """The (capacity + 1)th acquire sleeps until a token refills."""
        clock = _FakeClock()
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock.advance(seconds)

        bucket = TokenBucket(
            capacity=1, refill_period_seconds=60.0, clock=clock, sleep=fake_sleep
        )
        bucket.acquire()
        bucket.acquire()
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0)

    def test_refills_after_the_period_elapses(self) -> None:
        """Advancing the clock past refill_period_seconds allows another
        acquire without sleeping."""
        clock = _FakeClock()
        sleeps: list[float] = []
        bucket = TokenBucket(
            capacity=1, refill_period_seconds=60.0, clock=clock, sleep=sleeps.append
        )
        bucket.acquire()
        clock.advance(61.0)
        bucket.acquire()
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
