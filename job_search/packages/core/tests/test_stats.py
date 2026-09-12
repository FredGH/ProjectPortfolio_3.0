from __future__ import annotations

import unittest

from core.stats import margin_of_error_for_sample_size, recommended_sample_size


class TestRecommendedSampleSize(unittest.TestCase):
    def test_matches_the_textbook_infinite_population_value(self) -> None:
        # Classic reference point: 95% confidence, +/-5% margin, p=0.5,
        # unconstrained by population size -> n0 = 1.96^2*0.25/0.05^2 = 384.16.
        size = recommended_sample_size(
            population=10_000_000, confidence=0.95, margin_of_error=0.05
        )
        self.assertEqual(size, 385)

    def test_finite_population_correction_shrinks_the_recommendation(self) -> None:
        large_population = recommended_sample_size(
            population=10_000_000, confidence=0.95, margin_of_error=0.05
        )
        small_population = recommended_sample_size(
            population=2_600, confidence=0.95, margin_of_error=0.05
        )
        self.assertLess(small_population, large_population)

    def test_never_recommends_more_than_the_population(self) -> None:
        size = recommended_sample_size(
            population=50, confidence=0.95, margin_of_error=0.01
        )
        self.assertEqual(size, 50)

    def test_zero_population_recommends_zero(self) -> None:
        self.assertEqual(
            recommended_sample_size(
                population=0, confidence=0.95, margin_of_error=0.05
            ),
            0,
        )

    def test_tighter_margin_of_error_recommends_more_reviews(self) -> None:
        loose = recommended_sample_size(
            population=2_600, confidence=0.90, margin_of_error=0.10
        )
        tight = recommended_sample_size(
            population=2_600, confidence=0.90, margin_of_error=0.03
        )
        self.assertLess(loose, tight)

    def test_higher_confidence_recommends_more_reviews(self) -> None:
        lower_confidence = recommended_sample_size(
            population=2_600, confidence=0.80, margin_of_error=0.08
        )
        higher_confidence = recommended_sample_size(
            population=2_600, confidence=0.99, margin_of_error=0.08
        )
        self.assertLess(lower_confidence, higher_confidence)


class TestMarginOfErrorForSampleSize(unittest.TestCase):
    def test_is_the_inverse_of_recommended_sample_size(self) -> None:
        population, confidence, margin_of_error = 2_600, 0.90, 0.08
        size = recommended_sample_size(population, confidence, margin_of_error)
        implied = margin_of_error_for_sample_size(population, size, confidence)
        self.assertAlmostEqual(implied, margin_of_error, delta=0.005)

    def test_smaller_sample_has_a_wider_margin_of_error(self) -> None:
        population, confidence = 2_600, 0.90
        wide = margin_of_error_for_sample_size(population, 50, confidence)
        narrow = margin_of_error_for_sample_size(population, 500, confidence)
        self.assertGreater(wide, narrow)

    def test_sampling_the_entire_population_has_zero_margin_of_error(self) -> None:
        self.assertEqual(margin_of_error_for_sample_size(200, 200, 0.90), 0.0)

    def test_sample_size_of_zero_raises(self) -> None:
        with self.assertRaises(ValueError):
            margin_of_error_for_sample_size(200, 0, 0.90)
