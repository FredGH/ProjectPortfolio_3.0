"""Integration tests for core.scoring.llm_rerank against live Postgres.

Only the Anthropic adapter is faked.
"""

from __future__ import annotations

import json
import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.llm.types import LLMResponse
from core.scoring.llm_rerank import run_llm_rerank
from core.settings import get_settings


class _FakeAdapter:
    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        return LLMResponse(
            text=json.dumps(
                {
                    "fit_score": 80,
                    "rationale": "zzfixture rationale",
                    "missing_skills": ["Kubernetes"],
                    "stretch_flag": False,
                }
            ),
            provider="anthropic",
            model=model,
            input_tokens=10,
            output_tokens=10,
        )


class _MalformedAdapter:
    """Always returns unparseable JSON — simulates a bad LLM reply."""

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        return LLMResponse(
            text="not json at all",
            provider="anthropic",
            model=model,
            input_tokens=10,
            output_tokens=10,
        )


class TestLlmRerank(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture rerank user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            for i in range(3):
                job = f"fixture-job-rerank-{i}"
                conn.execute(
                    text(
                        "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                        "company) VALUES (:j, 'zzfixture role', 'zzfixture co')"
                    ),
                    {"j": job},
                )
                conn.execute(
                    text(
                        "INSERT INTO scoring.job_score (user_id, job_group_id, "
                        "hard_filter_passed, vector_similarity_score) "
                        "VALUES (:u, :j, true, :s)"
                    ),
                    {"u": self.user_id, "j": job, "s": 0.9 - i * 0.1},
                )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id LIKE 'fixture-job-rerank-%'"
                )
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_top_jobs_get_an_llm_fit_score(self) -> None:
        run_llm_rerank(
            self.app_engine, self.user_id, adapters={"anthropic": _FakeAdapter()}
        )
        with self.owner.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT llm_fit_score, llm_rationale, llm_missing_skills, "
                    "llm_stretch_flag FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-rerank-0'"
                ),
                {"u": self.user_id},
            ).one()
        self.assertEqual(int(row.llm_fit_score), 80)
        self.assertEqual(list(row.llm_missing_skills), ["Kubernetes"])
        self.assertFalse(row.llm_stretch_flag)

    def test_never_sends_more_than_top_n_jobs(self) -> None:
        # 3 fixture jobs exist; capping top_n at 2 must leave exactly one
        # (the lowest-scoring) untouched, regardless of the real 50 cap.
        run_llm_rerank(
            self.app_engine,
            self.user_id,
            adapters={"anthropic": _FakeAdapter()},
            top_n=2,
        )
        with self.owner.connect() as conn:
            scored = (
                conn.execute(
                    text(
                        "SELECT job_group_id FROM scoring.job_score "
                        "WHERE user_id = :u AND llm_fit_score IS NOT NULL "
                        "ORDER BY job_group_id"
                    ),
                    {"u": self.user_id},
                )
                .scalars()
                .all()
            )
        self.assertEqual(scored, ["fixture-job-rerank-0", "fixture-job-rerank-1"])

    def test_stale_llm_fields_cleared_when_reconsidered_job_fails(self) -> None:
        # First run: fixture-job-rerank-0 gets a real LLM fit score.
        run_llm_rerank(
            self.app_engine, self.user_id, adapters={"anthropic": _FakeAdapter()}
        )
        with self.owner.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT llm_fit_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-rerank-0'"
                ),
                {"u": self.user_id},
            ).one()
        self.assertIsNotNone(row.llm_fit_score)

        # Second run: same job is reconsidered (still in the top-N pool) but
        # this time the adapter returns malformed JSON. The stale fit score
        # from the first run must NOT survive — it must be cleared to NULL,
        # not left over as though it were still current.
        run_llm_rerank(
            self.app_engine, self.user_id, adapters={"anthropic": _MalformedAdapter()}
        )
        with self.owner.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT llm_fit_score, llm_rationale, llm_missing_skills, "
                    "llm_stretch_flag FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-rerank-0'"
                ),
                {"u": self.user_id},
            ).one()
        self.assertIsNone(row.llm_fit_score)
        self.assertIsNone(row.llm_rationale)
        self.assertIsNone(row.llm_missing_skills)
        self.assertIsNone(row.llm_stretch_flag)

    def test_stale_llm_fields_cleared_when_job_drops_out_of_top_n(self) -> None:
        # First run with top_n=2: fixture-job-rerank-0 and -1 (the two
        # highest pre_llm_score fixtures) both get a real LLM fit score.
        run_llm_rerank(
            self.app_engine,
            self.user_id,
            adapters={"anthropic": _FakeAdapter()},
            top_n=2,
        )
        with self.owner.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT llm_fit_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-rerank-1'"
                ),
                {"u": self.user_id},
            ).one()
        self.assertIsNotNone(row.llm_fit_score)

        # Now tank fixture-job-rerank-1's pre-LLM score so the second run's
        # top_n=2 cut excludes it entirely — it drops OUT of the top-N
        # rather than being reconsidered-and-failing within it. It is, by
        # construction, absent from the second run's `top_jobs`.
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "UPDATE scoring.job_score SET vector_similarity_score = 0.01 "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-rerank-1'"
                ),
                {"u": self.user_id},
            )

        run_llm_rerank(
            self.app_engine,
            self.user_id,
            adapters={"anthropic": _FakeAdapter()},
            top_n=2,
        )
        with self.owner.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT llm_fit_score, llm_rationale, llm_missing_skills, "
                    "llm_stretch_flag FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-rerank-1'"
                ),
                {"u": self.user_id},
            ).one()
        self.assertIsNone(row.llm_fit_score)
        self.assertIsNone(row.llm_rationale)
        self.assertIsNone(row.llm_missing_skills)
        self.assertIsNone(row.llm_stretch_flag)


if __name__ == "__main__":
    unittest.main()
