"""Hard-stop helper for a cancelled tailoring run (Step 17).

A slow model call cannot be interrupted from Python, so it runs in a worker
thread while the caller polls: on cancel the caller abandons the thread,
optionally closes the underlying client (which makes a local Ollama server
stop generating) and moves on at once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RunCancelled(Exception):
    """The run was cancelled while a call was in flight."""


def run_cancellable(
    fn: Callable[[], T],
    *,
    should_stop: Callable[[], bool],
    abort: Callable[[], None] | None = None,
    poll_seconds: float = 1.0,
) -> T:
    """Run `fn` in a worker thread, giving up as soon as `should_stop`.

    Args:
        fn: The (possibly slow) call.
        should_stop: Polled every `poll_seconds` while `fn` runs. If it
            raises, the error is logged and treated as False, so it can
            never hide the call's result.
        abort: Called once, on cancel, to cut the call short (e.g. close the
            HTTP client). Its errors are swallowed.
        poll_seconds: How often to poll `should_stop`.

    Returns:
        `fn`'s result.

    Raises:
        RunCancelled: If `should_stop` became True before `fn` finished. The
            worker thread is abandoned, not joined.
        Exception: Whatever `fn` raised, unchanged.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        while True:
            done, _ = wait([future], timeout=poll_seconds)
            if done:
                result = future.result()
                executor.shutdown(wait=False)
                return result
            try:
                stop = should_stop()
            except Exception:  # noqa: BLE001 — a failed poll must not cancel
                logger.warning(
                    "cancel check failed; assuming not cancelled", exc_info=True
                )
                stop = False
            if stop:
                if abort is not None:
                    try:
                        abort()
                    except Exception:  # noqa: BLE001 — best effort
                        logger.warning("abort failed", exc_info=True)
                executor.shutdown(wait=False, cancel_futures=True)
                raise RunCancelled("run cancelled")
    except BaseException:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
