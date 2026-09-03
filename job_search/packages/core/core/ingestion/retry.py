"""Retry with exponential backoff and jitter (PLAN.md Step 3).

The runner wraps a connector's whole fetch() consumption in this — a
transient failure retries the entire fetch, not individual pages. Real
per-page resumable retry is a Step 4+ refinement once a paginated
connector actually exists.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    base: float,
    max_retries: int,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Call `fn`, retrying on failure with exponential backoff plus jitter.

    Args:
        fn: The zero-argument function to call.
        base: Base delay in seconds; the Nth retry sleeps
            `base * 2**(N-1) + jitter()`.
        max_retries: Maximum number of retries after the first attempt.
        sleep: Injectable sleep function, for deterministic tests.
        jitter: Injectable function returning a random delay to add.
        retry_on: Exception types that trigger a retry. Anything else
            propagates immediately.

    Returns:
        `fn()`'s return value, from whichever attempt first succeeds.

    Raises:
        Exception: Whatever `fn` raised on its final attempt, once
            `max_retries` is exhausted.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except retry_on:
            if attempt >= max_retries:
                raise
            delay = base * (2**attempt) + jitter()
            sleep(delay)
            attempt += 1
