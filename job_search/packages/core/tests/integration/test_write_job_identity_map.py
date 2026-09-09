from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_job_identity_map import write_job_identity_map

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


class TestWriteJobIdentityMap(unittest.TestCase):
    """Integration test against a real Postgres instance.

    Inserts fixture rows directly into silver.silver__job_posting and
    dedup.dedup__similarity_scores (both dbt tables — safe here since
    nothing runs `dbt run` mid-test, matching the existing
    test_write_title_similarity_scores.py pattern), then exercises the
    real write path.
    """

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.suffix = uuid.uuid4().hex
        self.job_key_a = f"test-a-{self.suffix}"
        self.job_key_b = f"test-b-{self.suffix}"
        self.source_a = f"src-a-{self.suffix}"
        self.source_b = f"src-b-{self.suffix}"
        with self.engine.begin() as conn:
            for job_key, source_job_id in (
                (self.job_key_a, self.source_a),
                (self.job_key_b, self.source_b),
            ):
                conn.execute(
                    text(
                        "INSERT INTO silver.silver__job_posting "
                        "(job_key, source_name, source_job_id, job_url, "
                        "job_url_canonical, entry_method, title, company, "
                        "location, description, salary_raw, posted_at) "
                        "VALUES (:job_key, 'manual', :source_job_id, "
                        "'https://example.com/' || :source_job_id, "
                        "'https://example.com/' || :source_job_id, "
                        "'manual', 'Data Engineer', 'Test Co', 'London', "
                        "'A description', NULL, now())"
                    ),
                    {"job_key": job_key, "source_job_id": source_job_id},
                )
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__similarity_scores "
                    "(job_key_a, job_key_b, match_type, company_similarity, "
                    "title_similarity, description_similarity, "
                    "location_similarity, date_diff_days, date_similarity, "
                    "salary_similarity, hard_veto, blended_score) "
                    "VALUES (:a, :b, 'block', 1.0, 1.0, 1.0, 1.0, 0.0, 1.0, "
                    "1.0, false, 0.95)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM silver.job_identity_map "
                    "WHERE source_job_id IN (:a, :b)"
                ),
                {"a": self.source_a, "b": self.source_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.dedup__similarity_scores "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__job_posting "
                    "WHERE job_key IN (:a, :b)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
        self.engine.dispose()

    def test_writes_both_jobs_into_the_same_group(self) -> None:
        # 0.95 clears the real calibrated threshold (0.81 as of Step 9)
        # comfortably, so this exercises the ordinary fuzzy-match path
        # without needing to know the exact live threshold value.
        write_job_identity_map(self.engine)

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source_job_id, job_group_id, match_method, "
                    "is_manual_override "
                    "FROM silver.job_identity_map "
                    "WHERE source_job_id IN (:a, :b)"
                ),
                {"a": self.source_a, "b": self.source_b},
            ).all()
        self.assertEqual(len(rows), 2)
        group_ids = {row.job_group_id for row in rows}
        self.assertEqual(len(group_ids), 1)
        self.assertTrue(all(row.match_method == "fuzzy" for row in rows))
        self.assertTrue(all(row.is_manual_override is False for row in rows))

    def test_manual_not_match_label_blocks_the_merge_despite_high_score(self) -> None:
        # Step 9's whole point: a human can override a high automatic
        # score. Without this label the fixture pair (blended_score
        # 0.95) would merge, per the test above.
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dedup.pair_labels (job_key_a, job_key_b, label) "
                    "VALUES (:a, :b, 'not_match')"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

        write_job_identity_map(self.engine)

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source_job_id, job_group_id "
                    "FROM silver.job_identity_map "
                    "WHERE source_job_id IN (:a, :b)"
                ),
                {"a": self.source_a, "b": self.source_b},
            ).all()
        self.assertEqual(len(rows), 2)
        group_ids = {row.job_group_id for row in rows}
        self.assertEqual(len(group_ids), 2, "not_match must prevent the merge")

    def test_rerun_produces_byte_identical_job_group_id(self) -> None:
        write_job_identity_map(self.engine)
        with self.engine.connect() as conn:
            first_run = dict(
                conn.execute(
                    text(
                        "SELECT source_job_id, job_group_id "
                        "FROM silver.job_identity_map "
                        "WHERE source_job_id IN (:a, :b)"
                    ),
                    {"a": self.source_a, "b": self.source_b},
                ).all()
            )

        second_written = write_job_identity_map(self.engine)

        with self.engine.connect() as conn:
            second_run = dict(
                conn.execute(
                    text(
                        "SELECT source_job_id, job_group_id "
                        "FROM silver.job_identity_map "
                        "WHERE source_job_id IN (:a, :b)"
                    ),
                    {"a": self.source_a, "b": self.source_b},
                ).all()
            )

        self.assertEqual(second_written, 0)
        self.assertEqual(first_run, second_run)
