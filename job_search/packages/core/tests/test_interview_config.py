"""Unit tests for the interview topics config and work planning."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.interview.config import (
    BEHAVIOURAL_TOPIC,
    TopicsConfig,
    active_categories,
    load_topics_config,
    plan_work,
)

_CONFIG = TopicsConfig(2, 1, {"a": ["t1", "t2"], "b": ["t3"]})


class TestLoad(unittest.TestCase):
    """The shipped config is valid and covers every active category."""

    def test_every_active_category_has_topics_in_the_shipped_config(self) -> None:
        config = load_topics_config()
        for category in active_categories():
            self.assertTrue(config.topics.get(category), category)
        self.assertEqual(config.target_per_topic, 30)

    def test_active_categories_come_from_the_category_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.yml"
            path.write_text(
                "qa_category_map:\n  x: alpha\n  y: alpha\n  z: beta\n  o: null\n"
            )
            self.assertEqual(active_categories(path), ["alpha", "beta"])

    def test_a_bad_target_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.yml"
            path.write_text("target_per_topic: 0\nbehavioural_per_category: 1\n")
            with self.assertRaises(ValueError):
                load_topics_config(path)


class TestPlanWork(unittest.TestCase):
    """plan_work reports only what is missing."""

    def test_an_empty_bank_needs_every_topic_and_the_behavioural_set(self) -> None:
        work, unconfigured = plan_work(_CONFIG, ["a"], {})
        self.assertEqual(
            [(w.topic, w.kind, w.missing) for w in work],
            [
                ("t1", "technical", 2),
                ("t2", "technical", 2),
                (BEHAVIOURAL_TOPIC, "behavioural", 1),
            ],
        )
        self.assertEqual(unconfigured, [])

    def test_a_complete_topic_is_skipped(self) -> None:
        work, _ = plan_work(_CONFIG, ["a"], {("a", "t1"): 2, ("a", "t2"): 1})
        self.assertEqual(
            [(w.topic, w.missing) for w in work], [("t2", 1), (BEHAVIOURAL_TOPIC, 1)]
        )

    def test_everything_complete_means_no_work(self) -> None:
        counts = {("a", "t1"): 2, ("a", "t2"): 5, ("a", BEHAVIOURAL_TOPIC): 1}
        self.assertEqual(plan_work(_CONFIG, ["a"], counts), ([], []))

    def test_a_new_category_generates_only_itself(self) -> None:
        counts = {("a", "t1"): 2, ("a", "t2"): 2, ("a", BEHAVIOURAL_TOPIC): 1}
        work, _ = plan_work(_CONFIG, ["a", "b"], counts)
        self.assertEqual({w.category for w in work}, {"b"})

    def test_a_category_without_topics_is_reported_and_skipped(self) -> None:
        work, unconfigured = plan_work(_CONFIG, ["a", "zzz"], {})
        self.assertEqual(unconfigured, ["zzz"])
        self.assertNotIn("zzz", {w.category for w in work})

    def test_filters_narrow_the_work(self) -> None:
        work, _ = plan_work(_CONFIG, ["a", "b"], {}, only_category="a", only_topic="t2")
        self.assertEqual([(w.category, w.topic) for w in work], [("a", "t2")])


if __name__ == "__main__":
    unittest.main()
