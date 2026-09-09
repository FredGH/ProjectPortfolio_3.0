from __future__ import annotations

import unittest

from core.classification.embeddings import _cosine_similarity


class TestCosineSimilarity(unittest.TestCase):
    def test_identical_vectors_have_similarity_one(self) -> None:
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)

    def test_orthogonal_vectors_have_similarity_zero(self) -> None:
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_opposite_vectors_have_similarity_negative_one(self) -> None:
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_zero_vector_returns_zero_rather_than_dividing_by_zero(self) -> None:
        self.assertEqual(_cosine_similarity([0.0, 0.0], [1.0, 0.0]), 0.0)
