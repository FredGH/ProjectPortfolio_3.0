from __future__ import annotations

import unittest

from core.dedup.calibration import LabeledPair, compute_precision_recall_curve

# Synthetic, hand-verified fixture — NOT real data. No real labels exist
# yet; this plan builds the tool that will produce them. See the plan's
# own hand-verification table for the arithmetic behind every assertion
# below.
_SYNTHETIC_PAIRS = [
    LabeledPair("job-1", "job-2", blended_score=0.95, label="match"),
    LabeledPair("job-3", "job-4", blended_score=0.90, label="match"),
    LabeledPair("job-5", "job-6", blended_score=0.80, label="not_match"),
    LabeledPair("job-7", "job-8", blended_score=0.70, label="match"),
    LabeledPair("job-9", "job-10", blended_score=0.60, label="not_match"),
]


class TestComputePrecisionRecallCurve(unittest.TestCase):
    """Tests against a small, hand-verified synthetic fixture."""

    def test_curve_has_one_point_per_distinct_score(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        self.assertEqual(len(curve), 5)

    def test_curve_is_sorted_by_descending_threshold(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        thresholds = [point.threshold for point in curve]
        self.assertEqual(thresholds, sorted(thresholds, reverse=True))

    def test_highest_threshold_point(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        point = curve[0]
        self.assertEqual(point.threshold, 0.95)
        self.assertEqual(point.predicted_match_count, 1)
        self.assertAlmostEqual(point.precision, 1.0)
        self.assertAlmostEqual(point.recall, 1 / 3)

    def test_middle_threshold_point_with_one_false_positive(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        point = next(p for p in curve if p.threshold == 0.80)
        self.assertEqual(point.predicted_match_count, 3)
        self.assertAlmostEqual(point.precision, 2 / 3)
        self.assertAlmostEqual(point.recall, 2 / 3)

    def test_threshold_where_recall_reaches_one(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        point = next(p for p in curve if p.threshold == 0.70)
        self.assertEqual(point.predicted_match_count, 4)
        self.assertAlmostEqual(point.precision, 3 / 4)
        self.assertAlmostEqual(point.recall, 1.0)

    def test_lowest_threshold_predicts_everything_as_a_match(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        point = curve[-1]
        self.assertEqual(point.threshold, 0.60)
        self.assertEqual(point.predicted_match_count, 5)
        self.assertAlmostEqual(point.precision, 3 / 5)
        self.assertAlmostEqual(point.recall, 1.0)

    def test_empty_input_returns_empty_curve(self) -> None:
        self.assertEqual(compute_precision_recall_curve([]), [])

    def test_no_true_matches_gives_none_recall_everywhere(self) -> None:
        """Recall is undefined (0 true matches to find) — None, not a
        divide-by-zero crash or a misleading 0.0/1.0."""
        pairs = [
            LabeledPair("job-1", "job-2", blended_score=0.9, label="not_match"),
            LabeledPair("job-3", "job-4", blended_score=0.5, label="not_match"),
        ]
        curve = compute_precision_recall_curve(pairs)
        self.assertTrue(all(point.recall is None for point in curve))
        # Precision is still well-defined here (0 TP / N predicted = 0.0).
        self.assertAlmostEqual(curve[0].precision, 0.0)
