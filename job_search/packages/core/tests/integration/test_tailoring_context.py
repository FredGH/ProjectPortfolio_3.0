"""Integration tests for tailoring's job-context and candidate queries."""

from __future__ import annotations

import time
import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.db.session import session_scope
from core.tailoring.context import list_candidates, load_job_context

_PREFIX = "zzfixture-tlr-ctx"


class TestTailoringContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        self.user_a = uuid.uuid4()
        self.user_b = uuid.uuid4()
        with self.owner.begin() as conn:
            for user_id in (self.user_a, self.user_b):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'zzfixture ctx user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )
            for suffix, title, company in (
                ("1", "Lead Data Engineer", "Gamma"),
                ("2", "Analytics Engineer", "Delta"),
                ("3", "Data Scientist", "Eps"),
                ("4", None, "NoTitleCo"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO gold.dim_job "
                        "(job_group_id, title_for_display, company, description) "
                        "VALUES (:id, :title, :company, :description)"
                    ),
                    {
                        "id": f"{_PREFIX}-{suffix}",
                        "title": title,
                        "company": company,
                        "description": f"description {suffix}",
                    },
                )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__skill "
                    "(skill_id, canonical_label, source) VALUES "
                    "('zzfixture-sk-dbt', 'dbt', 'custom')"
                )
            )
            for skill_id, level, mentions in (
                ("zzfixture-sk-dbt", "nice_to_have", 5),
                ("zzfixture-sk-k8s", "must_have", 1),
                ("zzfixture-sk-aws", "must_have", 3),
            ):
                conn.execute(
                    text(
                        "INSERT INTO silver.silver__bridge_job_skill "
                        "(job_group_id, skill_id, requirement_level, mention_count) "
                        "VALUES (:job, :skill, :level, :mentions)"
                    ),
                    {
                        "job": f"{_PREFIX}-1",
                        "skill": skill_id,
                        "level": level,
                        "mentions": mentions,
                    },
                )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM tailoring.tailored_cv WHERE user_id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__bridge_job_skill "
                    "WHERE job_group_id LIKE :p"
                ),
                {"p": f"{_PREFIX}-%"},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__skill "
                    "WHERE skill_id LIKE 'zzfixture-sk-%'"
                )
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE :p"),
                {"p": f"{_PREFIX}-%"},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )

    def _score(self, user_id, suffix: str, score, passed: bool = True) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score "
                    "(user_id, job_group_id, hard_filter_passed, final_score) "
                    "VALUES (:u, :j, :passed, :score)"
                ),
                {
                    "u": user_id,
                    "j": f"{_PREFIX}-{suffix}",
                    "passed": passed,
                    "score": score,
                },
            )

    def test_load_job_context_returns_title_company_description(self) -> None:
        context = load_job_context(self.app_engine, f"{_PREFIX}-1")
        self.assertEqual(context.title_for_display, "Lead Data Engineer")
        self.assertEqual(context.company, "Gamma")
        self.assertEqual(context.description, "description 1")

    def test_skills_are_ordered_must_have_first_then_by_mentions(self) -> None:
        context = load_job_context(self.app_engine, f"{_PREFIX}-1")
        self.assertEqual(
            [(s.skill_id, s.requirement_level) for s in context.skills],
            [
                ("zzfixture-sk-aws", "must_have"),
                ("zzfixture-sk-k8s", "must_have"),
                ("zzfixture-sk-dbt", "nice_to_have"),
            ],
        )

    def test_a_skill_label_comes_from_silver_skill_or_falls_back_to_the_id(
        self,
    ) -> None:
        labels = {
            s.skill_id: s.label
            for s in load_job_context(self.app_engine, f"{_PREFIX}-1").skills
        }
        self.assertEqual(labels["zzfixture-sk-dbt"], "dbt")
        self.assertEqual(labels["zzfixture-sk-k8s"], "zzfixture-sk-k8s")

    def test_an_unknown_job_returns_none(self) -> None:
        self.assertIsNone(load_job_context(self.app_engine, f"{_PREFIX}-nope"))

    def test_a_job_with_no_title_loads_with_a_none_title(self) -> None:
        context = load_job_context(self.app_engine, f"{_PREFIX}-4")
        self.assertIsNone(context.title_for_display)

    def test_candidates_are_the_users_passed_jobs_best_first(self) -> None:
        self._score(self.user_a, "1", 0.4)
        self._score(self.user_a, "2", 0.9)
        self._score(self.user_a, "3", 0.99, passed=False)
        self._score(self.user_a, "4", None)
        candidates = [
            c
            for c in list_candidates(self.app_engine, self.user_a, limit=500)
            if c.job_group_id.startswith(_PREFIX)
        ]
        self.assertEqual(
            [c.job_group_id for c in candidates], [f"{_PREFIX}-2", f"{_PREFIX}-1"]
        )
        self.assertEqual(candidates[0].title_for_display, "Analytics Engineer")
        self.assertAlmostEqual(candidates[0].final_score, 0.9)

    def test_candidates_do_not_include_another_users_scores(self) -> None:
        self._score(self.user_a, "1", 0.4)
        mine = [
            c
            for c in list_candidates(self.app_engine, self.user_b, limit=500)
            if c.job_group_id.startswith(_PREFIX)
        ]
        self.assertEqual(mine, [])

    def test_candidates_respect_the_limit(self) -> None:
        self._score(self.user_a, "1", 0.4)
        self._score(self.user_a, "2", 0.9)
        self.assertEqual(len(list_candidates(self.app_engine, self.user_a, limit=1)), 1)

    def test_a_candidate_reports_the_latest_run(self) -> None:
        self._score(self.user_a, "1", 0.4)
        # One transaction per insert: now() is fixed for a transaction, so
        # two inserts in one would share a created_at and the "latest" run
        # would be ambiguous.
        for status in ("failed", "needs_review"):
            with session_scope(self.app_engine, user_id=self.user_a) as conn:
                conn.execute(
                    text(
                        "INSERT INTO tailoring.tailored_cv "
                        "(user_id, job_group_id, truth_base_version, target_title, "
                        "status) VALUES (:u, :j, 1, 'T', :s)"
                    ),
                    {"u": self.user_a, "j": f"{_PREFIX}-1", "s": status},
                )
            time.sleep(0.05)
        candidate = next(
            c
            for c in list_candidates(self.app_engine, self.user_a, limit=500)
            if c.job_group_id == f"{_PREFIX}-1"
        )
        self.assertEqual(candidate.latest_status, "needs_review")
        self.assertIsNotNone(candidate.latest_run_id)

    def test_a_candidate_with_no_run_has_none(self) -> None:
        self._score(self.user_a, "1", 0.4)
        candidate = next(
            c
            for c in list_candidates(self.app_engine, self.user_a, limit=500)
            if c.job_group_id == f"{_PREFIX}-1"
        )
        self.assertIsNone(candidate.latest_run_id)
        self.assertIsNone(candidate.latest_status)


if __name__ == "__main__":
    unittest.main()
