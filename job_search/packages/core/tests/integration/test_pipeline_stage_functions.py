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


if __name__ == "__main__":
    unittest.main()
