"""Integration tests for core.scoring.blend against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.scoring.blend import compute_final_scores
from core.settings import get_settings


class TestBlend(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.job_id = "fixture-job-blend-1"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture blend user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company) VALUES (:j, 'zzfixture role', 'zzfixture co')"
                ),
                {"j": self.job_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, "
                    "skill_coverage_score) VALUES (:u, :j, true, 0.8, 0.6)"
                ),
                {"u": self.user_id, "j": self.job_id},
            )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.weight WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id LIKE 'fixture-job-blend-%'"
                )
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _final(self) -> float:
        with self.owner.connect() as conn:
            return float(
                conn.execute(
                    text(
                        "SELECT final_score FROM scoring.job_score "
                        "WHERE user_id = :u AND job_group_id = :j"
                    ),
                    {"u": self.user_id, "j": self.job_id},
                ).scalar_one()
            )

    def test_missing_components_are_excluded_not_treated_as_zero(self) -> None:
        compute_final_scores(self.app_engine, self.user_id)
        # Equal weight over the two present components: (0.8 + 0.6) / 2 = 0.7,
        # not (0.8 + 0.6 + 0 + 0) / 4 = 0.35.
        self.assertAlmostEqual(self._final(), 0.7, places=4)

    def test_llm_fit_score_is_rescaled_from_0_100_before_blending(self) -> None:
        # Finding 5 regression: llm_fit_score is stored 0-100 (human
        # readable) but every other component is ~[0,1]. The blend must
        # divide it by 100 before averaging, not treat 40 as if it were
        # already on the [0,1] scale.
        job_id = "fixture-job-blend-llm"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company) VALUES (:j, 'zzfixture role', 'zzfixture co')"
                ),
                {"j": job_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, llm_fit_score) "
                    "VALUES (:u, :j, true, 0.8, 40)"
                ),
                {"u": self.user_id, "j": job_id},
            )
        compute_final_scores(self.app_engine, self.user_id)
        with self.owner.connect() as conn:
            final_score, stored_llm_fit_score = conn.execute(
                text(
                    "SELECT final_score, llm_fit_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": job_id},
            ).one()
        # (0.8 + 40/100) / 2 = 0.6, not (0.8 + 40) / 2 = 20.4.
        self.assertAlmostEqual(float(final_score), 0.6, places=4)
        # The stored llm_fit_score column itself stays human-readable 0-100.
        self.assertAlmostEqual(float(stored_llm_fit_score), 40.0, places=4)

    def test_a_zero_weight_on_every_present_component_falls_back_to_equal_weight(
        self,
    ) -> None:
        # A grid search can legitimately fit 0.0 for a component -- if every
        # component actually present on a job got fitted 0.0 (e.g. a job
        # with only vector_similarity, and vector_similarity's fitted
        # weight is 0.0), weight_sum must not be 0 and divide-by-zero the
        # whole run. Falls back to equal weight over present components,
        # the same fallback used when no weights are fitted at all.
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.weight (user_id, component, weight) "
                    "VALUES (:u, 'vector_similarity', 0.0), "
                    "(:u, 'skill_coverage', 0.0)"
                ),
                {"u": self.user_id},
            )
        compute_final_scores(self.app_engine, self.user_id)
        self.assertAlmostEqual(self._final(), 0.7, places=4)

    def test_a_fitted_weight_overrides_the_default(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.weight (user_id, component, weight) "
                    "VALUES (:u, 'vector_similarity', 0.9), "
                    "(:u, 'skill_coverage', 0.1)"
                ),
                {"u": self.user_id},
            )
        compute_final_scores(self.app_engine, self.user_id)
        self.assertAlmostEqual(self._final(), 0.8 * 0.9 + 0.6 * 0.1, places=4)


if __name__ == "__main__":
    unittest.main()
