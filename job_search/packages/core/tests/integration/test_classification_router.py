from __future__ import annotations

import json
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
        "country_iso": None,
        "region": None,
        "description": "A test description.",
        "category": "data_engineer",
        "category_confidence": 0.9,
        "category_method": "rules",
        "qa_category": "data_engineer",
        "seniority_band": "mid",
        "sources": None,
    }
    values.update(overrides)
    if values["sources"] is not None:
        values["sources"] = json.dumps(values["sources"])
    conn.execute(
        text(
            """
            INSERT INTO gold.dim_job (
                job_group_id, title_for_display, title_raw, company,
                location, country_iso, region, description, category,
                category_confidence, category_method, qa_category,
                seniority_band, sources
            ) VALUES (
                :job_group_id, :title_for_display, :title_raw, :company,
                :location, :country_iso, :region, :description, :category,
                :category_confidence, :category_method, :qa_category,
                :seniority_band, CAST(:sources AS jsonb)
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
        ids = {job["job_group_id"] for job in response.json()["jobs"]}
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
        ids = {job["job_group_id"] for job in response.json()["jobs"]}
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
        ids = {job["job_group_id"] for job in get_response.json()["jobs"]}
        self.assertNotIn(self.job_group_id, ids)

    def test_reviewing_a_job_decreases_total_unreviewed_count(self) -> None:
        # This dev database sees concurrent pipeline activity (see the
        # class docstring's note on ~2.6k real rows), so assert the
        # direction of the change rather than an exact delta.
        before = self.client.get(
            "/classification/jobs-to-review", params={"limit": 1}
        ).json()["total_unreviewed_count"]

        self.client.post(
            "/classification/reviews",
            json={
                "job_group_id": self.job_group_id,
                "reviewed_category": "data_engineer",
                "reviewed_seniority_band": "mid",
            },
        )

        after = self.client.get(
            "/classification/jobs-to-review", params={"limit": 1}
        ).json()["total_unreviewed_count"]
        self.assertLess(after, before)

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


class TestJobsToReviewCountryFilter(unittest.TestCase):
    """Integration tests for `country_iso`-filtering on the review endpoints."""

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.gb_id = f"test-gb-{suffix}"
        self.us_id = f"test-us-{suffix}"
        self.unresolved_id = f"test-unresolved-{suffix}"
        with self.owner_engine.begin() as conn:
            _insert_dim_job(
                conn, job_group_id=self.gb_id, country_iso="GB", region="UKI"
            )
            _insert_dim_job(conn, job_group_id=self.us_id, country_iso="US")
            _insert_dim_job(conn, job_group_id=self.unresolved_id, country_iso=None)

    def tearDown(self) -> None:
        ids = (self.gb_id, self.us_id, self.unresolved_id)
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM classification.category_review_labels "
                    "WHERE job_group_id = ANY(:ids)"
                ),
                {"ids": list(ids)},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = ANY(:ids)"),
                {"ids": list(ids)},
            )
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_jobs_to_review_filters_to_one_country(self) -> None:
        response = self.client.get(
            "/classification/jobs-to-review",
            params={"limit": 100_000, "country_iso": "GB"},
        )
        ids = {job["job_group_id"] for job in response.json()["jobs"]}
        self.assertIn(self.gb_id, ids)
        self.assertNotIn(self.us_id, ids)
        self.assertNotIn(self.unresolved_id, ids)

    def test_jobs_to_review_filters_to_unresolved_country_with_sentinel(self) -> None:
        response = self.client.get(
            "/classification/jobs-to-review",
            params={"limit": 100_000, "country_iso": "__unknown__"},
        )
        ids = {job["job_group_id"] for job in response.json()["jobs"]}
        self.assertIn(self.unresolved_id, ids)
        self.assertNotIn(self.gb_id, ids)
        self.assertNotIn(self.us_id, ids)

    def test_jobs_to_review_reports_country_iso_and_region(self) -> None:
        response = self.client.get(
            "/classification/jobs-to-review",
            params={"limit": 100_000, "country_iso": "GB"},
        )
        job = next(
            j for j in response.json()["jobs"] if j["job_group_id"] == self.gb_id
        )
        self.assertEqual(job["country_iso"], "GB")
        self.assertEqual(job["region"], "UKI")

    def test_regions_lists_country_iso_with_unreviewed_counts(self) -> None:
        response = self.client.get("/classification/regions")
        self.assertEqual(response.status_code, 200)
        by_country = {row["country_iso"]: row for row in response.json()}
        self.assertIn("GB", by_country)
        self.assertGreaterEqual(by_country["GB"]["unreviewed_count"], 1)
        self.assertIn("US", by_country)
        self.assertGreaterEqual(by_country["US"]["unreviewed_count"], 1)
        # Unresolved rows (country_iso IS NULL) are reported under a
        # `None` key rather than dropped from the listing.
        self.assertIn(None, by_country)

    def test_jobs_to_review_treats_an_explicit_none_param_as_no_filter(self) -> None:
        # httpx (what both TestClient and the Streamlit UI use)
        # serializes a `country_iso=None` params entry as an empty
        # string ("?country_iso="), not an omitted param — FastAPI's
        # Query(default=None) then sees "", not None. A caller that
        # always includes the key (rather than omitting it for "no
        # filter") must still get the unfiltered result, matching a
        # request that omits the param entirely.
        with_none = self.client.get(
            "/classification/jobs-to-review",
            params={"limit": 100_000, "country_iso": None},
        )
        omitted = self.client.get(
            "/classification/jobs-to-review", params={"limit": 100_000}
        )
        with_none_ids = {j["job_group_id"] for j in with_none.json()["jobs"]}
        omitted_ids = {j["job_group_id"] for j in omitted.json()["jobs"]}
        self.assertEqual(with_none_ids, omitted_ids)
        self.assertIn(self.gb_id, with_none_ids)
        self.assertIn(self.us_id, with_none_ids)
        self.assertIn(self.unresolved_id, with_none_ids)


class TestJobsToReviewJoobleExclusion(unittest.TestCase):
    """Integration tests for excluding Jooble-only rows from review.

    Jooble's search API only ever returns a short pre-truncated
    snippet, never a full description (see JoobleConnector's
    docstring), so a job whose surviving description came only from
    Jooble carries too little text to meaningfully review or classify.
    """

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.jooble_only_id = f"test-jooble-only-{suffix}"
        self.jooble_and_other_id = f"test-jooble-other-{suffix}"
        self.no_sources_id = f"test-no-sources-{suffix}"
        with self.owner_engine.begin() as conn:
            _insert_dim_job(
                conn,
                job_group_id=self.jooble_only_id,
                sources=[{"source_name": "jooble", "job_url": "https://x"}],
            )
            _insert_dim_job(
                conn,
                job_group_id=self.jooble_and_other_id,
                sources=[
                    {"source_name": "jooble", "job_url": "https://x"},
                    {"source_name": "greenhouse", "job_url": "https://y"},
                ],
            )
            _insert_dim_job(conn, job_group_id=self.no_sources_id, sources=None)

    def tearDown(self) -> None:
        ids = (self.jooble_only_id, self.jooble_and_other_id, self.no_sources_id)
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM classification.category_review_labels "
                    "WHERE job_group_id = ANY(:ids)"
                ),
                {"ids": list(ids)},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = ANY(:ids)"),
                {"ids": list(ids)},
            )
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_excludes_a_job_whose_only_source_is_jooble(self) -> None:
        response = self.client.get(
            "/classification/jobs-to-review", params={"limit": 100_000}
        )
        ids = {job["job_group_id"] for job in response.json()["jobs"]}
        self.assertNotIn(self.jooble_only_id, ids)

    def test_keeps_a_job_with_jooble_plus_another_source(self) -> None:
        response = self.client.get(
            "/classification/jobs-to-review", params={"limit": 100_000}
        )
        ids = {job["job_group_id"] for job in response.json()["jobs"]}
        self.assertIn(self.jooble_and_other_id, ids)

    def test_keeps_a_job_with_no_sources_recorded(self) -> None:
        response = self.client.get(
            "/classification/jobs-to-review", params={"limit": 100_000}
        )
        ids = {job["job_group_id"] for job in response.json()["jobs"]}
        self.assertIn(self.no_sources_id, ids)


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
                country_iso="GB",
            )
            _insert_dim_job(
                conn,
                job_group_id=self.disagreeing_id,
                category="other",
                category_method="llm",
                country_iso="US",
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

    def test_summary_scopes_to_one_country_when_filtered(self) -> None:
        unfiltered = self.client.get("/classification/review-summary").json()
        gb_filtered = self.client.get(
            "/classification/review-summary", params={"country_iso": "GB"}
        ).json()
        # self.agreeing_id (GB) must be counted; self.disagreeing_id (US)
        # must not, so a GB-scoped total excludes at least that one row
        # from the unfiltered total — strictly less, not just <=, so an
        # unfiltered `country_iso` param (ignored rather than applied)
        # can't accidentally satisfy this assertion.
        self.assertGreaterEqual(gb_filtered["reviewed_count"], 1)
        self.assertLess(gb_filtered["reviewed_count"], unfiltered["reviewed_count"])

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
