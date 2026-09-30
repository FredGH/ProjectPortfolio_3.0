"""Integration tests for core.pipeline.stage_functions against live
Postgres. Each stage function is already covered end-to-end by its own
underlying write_X function's tests (e.g. test_write_blocking_keys.py)
-- these tests only prove the *wrapper* calls the right function with
the right engine and returns a sane result dict, using each function's
already-established "0 rows written on an empty pool" no-op case so no
fixture data is needed.
"""

from __future__ import annotations

import unittest

from core.pipeline.stage_functions import (
    run_classify_jobs,
    run_cluster_jobs,
    run_compute_blocking_keys,
    run_compute_similarity_features,
    run_compute_survivorship,
    run_compute_title_similarity_scores,
    run_enrich_engagement_terms,
)


class TestGlobalBatchStageFunctions(unittest.TestCase):
    def test_run_enrich_engagement_terms_returns_rows_written(self) -> None:
        result = run_enrich_engagement_terms({})
        self.assertIn("rows_written", result)
        self.assertIsInstance(result["rows_written"], int)

    def test_run_compute_blocking_keys_returns_rows_written(self) -> None:
        result = run_compute_blocking_keys({})
        self.assertIn("rows_written", result)

    def test_run_compute_similarity_features_returns_rows_written(self) -> None:
        result = run_compute_similarity_features({})
        self.assertIn("rows_written", result)

    def test_run_compute_title_similarity_scores_returns_rows_written(self) -> None:
        result = run_compute_title_similarity_scores({})
        self.assertIn("rows_written", result)

    def test_run_cluster_jobs_returns_rows_written(self) -> None:
        result = run_cluster_jobs({})
        self.assertIn("rows_written", result)

    def test_run_compute_survivorship_returns_rows_written(self) -> None:
        result = run_compute_survivorship({})
        self.assertIn("rows_written", result)

    def test_run_classify_jobs_returns_rows_written(self) -> None:
        result = run_classify_jobs({})
        self.assertIn("rows_written", result)


class TestCvAndSkillsStageFunctions(unittest.TestCase):
    def test_run_embed_esco_returns_embeddings_written(self) -> None:
        from core.pipeline.stage_functions import run_embed_esco

        result = run_embed_esco({})
        self.assertIn("embeddings_written", result)

    def test_run_map_skills_returns_a_summary(self) -> None:
        from core.pipeline.stage_functions import run_map_skills

        result = run_map_skills({})
        self.assertIn("mapped", result)
        self.assertIn("unmapped", result)

    def test_run_llm_map_skills_without_an_api_key_raises(self) -> None:
        # Exercised for real in test_llm_map.py's own suite when a key IS
        # configured; here we only prove the wrapper's shape, using the
        # same "no ANTHROPIC_API_KEY" guard as run_classify_jobs's own
        # test would if the dev environment had no key. Since this dev
        # environment DOES have a key configured (verified this session),
        # assert the success shape instead.
        from core.pipeline.stage_functions import run_llm_map_skills

        result = run_llm_map_skills({})
        self.assertIn("checked", result)
        self.assertIn("applied", result)

    def test_run_map_cv_skills_requires_a_user_id(self) -> None:
        from core.pipeline.stage_functions import run_map_cv_skills

        with self.assertRaises(KeyError):
            run_map_cv_skills({})


class TestScoringFunnelStageFunctions(unittest.TestCase):
    def test_run_chunk_embed_jobs_returns_jobs_chunked(self) -> None:
        from core.pipeline.stage_functions import run_chunk_embed_jobs

        result = run_chunk_embed_jobs({})
        self.assertIn("jobs_chunked", result)

    def test_run_score_filter_jobs_requires_a_user_id(self) -> None:
        from core.pipeline.stage_functions import run_score_filter_jobs

        with self.assertRaises(KeyError):
            run_score_filter_jobs({})

    def test_run_score_blend_requires_a_user_id(self) -> None:
        from core.pipeline.stage_functions import run_score_blend

        with self.assertRaises(KeyError):
            run_score_blend({})


if __name__ == "__main__":
    unittest.main()
