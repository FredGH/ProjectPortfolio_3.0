"""SM-2 spaced repetition (PLAN.md Step 20)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_EASE = 1.3


@dataclass(frozen=True)
class Sm2State:
    """One question's SM-2 state for one user.

    Attributes:
        ease_factor: The ease factor (starts at 2.5, never below 1.3).
        interval_days: Days until the next review.
        repetitions: Consecutive successful recalls.
        next_review: When the question is next due, or None before the first
            review.
    """

    ease_factor: float
    interval_days: int
    repetitions: int
    next_review: datetime | None = None


def review(state: Sm2State, quality: int, now: datetime) -> Sm2State:
    """Apply one review.

    Args:
        state: The state before the review.
        quality: Self-rating 0-5 (below 3 is a failed recall).
        now: The review time.

    Returns:
        The new state: a failed recall resets repetitions and comes back
        tomorrow; otherwise the interval goes 1, 6, then interval x ease.

    Raises:
        ValueError: If `quality` is not an integer from 0 to 5.
    """
    if (
        not isinstance(quality, int)
        or isinstance(quality, bool)
        or not 0 <= quality <= 5
    ):
        raise ValueError("quality must be an integer from 0 to 5")
    ease = max(
        MIN_EASE,
        state.ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02),
    )
    if quality < 3:
        repetitions, interval = 0, 1
    else:
        repetitions = state.repetitions + 1
        if repetitions == 1:
            interval = 1
        elif repetitions == 2:
            interval = 6
        else:
            interval = round(state.interval_days * state.ease_factor)
    return Sm2State(ease, interval, repetitions, now + timedelta(days=interval))
