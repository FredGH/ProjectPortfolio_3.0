from __future__ import annotations

import unittest

from core.dedup.title_similarity import title_token_set_ratio


class TestTitleTokenSetRatio(unittest.TestCase):
    def test_identical_titles_score_one(self) -> None:
        self.assertEqual(
            title_token_set_ratio("Data Centre Engineer", "Data Centre Engineer"),
            1.0,
        )

    def test_seniority_variant_scores_highly(self) -> None:
        """PLAN.md's own example: 'Senior Data Engineer' vs 'Data
        Platform Engineer' at the same company — token-set ratio should
        still register meaningful overlap even though these are two
        DIFFERENT roles (this is why Step 8 also weighs company/
        description, not title alone)."""
        score = title_token_set_ratio("Senior Data Engineer", "Data Platform Engineer")
        self.assertGreater(score, 0.5)

    def test_completely_different_titles_score_low(self) -> None:
        score = title_token_set_ratio("Data Engineer", "Payroll Administrator")
        self.assertLess(score, 0.5)

    def test_empty_titles_do_not_raise(self) -> None:
        result = title_token_set_ratio("", "")
        self.assertIsInstance(result, float)
