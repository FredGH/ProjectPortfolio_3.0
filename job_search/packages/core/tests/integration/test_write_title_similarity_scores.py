from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_title_similarity_scores import write_title_similarity_scores

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


class TestWriteTitleSimilarityScores(unittest.TestCase):
    """Integration test against a real Postgres instance."""

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        suffix = uuid.uuid4().hex
        self.job_key_a = f"test-a-{suffix}"
        self.job_key_b = f"test-b-{suffix}"
        with self.engine.begin() as conn:
            for job_key, title in (
                (self.job_key_a, "Data Engineer"),
                (self.job_key_b, "Data Engineer"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO dedup.job_blocking_keys "
                        "(job_key, normalised_company, matching_title, "
                        "country_iso, region, block_key, content_sha256) "
                        "VALUES (:job_key, 'Test Co', :title, NULL, NULL, "
                        "'Test Co|Data Engine|', 'sha-' || :job_key)"
                    ),
                    {"job_key": job_key, "title": title},
                )
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__candidate_pairs "
                    "(job_key_a, job_key_b, match_type) "
                    "VALUES (:a, :b, 'block')"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.pair_title_scores "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.dedup__candidate_pairs "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.job_blocking_keys " "WHERE job_key IN (:a, :b)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
        self.engine.dispose()

    def test_writes_a_perfect_score_for_identical_titles(self) -> None:
        write_title_similarity_scores(self.engine)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT title_token_set_ratio FROM dedup.pair_title_scores "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            ).one()
        self.assertEqual(float(row.title_token_set_ratio), 1.0)
