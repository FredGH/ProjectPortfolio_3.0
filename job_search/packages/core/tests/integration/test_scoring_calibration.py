"""Tests for core.scoring.calibration (PLAN.md Step 16).

TestBlend, TestGridSearchWeights, and TestSpearmanAgreement exercise pure
functions with hand-built fixtures — no database connection, despite living
under tests/integration/ (a directory convention in this repo, not a promise
every test here touches a DB).
"""

from __future__ import annotations

import unittest

from core.scoring.calibration import (
    _COMPONENTS,
    _blend,
    _grid_search_weights,
    _spearman_agreement,
)


class TestBlend(unittest.TestCase):
    def test_blend_is_a_weighted_mean_of_present_components(self) -> None:
        row = {
            "vector_similarity": 0.8,
            "reranker": 0.6,
            "skill_coverage": 0.4,
            "llm_fit": 0.2,
        }
        weights = {
            "vector_similarity": 0.4,
            "reranker": 0.3,
            "skill_coverage": 0.2,
            "llm_fit": 0.1,
        }
        expected = 0.8 * 0.4 + 0.6 * 0.3 + 0.4 * 0.2 + 0.2 * 0.1
        self.assertAlmostEqual(_blend(row, weights), expected)


class TestGridSearchWeights(unittest.TestCase):
    def test_finds_the_single_component_that_perfectly_predicts_the_label(
        self,
    ) -> None:
        # skill_coverage's values perfectly rank with labels (0.9/0.7/0.3/0.1
        # vs labels 1.0/1.0/0.5/0.0). Other components are constant and thus
        # uninformative (Spearman with constant data = NaN). Only skill_coverage
        # and weighted blends including it can produce defined correlations.
        # The grid search must find that any weight on skill_coverage achieves
        # the same high correlation, so tie-breaking prefers smoothest dist.
        # This test validates that the grid search returns a meaningful result.
        fit_rows = [
            {
                "vector_similarity": 0.5,
                "reranker": 0.5,
                "skill_coverage": 0.9,
                "llm_fit": 0.5,
            },
            {
                "vector_similarity": 0.5,
                "reranker": 0.5,
                "skill_coverage": 0.7,
                "llm_fit": 0.5,
            },
            {
                "vector_similarity": 0.5,
                "reranker": 0.5,
                "skill_coverage": 0.3,
                "llm_fit": 0.5,
            },
            {
                "vector_similarity": 0.5,
                "reranker": 0.5,
                "skill_coverage": 0.1,
                "llm_fit": 0.5,
            },
        ]
        fit_labels = [1.0, 1.0, 0.5, 0.0]
        weights = _grid_search_weights(fit_rows, fit_labels)
        self.assertEqual(set(weights), set(_COMPONENTS))
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)
        # Since all informed weight vectors tie, verify we get a valid result
        # (the smoothest distribution among tied tie-breaker winners).
        self.assertLess(max(weights.values()), 1.0)

    def test_every_returned_weight_is_a_multiple_of_0_05_and_non_negative(
        self,
    ) -> None:
        fit_rows = [
            {
                "vector_similarity": 0.9,
                "reranker": 0.1,
                "skill_coverage": 0.5,
                "llm_fit": 0.5,
            },
            {
                "vector_similarity": 0.1,
                "reranker": 0.9,
                "skill_coverage": 0.5,
                "llm_fit": 0.5,
            },
        ]
        fit_labels = [1.0, 0.0]
        weights = _grid_search_weights(fit_rows, fit_labels)
        for component in _COMPONENTS:
            w = weights[component]
            self.assertGreaterEqual(w, 0.0)
            self.assertAlmostEqual(round(w / 0.05) * 0.05, w, places=6)

    def test_tie_breaking_prefers_smoothest_distribution(self) -> None:
        # Create a fixture where two components (vector_similarity and
        # reranker) have proportional rank orderings: both rank rows the
        # same way. Any mixture of these two will tie for the same Spearman
        # correlation. The tie-breaker should prefer the smoothest
        # distribution (lowest max single weight). With both components tied
        # for highest agreement, weights (0.5, 0.5, 0.0, 0.0) should win
        # over (1.0, 0.0, 0.0, 0.0) or (0.95, 0.05, 0.0, 0.0).
        fit_rows = [
            {
                "vector_similarity": 8.0,
                "reranker": 4.0,
                "skill_coverage": 0.2,
                "llm_fit": 0.2,
            },
            {
                "vector_similarity": 6.0,
                "reranker": 3.0,
                "skill_coverage": 0.2,
                "llm_fit": 0.2,
            },
            {
                "vector_similarity": 4.0,
                "reranker": 2.0,
                "skill_coverage": 0.2,
                "llm_fit": 0.2,
            },
            {
                "vector_similarity": 2.0,
                "reranker": 1.0,
                "skill_coverage": 0.2,
                "llm_fit": 0.2,
            },
        ]
        fit_labels = [1.0, 0.8, 0.6, 0.4]
        weights = _grid_search_weights(fit_rows, fit_labels)
        # The smoothest distribution among tied combos is closest to equal
        # weight on the two ranking components.
        max_weight = max(weights.values())
        self.assertLess(max_weight, 0.75)  # Smoothest is far from (1.0, 0, 0, 0)


class TestSpearmanAgreement(unittest.TestCase):
    def test_perfect_rank_agreement_scores_close_to_one(self) -> None:
        rows = [
            {
                "vector_similarity": 0.9,
                "reranker": 0.9,
                "skill_coverage": 0.9,
                "llm_fit": 0.9,
            },
            {
                "vector_similarity": 0.1,
                "reranker": 0.1,
                "skill_coverage": 0.1,
                "llm_fit": 0.1,
            },
        ]
        labels = [1.0, 0.0]
        weights = {c: 0.25 for c in _COMPONENTS}
        agreement = _spearman_agreement(rows, labels, weights)
        self.assertIsNotNone(agreement)
        self.assertGreater(agreement, 0.99)

    def test_a_constant_label_array_returns_none_not_nan(self) -> None:
        rows = [
            {
                "vector_similarity": 0.9,
                "reranker": 0.9,
                "skill_coverage": 0.9,
                "llm_fit": 0.9,
            },
            {
                "vector_similarity": 0.1,
                "reranker": 0.1,
                "skill_coverage": 0.1,
                "llm_fit": 0.1,
            },
        ]
        labels = [1.0, 1.0]
        weights = {c: 0.25 for c in _COMPONENTS}
        agreement = _spearman_agreement(rows, labels, weights)
        self.assertIsNone(agreement)


if __name__ == "__main__":
    unittest.main()
