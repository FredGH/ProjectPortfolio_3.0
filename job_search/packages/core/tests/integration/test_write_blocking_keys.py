from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_blocking_keys import write_blocking_keys

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


class TestWriteBlockingKeys(unittest.TestCase):
    """Integration test against a real Postgres instance."""

    def setUp(self) -> None:
        # NOTE: deviates from the task brief, which inserted this fixture
        # row into intermediate.int_jobs__unioned. write_blocking_keys
        # reads from silver.silver__job_posting, which dbt materialises
        # as a `table` (dbt_project.yml), not a view — so a row inserted
        # only into int_jobs__unioned would never appear there without an
        # intervening `dbt run`, and the test would fail with
        # NoResultFound regardless of write_blocking_keys's correctness.
        # Inserting straight into silver.silver__job_posting (the table
        # this function actually reads) is the correct fixture for what
        # this test is checking.
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
                    "'https://x', 'api', 'Senior Data Engineer', "
                    "'Acme Ltd', 'London', 'A test description.', "
                    "NULL, now())"
                ),
                {"job_key": self.job_key},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM dedup.job_blocking_keys " "WHERE job_key = :job_key"),
                {"job_key": self.job_key},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__job_posting " "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            )
        self.engine.dispose()

    def test_writes_one_row_per_posting(self) -> None:
        written = write_blocking_keys(self.engine)
        self.assertGreaterEqual(written, 1)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT normalised_company, matching_title, block_key "
                    "FROM dedup.job_blocking_keys WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).one()
        # normalise_company (Task 2, core.normalisation.company) strips
        # legal-entity suffixes like "Ltd" by design — see its docstring —
        # so "Acme Ltd" correctly normalises to "Acme", not "Acme Ltd" as
        # the task brief's original assertion stated.
        self.assertEqual(row.normalised_company, "Acme")
        self.assertEqual(row.matching_title, "Data Engineer")

    def test_rerun_upserts_rather_than_duplicating(self) -> None:
        write_blocking_keys(self.engine)
        write_blocking_keys(self.engine)

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM dedup.job_blocking_keys "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).scalar_one()
        self.assertEqual(count, 1)
