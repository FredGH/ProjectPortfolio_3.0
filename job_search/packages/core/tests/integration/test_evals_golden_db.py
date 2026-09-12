"""Integration tests for `core.evals.golden_db`'s DB-backed golden set.

Exercises `load_job_categorisation_golden_set` against a real Postgres
connection — no mocking the database, per this project's testing rules.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.evals.golden import GoldenCase
from core.evals.golden_db import load_job_categorisation_golden_set

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


def _insert_dim_job(conn, **overrides: object) -> None:
    """Insert one fixture row into `gold.dim_job`.

    Args:
        conn: An open SQLAlchemy connection with an active transaction.
        **overrides: Column values overriding the defaults below —
            typically at least `job_group_id`.

    Returns:
        None.
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


class TestLoadJobCategorisationGoldenSet(unittest.TestCase):
    """Integration test against real Postgres — no mocking the database."""

    def setUp(self) -> None:
        """Seed one reviewed `gold.dim_job` / review-labels fixture row.

        Args:
            None.

        Returns:
            None.
        """
        self.engine = build_engine(_OWNER_DSN)
        self.job_group_id = f"test-golden-{uuid.uuid4().hex}"
        with self.engine.begin() as conn:
            _insert_dim_job(
                conn, job_group_id=self.job_group_id, title_raw="Senior Data Engineer"
            )
            conn.execute(
                text(
                    "INSERT INTO classification.category_review_labels "
                    "(job_group_id, reviewed_category, reviewed_seniority_band) "
                    "VALUES (:id, 'data_engineer', 'senior')"
                ),
                {"id": self.job_group_id},
            )

    def tearDown(self) -> None:
        """Delete this test's fixture rows and dispose of the engine.

        Args:
            None.

        Returns:
            None.
        """
        with self.engine.begin() as conn:
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
        self.engine.dispose()

    def test_includes_the_seeded_reviewed_case(self) -> None:
        """Scoping to this fixture's job_group_id returns just that case.

        `classification.category_review_labels` is SHARED, real state in
        this dev database (other tests, and eventually a real JOB-170
        hand-check, write into the same table), so an unscoped load here
        would be asserting against whatever else happens to exist too.

        Args:
            None.

        Returns:
            None.

        Raises:
            AssertionError: If the returned cases don't match exactly
                this test's seeded fixture row.
        """
        cases = load_job_categorisation_golden_set(
            self.engine, job_group_ids=[self.job_group_id]
        )
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertIsInstance(case, GoldenCase)
        self.assertEqual(case.case_id, self.job_group_id)
        self.assertEqual(case.input, {"title": "Senior Data Engineer"})
        self.assertEqual(case.expected, {"category": "data_engineer"})


if __name__ == "__main__":
    unittest.main()
