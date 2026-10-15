"""The drill queue (PLAN.md Step 20): due first, then the biggest CV gaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class QueueItem:
    """A question as the queue sees it.

    Attributes:
        question_id: The question's id.
        kind: `technical` or `behavioural`.
        next_review: When it is due; None when never reviewed.
        gap_score: The user's CV-gap score (0 when not scored).
        weight: A priority multiplier by question source (`generated` is 1.0;
            Step 20a raises it for questions actually asked).
    """

    question_id: str
    kind: str
    next_review: datetime | None
    gap_score: float
    weight: float = 1.0


def order_queue(
    items: list[QueueItem], now: datetime, limit: int | None = None
) -> list[QueueItem]:
    """Order questions for drilling.

    Args:
        items: Every candidate question.
        now: The current time.
        limit: Keep only this many, or None for all.

    Returns:
        Due questions (most overdue first, higher weight first on ties), then
        never-seen technical questions by `gap_score x weight`, then never-seen
        behavioural ones. Reviewed questions not yet due are left out.
    """
    due = sorted(
        (i for i in items if i.next_review is not None and i.next_review <= now),
        key=lambda i: (i.next_review, -i.weight),
    )
    unseen = [i for i in items if i.next_review is None]
    technical = sorted(
        (i for i in unseen if i.kind == "technical"),
        key=lambda i: -(i.gap_score * i.weight),
    )
    behavioural = sorted(
        (i for i in unseen if i.kind != "technical"), key=lambda i: -i.weight
    )
    ordered = due + technical + behavioural
    return ordered if limit is None else ordered[:limit]
