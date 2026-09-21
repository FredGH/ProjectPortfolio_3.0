"""Unit tests for the embedding-coverage warning wording."""

from __future__ import annotations

import unittest

from core.skills.esco_embed import embedding_coverage_warning


class TestEmbeddingCoverageWarning(unittest.TestCase):
    def test_full_coverage_needs_no_warning(self) -> None:
        self.assertIsNone(embedding_coverage_warning(100, 100))

    def test_no_embeddings_says_the_similarity_stage_will_match_nothing(
        self,
    ) -> None:
        message = embedding_coverage_warning(100, 0)
        self.assertIn("empty", message)
        self.assertIn("embed-esco", message)
        self.assertIn("similarity", message)

    def test_a_partial_embedding_names_the_numbers(self) -> None:
        message = embedding_coverage_warning(13939, 4000)
        self.assertIn("4000 of 13939", message)
        self.assertIn("embed-esco", message)

    def test_no_skills_at_all_points_at_load_esco(self) -> None:
        message = embedding_coverage_warning(0, 0)
        self.assertIn("load-esco", message)


if __name__ == "__main__":
    unittest.main()
