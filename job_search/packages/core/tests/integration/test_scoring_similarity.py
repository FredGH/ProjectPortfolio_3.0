"""Integration tests for core.scoring.similarity against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.scoring.similarity import run_similarity
from core.settings import get_settings


class TestSimilarity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.job_a = "fixture-job-sim-a"  # aligned with the CV
        self.job_b = "fixture-job-sim-b"  # embedded under a different model
        self.job_c = "fixture-job-sim-c"  # same model, lower similarity than a
        self.job_d = "fixture-job-sim-d"  # same model, orthogonal (cosine 0.0)
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture sim user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            for job in (self.job_a, self.job_b):
                conn.execute(
                    text(
                        "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                        "company) VALUES (:j, 'zzfixture role', 'zzfixture co')"
                    ),
                    {"j": job},
                )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding (job_group_id, section, "
                    "chunk_index, chunk_text, embedding, embedding_model) VALUES "
                    "(:j, 'responsibilities', 0, 'zzfixture', "
                    "CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"j": self.job_a, "v": "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding (job_group_id, section, "
                    "chunk_index, chunk_text, embedding, embedding_model) VALUES "
                    "(:j, 'responsibilities', 0, 'zzfixture', "
                    "CAST(:v AS vector), 'a-different-model')"
                ),
                {"j": self.job_b, "v": "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, :j, true)"
                ),
                {"u": self.user_id, "j": self.job_a},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, :j, true)"
                ),
                {"u": self.user_id, "j": self.job_b},
            )
        with self.app_engine.begin() as conn:
            # `SET` does not support bound parameters (psycopg raises a
            # syntax error on `SET app.current_user_id = $1`), so the
            # validated UUID is interpolated directly, matching
            # core.db.session.session_scope's own safe-interpolation
            # pattern for the same reason.
            conn.execute(text(f"SET app.current_user_id = '{self.user_id}'"))
            conn.execute(
                text(
                    "INSERT INTO scoring.cv_chunk_embedding (user_id, cv_version, "
                    "section, chunk_index, chunk_text, embedding, embedding_model) "
                    "VALUES (:u, 1, 'experience', 0, 'zzfixture', "
                    "CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"u": self.user_id, "v": "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"},
            )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.cv_chunk_embedding WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id LIKE 'fixture-job-sim-%'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id LIKE 'fixture-job-sim-%'"
                )
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _fake_rerank(self, cv_text: str, jd_text: str) -> float:
        return 0.5

    def _insert_candidate_job(self, job_group_id: str, vector_literal: str) -> None:
        """Insert a fixture job with one 'responsibilities' chunk plus its
        `job_score` candidate row, under the CV's own embedding model.

        Args:
            job_group_id: The fixture job's id.
            vector_literal: The pgvector text literal for its one chunk,
                e.g. ``"[1.0,0.0,...]"``.
        """
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding (job_group_id, "
                    "section, chunk_index, chunk_text, embedding, "
                    "embedding_model) VALUES (:j, 'responsibilities', 0, "
                    "'zzfixture', CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"j": job_group_id, "v": vector_literal},
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company) VALUES (:j, 'zzfixture role', 'zzfixture co')"
                ),
                {"j": job_group_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, :j, true)"
                ),
                {"u": self.user_id, "j": job_group_id},
            )

    def test_matching_embedding_model_gets_a_similarity_score(self) -> None:
        run_similarity(
            self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=200
        )
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT vector_similarity_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": self.job_a},
            ).scalar_one()
        self.assertIsNotNone(score)
        self.assertGreater(float(score), 0.9)

    def test_a_model_mismatch_is_skipped_not_compared(self) -> None:
        run_similarity(
            self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=200
        )
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT vector_similarity_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": self.job_b},
            ).scalar_one()
        self.assertIsNone(score)

    def test_an_unpaired_section_is_never_compared(self) -> None:
        # "benefits" has no CV-side counterpart in _SECTION_PAIRS, so a job
        # with ONLY a benefits chunk must get no vector_similarity_score at
        # all, even though a CV chunk exists.
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding (job_group_id, "
                    "section, chunk_index, chunk_text, embedding, embedding_model) "
                    "VALUES ('fixture-job-sim-benefits-only', 'benefits', 0, "
                    "'zzfixture', CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"v": "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"},
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company) VALUES ('fixture-job-sim-benefits-only', "
                    "'zzfixture role', 'zzfixture co')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, 'fixture-job-sim-benefits-only', "
                    "true)"
                ),
                {"u": self.user_id},
            )
        run_similarity(
            self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=200
        )
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT vector_similarity_score FROM scoring.job_score "
                    "WHERE user_id = :u "
                    "AND job_group_id = 'fixture-job-sim-benefits-only'"
                ),
                {"u": self.user_id},
            ).scalar_one()
            conn.execute(
                text(
                    "DELETE FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-sim-benefits-only'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id = 'fixture-job-sim-benefits-only'"
                )
            )
        self.assertIsNone(score)

    def test_reranker_score_only_set_for_the_top_n(self) -> None:
        run_similarity(self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=1)
        with self.owner.connect() as conn:
            reranked = (
                conn.execute(
                    text(
                        "SELECT job_group_id FROM scoring.job_score "
                        "WHERE user_id = :u AND reranker_score IS NOT NULL"
                    ),
                    {"u": self.user_id},
                )
                .scalars()
                .all()
            )
        self.assertEqual(reranked, [self.job_a])

    def test_stale_reranker_score_cleared_when_job_falls_out_of_top_n(self) -> None:
        # job_c matches the CV's embedding model but less closely than job_a,
        # so a smaller top_n on a later run should drop it from the ranking.
        self._insert_candidate_job(
            self.job_c, "[0.6,0.8," + ",".join(["0.0"] * 766) + "]"
        )

        # First run: top_n=2 lets both job_a and job_c get a reranker_score.
        run_similarity(self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=2)
        with self.owner.connect() as conn:
            score_c = conn.execute(
                text(
                    "SELECT reranker_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": self.job_c},
            ).scalar_one()
        self.assertIsNotNone(score_c)

        # Second run: top_n=1 keeps only job_a. job_c's OLD reranker_score
        # must be cleared, not left over as a stale, non-NULL value.
        run_similarity(self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=1)
        with self.owner.connect() as conn:
            score_c = conn.execute(
                text(
                    "SELECT reranker_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": self.job_c},
            ).scalar_one()
        self.assertIsNone(score_c)

    def test_exact_zero_cosine_similarity_is_not_dropped_as_null(self) -> None:
        # job_d's chunk is orthogonal to the CV's (cosine exactly 0.0), under
        # the SAME embedding_model as the CV — a genuinely-computed 0.0, not
        # a model mismatch and not "nothing to compare".
        self._insert_candidate_job(
            self.job_d, "[0.0,1.0," + ",".join(["0.0"] * 766) + "]"
        )
        run_similarity(
            self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=200
        )
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT vector_similarity_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": self.job_d},
            ).scalar_one()
        self.assertIsNotNone(score)
        self.assertAlmostEqual(float(score), 0.0)

    def test_a_headingless_other_section_job_still_gets_a_similarity_score(
        self,
    ) -> None:
        # Finding 7 regression: detect_sections falls back to one 'other'
        # section for a headingless job description (common from
        # aggregators). _SECTION_PAIRS must pair 'other' against the CV's
        # 'experience' section so such a job is not structurally
        # unscoreable regardless of true fit.
        job_other = "fixture-job-sim-other"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding (job_group_id, "
                    "section, chunk_index, chunk_text, embedding, embedding_model) "
                    "VALUES (:j, 'other', 0, 'zzfixture', "
                    "CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"j": job_other, "v": "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"},
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company) VALUES (:j, 'zzfixture role', 'zzfixture co')"
                ),
                {"j": job_other},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, :j, true)"
                ),
                {"u": self.user_id, "j": job_other},
            )
        run_similarity(
            self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=200
        )
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT vector_similarity_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": job_other},
            ).scalar_one()
            conn.execute(
                text("DELETE FROM scoring.job_chunk_embedding WHERE job_group_id = :j"),
                {"j": job_other},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = :j"),
                {"j": job_other},
            )
        self.assertIsNotNone(score)
        self.assertGreater(float(score), 0.9)

    def test_mismatch_count_is_reported_in_the_summary(self) -> None:
        summary = run_similarity(
            self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=200
        )
        self.assertEqual(summary.jobs_scored, 2)  # job_a + job_b considered
        self.assertEqual(summary.mismatched, 1)  # job_b only


if __name__ == "__main__":
    unittest.main()
