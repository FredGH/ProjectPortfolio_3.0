from __future__ import annotations

import unittest

from core.ingestion.retry import retry_with_backoff


class TestRetryWithBackoff(unittest.TestCase):
    """Tests for retry_with_backoff's retry count, backoff, and success paths."""

    def test_returns_the_result_on_first_success_with_no_sleep(self) -> None:
        """A function that succeeds immediately is never retried."""
        sleeps: list[float] = []
        result = retry_with_backoff(
            lambda: "ok",
            base=1.0,
            max_retries=3,
            sleep=sleeps.append,
            jitter=lambda: 0.0,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(sleeps, [])

    def test_retries_up_to_max_retries_then_raises(self) -> None:
        """Exhausting all retries re-raises the last exception."""
        calls = {"count": 0}

        def always_fails() -> None:
            calls["count"] += 1
            raise RuntimeError("boom")

        sleeps: list[float] = []
        with self.assertRaises(RuntimeError):
            retry_with_backoff(
                always_fails,
                base=1.0,
                max_retries=2,
                sleep=sleeps.append,
                jitter=lambda: 0.0,
            )
        self.assertEqual(calls["count"], 3)  # 1 initial + 2 retries
        self.assertEqual(len(sleeps), 2)

    def test_backoff_grows_exponentially_with_base(self) -> None:
        """Sleep durations grow as base * 2**attempt (jitter zeroed out)."""
        calls = {"count": 0}

        def fails_twice_then_succeeds() -> str:
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("boom")
            return "ok"

        sleeps: list[float] = []
        result = retry_with_backoff(
            fails_twice_then_succeeds,
            base=2.0,
            max_retries=5,
            sleep=sleeps.append,
            jitter=lambda: 0.0,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_jitter_is_added_to_each_sleep(self) -> None:
        """The jitter callable's return value is added to the base delay."""
        calls = {"count": 0}

        def fails_once_then_succeeds() -> str:
            calls["count"] += 1
            if calls["count"] < 2:
                raise RuntimeError("boom")
            return "ok"

        sleeps: list[float] = []
        retry_with_backoff(
            fails_once_then_succeeds,
            base=1.0,
            max_retries=3,
            sleep=sleeps.append,
            jitter=lambda: 0.5,
        )
        self.assertEqual(sleeps, [1.5])


if __name__ == "__main__":
    unittest.main()
