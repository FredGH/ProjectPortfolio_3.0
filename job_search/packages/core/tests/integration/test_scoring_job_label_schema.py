"""Schema tests for scoring.job_label and scoring.calibration_run
(PLAN.md Step 16) — confirms RLS, grants, and constraints exist, the
same style as test_scoring_schema.py verifies migration 0028.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine, session_scope
from core.settings import get_settings


class TestJobLabelAndCalibrationRunSchema(unittest.TestCase):
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
                    "VALUES (:id, :email, 'zzfixture calibration schema user')"
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
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_job_label_rejects_an_invalid_label_value(self) -> None:
        with self.assertRaises(Exception):
            with self.owner_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO scoring.job_label (user_id, job_group_id, label) "
                        "VALUES (:u, 'zzfixture-job-1', 'excellent')"
                    ),
                    {"u": self.user_id},
                )

    def test_job_label_is_isolated_by_rls(self) -> None:
        other_user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture other user')"
                ),
                {
                    "id": other_user_id,
                    "email": f"zzfixture-{other_user_id}@example.com",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_label (user_id, job_group_id, label) "
                    "VALUES (:u, 'zzfixture-job-1', 'strong')"
                ),
                {"u": self.user_id},
            )
        try:
            with session_scope(self.app_engine, user_id=other_user_id) as conn:
                rows = conn.execute(text("SELECT * FROM scoring.job_label")).fetchall()
            self.assertEqual(rows, [])
        finally:
            with self.owner_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM app_user WHERE id = :id"), {"id": other_user_id}
                )

    def test_calibration_run_accepts_a_null_holdout_agreement(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.calibration_run "
                    "(user_id, fit_count, holdout_count, vector_similarity_weight, "
                    "reranker_weight, skill_coverage_weight, llm_fit_weight, "
                    "holdout_agreement, embedding_model) "
                    "VALUES (:u, 20, 10, 0.25, 0.25, 0.25, 0.25, NULL, "
                    "'nomic-embed-text')"
                ),
                {"u": self.user_id},
            )
            count = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.calibration_run "
                    "WHERE user_id = :u AND holdout_agreement IS NULL"
                ),
                {"u": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
