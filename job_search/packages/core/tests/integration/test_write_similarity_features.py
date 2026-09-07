from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_similarity_features import write_similarity_features

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


class TestWriteSimilarityFeatures(unittest.TestCase):
    """Integration test against a real Postgres instance."""

    def setUp(self) -> None:
        # write_similarity_features reads from silver.silver__job_posting
        # (a dbt table materialization), not intermediate.int_jobs__unioned
        # — the fixture must insert into the table this function actually
        # reads, or the row never appears there without an intervening
        # `dbt run` and the test fails with NoResultFound regardless of
        # correctness (this exact mistake was caught and fixed during the
        # sibling Step 7 plan's Task 3 — same root cause, fixed here too).
        self.engine = build_engine(_OWNER_DSN)
        self.job_key = f"test-{uuid.uuid4().hex}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.silver__job_posting "
                    "(job_key, source_name, source_job_id, job_url, "
                    "job_url_canonical, entry_method, title, company, "
                    "location, description, salary_raw, posted_at) VALUES "
                    "(:job_key, 'test_source', :job_key, 'https://x', "
                    "'https://x', 'api', 'Data Engineer', 'Acme Ltd', "
                    "'London', 'A test description.', "
                    "'£80k - £95k per year', now())"
                ),
                {"job_key": self.job_key},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.job_similarity_features "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__job_posting " "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            )
        self.engine.dispose()

    def test_writes_one_row_with_a_signed_bigint_simhash(self) -> None:
        written = write_similarity_features(self.engine)
        self.assertGreaterEqual(written, 1)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT description_simhash, rate_annualised, "
                    "salary_band FROM dedup.job_similarity_features "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).one()
        # Postgres bigint round-trips as a Python int within its signed
        # range — if the write path failed to convert an unsigned
        # fingerprint >= 2**63, this INSERT would have raised instead of
        # reaching this assertion.
        self.assertIsInstance(row.description_simhash, int)
        self.assertEqual(row.salary_band, "80000-90000")

    def test_rerun_upserts_rather_than_duplicating(self) -> None:
        write_similarity_features(self.engine)
        write_similarity_features(self.engine)

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM dedup.job_similarity_features "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).scalar_one()
        self.assertEqual(count, 1)
