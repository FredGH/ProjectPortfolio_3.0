"""Unit tests for the cancellable-call helper."""

from __future__ import annotations

import threading
import time
import unittest

from core.tailoring.cancel import RunCancelled, run_cancellable


class TestRunCancellable(unittest.TestCase):
    def test_returns_the_result(self) -> None:
        self.assertEqual(
            run_cancellable(lambda: 42, should_stop=lambda: False, poll_seconds=0.05),
            42,
        )

    def test_propagates_the_call_exception(self) -> None:
        def boom() -> None:
            raise ValueError("nope")

        with self.assertRaises(ValueError):
            run_cancellable(boom, should_stop=lambda: False, poll_seconds=0.05)

    def test_a_timeout_error_from_the_call_propagates(self) -> None:
        def slow_fail() -> None:
            time.sleep(0.1)
            raise TimeoutError("upstream")

        with self.assertRaises(TimeoutError):
            run_cancellable(slow_fail, should_stop=lambda: False, poll_seconds=0.02)

    def test_blocking_call_is_abandoned_and_aborted_once(self) -> None:
        release = threading.Event()
        aborted: list[int] = []
        started = time.monotonic()
        try:
            with self.assertRaises(RunCancelled):
                run_cancellable(
                    lambda: release.wait(30),
                    should_stop=lambda: time.monotonic() - started > 0.1,
                    abort=lambda: aborted.append(1),
                    poll_seconds=0.1,
                )
        finally:
            release.set()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(aborted, [1])

    def test_abort_errors_are_swallowed(self) -> None:
        release = threading.Event()

        def bad_abort() -> None:
            raise RuntimeError("close failed")

        try:
            with self.assertRaises(RunCancelled):
                run_cancellable(
                    lambda: release.wait(30),
                    should_stop=lambda: True,
                    abort=bad_abort,
                    poll_seconds=0.05,
                )
        finally:
            release.set()

    def test_raising_should_stop_does_not_break_a_normal_call(self) -> None:
        def flaky() -> bool:
            raise RuntimeError("db down")

        def slow() -> str:
            time.sleep(0.15)
            return "ok"

        self.assertEqual(
            run_cancellable(slow, should_stop=flaky, poll_seconds=0.05), "ok"
        )


if __name__ == "__main__":
    unittest.main()
