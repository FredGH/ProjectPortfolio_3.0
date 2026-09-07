from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.enrichment.write_engagement_terms import write_engagement_terms

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


class TestWriteEngagementTerms(unittest.TestCase):
    """Integration test against a real Postgres instance."""

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.job_key = f"test-{uuid.uuid4().hex}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO intermediate.int_jobs__unioned "
                    "(job_key, source_name, source_job_id, job_url, "
                    "job_url_canonical, entry_method, title, company, "
                    "location, description, salary_raw, posted_at, "
                    "fetched_at, run_id, payload_sha256) VALUES "
                    "(:job_key, 'test_source', :job_key, 'https://x', "
                    "'https://x', 'api', 'Test Role', 'Test Co', 'London', "
                    ":description, :salary_raw, now(), now(), 'run-1', "
                    "'sha-1')"
                ),
                {
                    "job_key": self.job_key,
                    "description": "6 month contract, outside IR35, umbrella.",
                    "salary_raw": None,
                },
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM silver.job_engagement_terms "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            )
            conn.execute(
                text(
                    "DELETE FROM intermediate.int_jobs__unioned "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            )
        self.engine.dispose()

    def test_writes_one_row_per_unioned_job(self) -> None:
        written = write_engagement_terms(self.engine)
        self.assertGreaterEqual(written, 1)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT engagement_type, ir35_status, engagement_vehicle "
                    "FROM silver.job_engagement_terms WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).one()
        self.assertEqual(row.engagement_type, "contract")
        self.assertEqual(row.ir35_status, "outside")
        self.assertEqual(row.engagement_vehicle, "umbrella")

    def test_rerun_upserts_rather_than_duplicating(self) -> None:
        write_engagement_terms(self.engine)
        write_engagement_terms(self.engine)

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM silver.job_engagement_terms "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).scalar_one()
        self.assertEqual(count, 1)
