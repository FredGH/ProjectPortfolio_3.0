"""Tests for core.scoring.calibration (PLAN.md Step 16).

TestBlend, TestGridSearchWeights, and TestSpearmanAgreement exercise pure
functions with hand-built fixtures — no database connection, despite living
under tests/integration/ (a directory convention in this repo, not a promise
every test here touches a DB).
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine, session_scope
from core.scoring.calibration import (
    _COMPONENTS,
    CalibrationPreview,
    _blend,
    _grid_search_weights,
    _spearman_agreement,
    save_calibration,
    split_and_fit,
    write_label,
)
from core.settings import get_settings


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
        # skill_coverage is perfectly monotonic with the (distinct, untied)
        # labels; the other three components have real, non-constant,
        # label-uncorrelated variance — verified by exhaustive grid search
        # that every weight vector achieving the best possible Spearman
        # correlation on this fixture has skill_coverage as its strict max
        # component (always > 0.5), so this assertion is not a coincidence
        # of this particular fixture's degeneracy.
        fit_rows = [
            {
                "vector_similarity": 0.56,
                "reranker": 0.12,
                "skill_coverage": 0.9,
                "llm_fit": 0.07,
            },
            {
                "vector_similarity": 0.94,
                "reranker": 0.44,
                "skill_coverage": 0.7,
                "llm_fit": 0.67,
            },
            {
                "vector_similarity": 0.84,
                "reranker": 0.07,
                "skill_coverage": 0.4,
                "llm_fit": 0.78,
            },
            {
                "vector_similarity": 0.14,
                "reranker": 0.24,
                "skill_coverage": 0.1,
                "llm_fit": 0.9,
            },
        ]
        fit_labels = [1.0, 0.7, 0.3, 0.0]
        weights = _grid_search_weights(fit_rows, fit_labels)
        self.assertEqual(set(weights), set(_COMPONENTS))
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)
        self.assertEqual(weights["skill_coverage"], max(weights.values()))
        self.assertGreater(weights["skill_coverage"], 0.5)

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


class TestSplitAndFit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture split_and_fit user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.calibration_run WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.weight WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id LIKE 'zzfixture-fit-%'"
                )
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _insert_labeled_job(self, index: int, label: str) -> None:
        job_group_id = f"zzfixture-fit-{index}"
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company, location, engagement_type, description) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co', 'London', "
                    "'contract', 'zzfixture description')"
                ),
                {"j": job_group_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, reranker_score, "
                    "skill_coverage_score, llm_fit_score, final_score, "
                    "embedding_model) "
                    "VALUES (:u, :j, true, :s, :s, :s, :llm, :s, 'nomic-embed-text')"
                ),
                {
                    "u": self.user_id,
                    "j": job_group_id,
                    # Score rises with index so labels correlate with score
                    # — a fit run against this fixture set should find a
                    # sane, non-degenerate weight vector.
                    "s": round(0.1 + 0.02 * index, 4),
                    "llm": round((0.1 + 0.02 * index) * 100, 2),
                },
            )
        write_label(self.app_engine, self.user_id, job_group_id, label)

    def test_raises_below_30_labels(self) -> None:
        for i in range(29):
            self._insert_labeled_job(i, "maybe")
        with self.assertRaises(ValueError):
            split_and_fit(self.app_engine, self.user_id)

    def test_splits_30_labels_into_20_fit_and_10_holdout_with_no_overlap(
        self,
    ) -> None:
        for i in range(30):
            label = "strong" if i >= 20 else ("maybe" if i >= 10 else "no")
            self._insert_labeled_job(i, label)
        preview = split_and_fit(self.app_engine, self.user_id)
        self.assertEqual(preview.fit_count, 20)
        self.assertEqual(preview.holdout_count, 10)
        self.assertEqual(
            set(preview.weights),
            {"vector_similarity", "reranker", "skill_coverage", "llm_fit"},
        )
        self.assertAlmostEqual(sum(preview.weights.values()), 1.0, places=6)
        self.assertEqual(preview.embedding_model, "nomic-embed-text")

    def test_same_seed_produces_the_same_split_every_time(self) -> None:
        for i in range(30):
            label = "strong" if i >= 20 else ("maybe" if i >= 10 else "no")
            self._insert_labeled_job(i, label)
        preview_a = split_and_fit(self.app_engine, self.user_id, seed=0)
        preview_b = split_and_fit(self.app_engine, self.user_id, seed=0)
        self.assertEqual(preview_a.weights, preview_b.weights)
        self.assertEqual(preview_a.holdout_agreement, preview_b.holdout_agreement)


class TestSaveCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture save_calibration user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.calibration_run WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.weight WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _preview(self) -> CalibrationPreview:
        return CalibrationPreview(
            fit_count=20,
            holdout_count=10,
            weights={
                "vector_similarity": 0.4,
                "reranker": 0.3,
                "skill_coverage": 0.2,
                "llm_fit": 0.1,
            },
            holdout_agreement=0.8,
            embedding_model="nomic-embed-text",
        )

    def test_writes_one_weight_row_per_component(self) -> None:
        save_calibration(self.app_engine, self.user_id, self._preview())
        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            rows = conn.execute(
                text(
                    "SELECT component, weight FROM scoring.weight " "WHERE user_id = :u"
                ),
                {"u": self.user_id},
            ).all()
        self.assertEqual(len(rows), 4)
        by_component = {r.component: float(r.weight) for r in rows}
        self.assertAlmostEqual(by_component["vector_similarity"], 0.4)

    def test_re_saving_upserts_weights_rather_than_duplicating(self) -> None:
        save_calibration(self.app_engine, self.user_id, self._preview())
        save_calibration(self.app_engine, self.user_id, self._preview())
        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            count = conn.execute(
                text("SELECT count(*) FROM scoring.weight WHERE user_id = :u"),
                {"u": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 4)

    def test_appends_one_calibration_run_row_per_save(self) -> None:
        save_calibration(self.app_engine, self.user_id, self._preview())
        save_calibration(self.app_engine, self.user_id, self._preview())
        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            count = conn.execute(
                text("SELECT count(*) FROM scoring.calibration_run WHERE user_id = :u"),
                {"u": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
