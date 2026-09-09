from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_job_survivorship import write_job_survivorship

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


class TestWriteJobSurvivorship(unittest.TestCase):
    """Integration test against a real Postgres instance.

    Inserts two fixture postings in the same job_group_id directly into
    silver.silver__job_posting (a dbt table) and silver.job_identity_map
    (safe here since nothing runs `dbt run` mid-test, matching the
    established test_write_job_identity_map.py pattern), then exercises
    the real write path.
    """

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.suffix = uuid.uuid4().hex
        self.job_group_id = f"group-{self.suffix}"
        self.job_key_greenhouse = f"gh-{self.suffix}"
        self.job_key_reed = f"reed-{self.suffix}"
        with self.engine.begin() as conn:
            # Greenhouse: shorter description, but wins apply_url via
            # source rank (greenhouse=0 beats reed=1).
            conn.execute(
                text(
                    "INSERT INTO silver.silver__job_posting "
                    "(job_key, source_name, source_job_id, job_url, "
                    "job_url_canonical, entry_method, title, company, "
                    "location, description, salary_raw, posted_at) "
                    "VALUES (:job_key, 'greenhouse', :source_job_id, "
                    "'https://greenhouse.example/job', "
                    "'https://greenhouse.example/job', 'api', "
                    "'Senior Data Engineer (m/f/d)', 'Test Co', 'London', "
                    "'short desc', NULL, now())"
                ),
                {
                    "job_key": self.job_key_greenhouse,
                    "source_job_id": f"gh-src-{self.suffix}",
                },
            )
            # Reed: longer description, but loses apply_url via source
            # rank — proves the two rules pick independently.
            conn.execute(
                text(
                    "INSERT INTO silver.silver__job_posting "
                    "(job_key, source_name, source_job_id, job_url, "
                    "job_url_canonical, entry_method, title, company, "
                    "location, description, salary_raw, posted_at) "
                    "VALUES (:job_key, 'reed', :source_job_id, "
                    "'https://reed.example/job', "
                    "'https://reed.example/job', 'api', "
                    "'Senior Data Engineer', 'Test Co', 'London', "
                    "'a much longer description than the other one', "
                    "NULL, now())"
                ),
                {
                    "job_key": self.job_key_reed,
                    "source_job_id": f"reed-src-{self.suffix}",
                },
            )
            for job_key, source_name, source_job_id in (
                (self.job_key_greenhouse, "greenhouse", f"gh-src-{self.suffix}"),
                (self.job_key_reed, "reed", f"reed-src-{self.suffix}"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO silver.job_identity_map "
                        "(source_name, source_job_id, job_group_id, "
                        "match_method, confidence) "
                        "VALUES (:source_name, :source_job_id, :job_group_id, "
                        "'fuzzy', 0.9)"
                    ),
                    {
                        "source_name": source_name,
                        "source_job_id": source_job_id,
                        "job_group_id": self.job_group_id,
                    },
                )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM silver.job_survivorship WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM silver.job_identity_map WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__job_posting WHERE job_key IN (:a, :b)"
                ),
                {"a": self.job_key_greenhouse, "b": self.job_key_reed},
            )
        self.engine.dispose()

    def test_resolves_description_and_apply_url_independently(self) -> None:
        write_job_survivorship(self.engine)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT winning_description, apply_source_name, "
                    "apply_job_url, apply_title_for_display "
                    "FROM silver.job_survivorship WHERE job_group_id = :g"
                ),
                {"g": self.job_group_id},
            ).one()

        self.assertEqual(
            row.winning_description, "a much longer description than the other one"
        )
        self.assertEqual(row.apply_source_name, "greenhouse")
        self.assertEqual(row.apply_job_url, "https://greenhouse.example/job")
        # title_for_display strips "(m/f/d)" but keeps "Senior" —
        # DECISIONS.md §5, computed here via core.normalisation.title.
        self.assertEqual(row.apply_title_for_display, "Senior Data Engineer")

    def test_rerun_upserts_rather_than_duplicating(self) -> None:
        write_job_survivorship(self.engine)
        write_job_survivorship(self.engine)

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM silver.job_survivorship "
                    "WHERE job_group_id = :g"
                ),
                {"g": self.job_group_id},
            ).scalar_one()

        self.assertEqual(count, 1)
