"""Unit tests for canonical titles, the maturity gate, novelty and hybridity."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from core.emergent.canonical import canonicalise, is_stoplisted
from core.emergent.config import load_config
from core.emergent.hybrid import best_blend, nearest_category
from core.emergent.jobs import DiscoveryJob
from core.emergent.maturity import assess
from core.emergent.novelty import novelty_index

_CONFIG = load_config()
_NOW = datetime(2027, 3, 1, tzinfo=UTC)


def _job(
    employer: str | None, days_ago: float, title: str = "Forward Deployed Engineer"
) -> DiscoveryJob:
    """Build a discovery job.

    Args:
        employer: The normalised employer.
        days_ago: How long before `_NOW` it was first seen.
        title: The raw title.

    Returns:
        A `DiscoveryJob`.
    """
    return DiscoveryJob(
        f"j-{employer}-{days_ago}",
        title,
        employer,
        employer,
        _NOW - timedelta(days=days_ago),
        "greenhouse",
    )


class TestConfig(unittest.TestCase):
    """The shipped config is valid."""

    def test_the_shipped_config_loads(self) -> None:
        self.assertGreaterEqual(_CONFIG.min_employers, 2)
        self.assertIn("ninja", _CONFIG.stoplist)

    def test_a_bad_threshold_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.yml"
            path.write_text("min_employers: 0\n")
            with self.assertRaises(ValueError):
                load_config(path)


class TestCanonicalise(unittest.TestCase):
    """Titles reduce to the phrase that names the function."""

    def test_level_gender_and_decoration_are_removed(self) -> None:
        cases = {
            "Senior Machine Learning Engineer": "machine learning engineer",
            "Forward Deployed Engineer - London (Hybrid)": "forward deployed engineer",
            "Data Engineer (m/f/d)": "data engineer",
            "Staff Software Engineer, Payments": "software engineer",
            "Lead Data Engineer II": "data engineer",
        }
        for raw, expected in cases.items():
            self.assertEqual(canonicalise(raw, _CONFIG), expected, raw)

    def test_the_employer_name_inside_a_title_is_removed(self) -> None:
        self.assertEqual(
            canonicalise("Acme Data Engineer", _CONFIG, company="Acme"), "data engineer"
        )

    def test_title_inflation_is_dropped_not_kept(self) -> None:
        for raw in (
            "Code Ninja",
            "Data Rockstar",
            "Rock Star Engineer",
            "Growth Hero",
            "SQL Guru",
        ):
            self.assertIsNone(canonicalise(raw, _CONFIG), raw)
            self.assertTrue(is_stoplisted(raw, _CONFIG), raw)
        self.assertFalse(is_stoplisted("Heroku Platform Engineer", _CONFIG))

    def test_empty_and_meaningless_titles_are_dropped(self) -> None:
        for raw in (None, "", "   ", "Senior", "-", "()"):
            self.assertIsNone(canonicalise(raw, _CONFIG), raw)


class TestMaturity(unittest.TestCase):
    """The gate names exactly what is missing."""

    def test_a_short_young_corpus_is_not_mature_and_says_why(self) -> None:
        jobs = [_job("a", 1), _job("b", 0.5)]
        result = assess(jobs, _CONFIG)
        self.assertFalse(result.mature)
        self.assertIn("days", result.note)
        self.assertIn("periods", result.note)

    def test_long_history_across_enough_weeks_is_mature(self) -> None:
        jobs = [_job(f"e{i}", 100 - i * 15) for i in range(7)]
        result = assess(jobs, _CONFIG)
        self.assertTrue(result.mature, result.note)
        self.assertEqual(result.employers, 7)

    def test_an_empty_corpus_is_not_mature(self) -> None:
        self.assertFalse(assess([], _CONFIG).mature)


class TestNovelty(unittest.TestCase):
    """Breadth is a hard floor, not a soft factor."""

    def test_below_the_employer_floor_the_index_is_none_and_called_noise(self) -> None:
        jobs = [_job(f"e{i}", 10 + i) for i in range(_CONFIG.min_employers - 1)]
        result = novelty_index(jobs, _CONFIG, _NOW)
        self.assertIsNone(result.value)
        self.assertIn("noise", result.note)

    def test_many_employers_over_several_weeks_scores_between_0_and_1(self) -> None:
        jobs = [_job(f"e{i}", 70 - i * 7) for i in range(10)]
        result = novelty_index(jobs, _CONFIG, _NOW)
        self.assertIsNotNone(result.value)
        self.assertTrue(0.0 < result.value <= 1.0)

    def test_too_few_periods_leave_velocity_unknown(self) -> None:
        jobs = [_job(f"e{i}", 3) for i in range(8)]  # all in the same week
        result = novelty_index(jobs, _CONFIG, _NOW)
        self.assertIsNone(result.value)
        self.assertIn("velocity", result.note)

    def test_a_growing_title_outscores_a_shrinking_one(self) -> None:
        growing = [
            _job(f"g{w}-{i}", 80 - w * 7)
            for w, n in enumerate([1, 1, 2, 3, 5, 8])
            for i in range(n)
        ]
        shrinking = [
            _job(f"s{w}-{i}", 80 - w * 7)
            for w, n in enumerate([8, 5, 3, 2, 1, 1])
            for i in range(n)
        ]
        up = novelty_index(growing, _CONFIG, _NOW).value
        down = novelty_index(shrinking, _CONFIG, _NOW).value
        self.assertIsNotNone(up)
        self.assertIsNotNone(down)
        self.assertGreater(up, down)

    def test_a_job_without_an_employer_never_counts_towards_breadth(self) -> None:
        jobs = [_job(None, 10 + i) for i in range(20)]
        self.assertIsNone(novelty_index(jobs, _CONFIG, _NOW).value)


def _axes() -> dict[str, np.ndarray]:
    """Build three orthogonal category centroids.

    Returns:
        Category name to a unit axis vector.
    """
    return {
        "software_engineer": np.array([1.0, 0, 0]),
        "data_engineer": np.array([0, 1.0, 0]),
        "data_scientist": np.array([0, 0, 1.0]),
    }


class TestHybrid(unittest.TestCase):
    """A blend must beat every single category by a margin."""

    def test_a_60_40_mixture_is_found_with_its_weights(self) -> None:
        centroid = 0.6 * np.array([1.0, 0, 0]) + 0.4 * np.array([0, 1.0, 0])
        blend = best_blend(centroid, _axes(), min_gain=0.05, min_weight=0.25)
        assert blend is not None
        self.assertEqual(
            {blend.category_a, blend.category_b}, {"software_engineer", "data_engineer"}
        )
        weights = {blend.category_a: blend.weight_a, blend.category_b: blend.weight_b}
        self.assertAlmostEqual(weights["software_engineer"], 0.6, places=1)
        self.assertGreater(blend.gain, 0.05)

    def test_a_pure_category_is_not_a_hybrid(self) -> None:
        self.assertIsNone(
            best_blend(np.array([1.0, 0, 0]), _axes(), min_gain=0.05, min_weight=0.25)
        )

    def test_a_near_pure_category_is_not_a_hybrid(self) -> None:
        centroid = 0.95 * np.array([1.0, 0, 0]) + 0.05 * np.array([0, 1.0, 0])
        self.assertIsNone(best_blend(centroid, _axes(), min_gain=0.05, min_weight=0.25))

    def test_an_outlier_far_from_everything_is_not_a_hybrid(self) -> None:
        outlier = np.array([-1.0, -1.0, -1.0])
        self.assertIsNone(best_blend(outlier, _axes(), min_gain=0.05, min_weight=0.25))

    def test_one_category_cannot_blend(self) -> None:
        self.assertIsNone(
            best_blend(
                np.array([1.0, 1.0, 0]),
                {"a": np.array([1.0, 0, 0])},
                min_gain=0.05,
                min_weight=0.25,
            )
        )

    def test_nearest_category_breaks_ties_by_name(self) -> None:
        tie = {"b": np.array([1.0, 0]), "a": np.array([1.0, 0])}
        self.assertEqual(nearest_category(np.array([1.0, 0]), tie)[0], "b")


if __name__ == "__main__":
    unittest.main()
