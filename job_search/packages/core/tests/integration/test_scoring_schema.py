"""Schema tests for the scoring schema (migration 0028)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine


class TestScoringSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture scoring user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            for table in (
                "scoring.weight",
                "scoring.job_score",
                "scoring.cv_chunk_embedding",
                "scoring.user_preference",
            ):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE user_id = :id"),
                    {"id": self.user_id},
                )
            conn.execute(
                text(
                    "DELETE FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id LIKE 'fixture-job-%'"
                )
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_user_preference_defaults_to_no_filters(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO scoring.user_preference (user_id) VALUES (:id)"),
                {"id": self.user_id},
            )
            row = conn.execute(
                text(
                    "SELECT preferred_locations, remote_ok, contract_types, "
                    "excluded_ir35_statuses, min_seniority_band, max_seniority_band, "
                    "min_salary_annual, min_rate_daily, max_posting_age_days "
                    "FROM scoring.user_preference WHERE user_id = :id"
                ),
                {"id": self.user_id},
            ).one()
        self.assertEqual(
            (
                list(row.preferred_locations),
                row.remote_ok,
                list(row.contract_types),
                list(row.excluded_ir35_statuses),
                row.min_seniority_band,
                row.max_seniority_band,
                row.min_salary_annual,
                row.min_rate_daily,
                row.max_posting_age_days,
            ),
            ([], "no_preference", [], [], None, None, None, None, None),
        )

    def test_user_preference_rejects_a_bad_remote_ok_value(self) -> None:
        with self.engine.begin() as conn, self.assertRaises(Exception):
            conn.execute(
                text(
                    "INSERT INTO scoring.user_preference (user_id, remote_ok) "
                    "VALUES (:id, 'sometimes')"
                ),
                {"id": self.user_id},
            )

    def test_job_chunk_embedding_has_no_rls_and_a_composite_key(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding "
                    "(job_group_id, section, chunk_index, chunk_text, embedding, "
                    "embedding_model) VALUES "
                    "('fixture-job-1', 'responsibilities', 0, 'zzfixture text', "
                    "CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"v": "[" + ",".join(["0.0"] * 768) + "]"},
            )
            count = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-1'"
                )
            ).scalar_one()
        self.assertEqual(count, 1)

    def test_job_score_and_weight_are_keyed_on_user_and_job(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:id, 'fixture-job-2', true)"
                ),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.weight (user_id, component, weight) "
                    "VALUES (:id, 'vector_similarity', 0.25)"
                ),
                {"id": self.user_id},
            )
            score = conn.execute(
                text(
                    "SELECT hard_filter_passed FROM scoring.job_score "
                    "WHERE user_id = :id AND job_group_id = 'fixture-job-2'"
                ),
                {"id": self.user_id},
            ).scalar_one()
        self.assertTrue(score)


if __name__ == "__main__":
    unittest.main()
