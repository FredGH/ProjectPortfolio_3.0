from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.db.session import build_engine  # noqa: E402

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
_APP_DSN = "postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search"


def _insert_dim_job(conn, **overrides: object) -> None:
    """Insert one minimal `gold.dim_job` test row.

    Args:
        conn: An open connection on the owner engine, inside a
            transaction the caller manages.
        **overrides: Column values to use instead of this helper's
            defaults — always includes `job_group_id`.
    """
    values = {
        "job_group_id": None,
        "title_for_display": "Data Engineer",
        "title_raw": "Data Engineer",
        "company": "Acme Ltd",
        "location": "London",
        "description": "A test description.",
        "category": "data_engineer",
        "category_confidence": 0.9,
        "category_method": "rules",
        "qa_category": "data_engineer",
        "seniority_band": "mid",
    }
    values.update(overrides)
    conn.execute(
        text(
            """
            INSERT INTO gold.dim_job (
                job_group_id, title_for_display, title_raw, company,
                location, description, category, category_confidence,
                category_method, qa_category, seniority_band
            ) VALUES (
                :job_group_id, :title_for_display, :title_raw, :company,
                :location, :description, :category, :category_confidence,
                :category_method, :qa_category, :seniority_band
            )
            """
        ),
        values,
    )


class TestJobsToReviewAndReviews(unittest.TestCase):
    """Integration tests against real Postgres — no mocking the database."""

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        self.job_group_id = f"test-job-{uuid.uuid4().hex}"
        with self.owner_engine.begin() as conn:
            _insert_dim_job(conn, job_group_id=self.job_group_id)

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM classification.category_review_labels "
                    "WHERE job_group_id = :id"
                ),
                {"id": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = :id"),
                {"id": self.job_group_id},
            )
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_jobs_to_review_includes_the_seeded_unreviewed_job(self) -> None:
        # This dev database already carries ~2.6k real dim_job rows spread
        # across (category, category_method) buckets, so a small `limit`
        # has no guaranteed chance of drawing this test's single freshly
        # seeded job. Use a `limit` large enough that every bucket's
        # per-bucket quota comfortably covers its full population.
        response = self.client.get(
            "/classification/jobs-to-review", params={"limit": 100_000}
        )
        self.assertEqual(response.status_code, 200)
        ids = {job["job_group_id"] for job in response.json()}
        self.assertIn(self.job_group_id, ids)

    def test_jobs_to_review_excludes_rows_with_no_category(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE gold.dim_job SET category = NULL "
                    "WHERE job_group_id = :id"
                ),
                {"id": self.job_group_id},
            )
        response = self.client.get(
            "/classification/jobs-to-review", params={"limit": 100_000}
        )
        ids = {job["job_group_id"] for job in response.json()}
        self.assertNotIn(self.job_group_id, ids)

    def test_reviewing_a_job_removes_it_from_jobs_to_review(self) -> None:
        post_response = self.client.post(
            "/classification/reviews",
            json={
                "job_group_id": self.job_group_id,
                "reviewed_category": "data_engineer",
                "reviewed_seniority_band": "mid",
                "reviewed_by": "test-user",
            },
        )
        self.assertEqual(post_response.status_code, 200)

        get_response = self.client.get(
            "/classification/jobs-to-review", params={"limit": 100_000}
        )
        ids = {job["job_group_id"] for job in get_response.json()}
        self.assertNotIn(self.job_group_id, ids)

    def test_review_round_trips(self) -> None:
        self.client.post(
            "/classification/reviews",
            json={
                "job_group_id": self.job_group_id,
                "reviewed_category": "software_engineer",
                "reviewed_seniority_band": "senior",
                "reviewed_by": "test-user",
                "notes": "actually a software role",
            },
        )
        with self.owner_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT reviewed_category, reviewed_seniority_band, "
                    "reviewed_by, notes FROM classification.category_review_labels "
                    "WHERE job_group_id = :id"
                ),
                {"id": self.job_group_id},
            ).one()
        self.assertEqual(row.reviewed_category, "software_engineer")
        self.assertEqual(row.reviewed_seniority_band, "senior")
        self.assertEqual(row.reviewed_by, "test-user")
        self.assertEqual(row.notes, "actually a software role")

    def test_re_reviewing_upserts_rather_than_duplicating(self) -> None:
        for category in ("data_engineer", "software_engineer"):
            self.client.post(
                "/classification/reviews",
                json={
                    "job_group_id": self.job_group_id,
                    "reviewed_category": category,
                    "reviewed_seniority_band": "mid",
                },
            )
        with self.owner_engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM classification.category_review_labels "
                    "WHERE job_group_id = :id"
                ),
                {"id": self.job_group_id},
            ).scalar_one()
            row = conn.execute(
                text(
                    "SELECT reviewed_category FROM "
                    "classification.category_review_labels WHERE job_group_id = :id"
                ),
                {"id": self.job_group_id},
            ).one()
        self.assertEqual(count, 1)
        self.assertEqual(row.reviewed_category, "software_engineer")

    def test_invalid_category_is_rejected_with_422(self) -> None:
        response = self.client.post(
            "/classification/reviews",
            json={
                "job_group_id": self.job_group_id,
                "reviewed_category": "not_a_real_category",
                "reviewed_seniority_band": "mid",
            },
        )
        self.assertEqual(response.status_code, 422)
        with self.owner_engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM classification.category_review_labels "
                    "WHERE job_group_id = :id"
                ),
                {"id": self.job_group_id},
            ).scalar_one()
        self.assertEqual(count, 0)


class TestReviewSummary(unittest.TestCase):
    """Integration tests for GET /classification/review-summary."""

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.agreeing_id = f"test-agree-{suffix}"
        self.disagreeing_id = f"test-disagree-{suffix}"
        with self.owner_engine.begin() as conn:
            _insert_dim_job(
                conn,
                job_group_id=self.agreeing_id,
                category="data_engineer",
                category_method="rules",
            )
            _insert_dim_job(
                conn,
                job_group_id=self.disagreeing_id,
                category="other",
                category_method="llm",
            )
            conn.execute(
                text(
                    "INSERT INTO classification.category_review_labels "
                    "(job_group_id, reviewed_category, reviewed_seniority_band) "
                    "VALUES (:id, 'data_engineer', 'mid')"
                ),
                {"id": self.agreeing_id},
            )
            conn.execute(
                text(
                    "INSERT INTO classification.category_review_labels "
                    "(job_group_id, reviewed_category, reviewed_seniority_band) "
                    "VALUES (:id, 'software_engineer', 'mid')"
                ),
                {"id": self.disagreeing_id},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM classification.category_review_labels "
                    "WHERE job_group_id IN (:a, :b)"
                ),
                {"a": self.agreeing_id, "b": self.disagreeing_id},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id IN (:a, :b)"),
                {"a": self.agreeing_id, "b": self.disagreeing_id},
            )
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_summary_counts_agreement_live_against_current_category(self) -> None:
        response = self.client.get("/classification/review-summary")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # This dev database already has other real reviews recorded by
        # real users, so assert on deltas this test controls rather than
        # exact totals.
        self.assertGreaterEqual(body["reviewed_count"], 2)
        self.assertGreaterEqual(body["agree_count"], 1)

    def test_summary_reflects_a_dbt_rerun_changing_category(self) -> None:
        # silver.job_category (and therefore dim_job.category) is
        # UPSERTed, not immutable — simulates a `classify-jobs` rerun
        # changing the disagreeing job's category to match the human
        # review after the fact, and asserts the summary picks this up
        # live rather than from a stale stored flag.
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE gold.dim_job SET category = 'software_engineer' "
                    "WHERE job_group_id = :id"
                ),
                {"id": self.disagreeing_id},
            )
        response = self.client.get("/classification/review-summary")
        by_category = {row["key"]: row for row in response.json()["by_category"]}
        self.assertIn("software_engineer", by_category)
        self.assertGreaterEqual(by_category["software_engineer"]["agree_count"], 1)


if __name__ == "__main__":
    unittest.main()
