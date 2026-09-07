from __future__ import annotations

import unittest

from core.dedup.simhash import compute_simhash
from core.dedup.similarity_features import compute_similarity_features


class TestComputeSimilarityFeatures(unittest.TestCase):
    def test_combines_simhash_and_parsed_salary(self) -> None:
        description = "Data Engineer role, permanent, London."
        result = compute_similarity_features(
            description, salary_raw="£80k - £95k per year"
        )
        self.assertEqual(result.description_simhash, compute_simhash(description))
        self.assertEqual(result.rate_annualised, 87500.0)
        self.assertEqual(result.rate_currency, "GBP")
        self.assertEqual(result.salary_band, "80000-90000")

    def test_none_description_hashes_to_zero(self) -> None:
        result = compute_similarity_features(None, salary_raw=None)
        self.assertEqual(result.description_simhash, 0)
        self.assertIsNone(result.rate_annualised)
        self.assertIsNone(result.salary_band)
