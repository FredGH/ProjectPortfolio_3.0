"""Unit tests for SM-2 and the drill queue."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from core.interview.queue import QueueItem, order_queue
from core.interview.sm2 import Sm2State, review

_NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
_NEW = Sm2State(ease_factor=2.5, interval_days=0, repetitions=0)


class TestReview(unittest.TestCase):
    """The textbook SM-2 update."""

    def test_first_good_answers_follow_1_6_then_interval_times_ease(self) -> None:
        one = review(_NEW, 4, _NOW)
        self.assertEqual((one.repetitions, one.interval_days), (1, 1))
        six = review(one, 4, _NOW)
        self.assertEqual((six.repetitions, six.interval_days), (2, 6))
        third = review(six, 4, _NOW)
        self.assertEqual(third.repetitions, 3)
        self.assertEqual(third.interval_days, round(6 * six.ease_factor))
        self.assertEqual(third.next_review, _NOW + timedelta(days=third.interval_days))

    def test_ease_changes_by_the_sm2_formula(self) -> None:
        self.assertAlmostEqual(review(_NEW, 5, _NOW).ease_factor, 2.6)
        self.assertAlmostEqual(review(_NEW, 4, _NOW).ease_factor, 2.5)
        self.assertAlmostEqual(review(_NEW, 3, _NOW).ease_factor, 2.36)
        self.assertAlmostEqual(review(_NEW, 0, _NOW).ease_factor, 1.7)

    def test_a_failed_recall_resets_repetitions_and_schedules_tomorrow(self) -> None:
        learned = Sm2State(2.5, 15, 4)
        after = review(learned, 2, _NOW)
        self.assertEqual((after.repetitions, after.interval_days), (0, 1))
        self.assertEqual(after.next_review, _NOW + timedelta(days=1))

    def test_ease_never_drops_below_1_3(self) -> None:
        state = Sm2State(1.35, 6, 2)
        self.assertEqual(review(state, 0, _NOW).ease_factor, 1.3)

    def test_quality_outside_0_to_5_is_refused(self) -> None:
        for bad in (-1, 6):
            with self.assertRaises(ValueError):
                review(_NEW, bad, _NOW)


def _item(qid: str, **kw: object) -> QueueItem:
    """Build a queue item.

    Args:
        qid: The question id.
        **kw: Fields to replace.

    Returns:
        A `QueueItem` for a never-seen technical question by default.
    """
    base: dict[str, object] = {
        "question_id": qid,
        "kind": "technical",
        "next_review": None,
        "gap_score": 0.0,
        "weight": 1.0,
    }
    return QueueItem(**{**base, **kw})  # type: ignore[arg-type]


class TestOrderQueue(unittest.TestCase):
    """Due first, then unseen technical by gap, then unseen behavioural."""

    def test_due_then_gap_ranked_unseen_then_behavioural(self) -> None:
        items = [
            _item("behav", kind="behavioural"),
            _item("low", gap_score=0.2),
            _item("high", gap_score=0.9),
            _item("late", next_review=_NOW - timedelta(days=1)),
            _item("later", next_review=_NOW - timedelta(days=5)),
            _item("future", next_review=_NOW + timedelta(days=3)),
        ]
        order = [i.question_id for i in order_queue(items, _NOW)]
        self.assertEqual(order, ["later", "late", "high", "low", "behav"])

    def test_weight_lifts_an_unseen_question(self) -> None:
        items = [_item("a", gap_score=0.5), _item("b", gap_score=0.3, weight=3.0)]
        self.assertEqual([i.question_id for i in order_queue(items, _NOW)], ["b", "a"])

    def test_limit_truncates(self) -> None:
        items = [_item(str(n), gap_score=n / 10) for n in range(5)]
        self.assertEqual(len(order_queue(items, _NOW, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
