"""Tests for core.scoring.calibration's label CRUD (PLAN.md Step 16).

Uses live Postgres via zzfixture-scoped rows, same pattern as
test_scoring_hard_filters.py.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.scoring.calibration import (
    delete_label,
    pick_labeling_candidate,
    read_labels,
    write_label,
)
from core.settings import get_settings


class TestJobLabelCrud(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture label crud user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_write_then_read_round_trips_the_label(self) -> None:
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "strong")
        labels = read_labels(self.app_engine, self.user_id)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0].job_group_id, "zzfixture-job-1")
        self.assertEqual(labels[0].label, "strong")

    def test_writing_the_same_job_again_overwrites_not_duplicates(self) -> None:
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "strong")
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "no")
        labels = read_labels(self.app_engine, self.user_id)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0].label, "no")

    def test_delete_removes_the_label(self) -> None:
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "maybe")
        delete_label(self.app_engine, self.user_id, "zzfixture-job-1")
        labels = read_labels(self.app_engine, self.user_id)
        self.assertEqual(labels, [])

    def test_read_labels_returns_empty_list_when_none_exist(self) -> None:
        self.assertEqual(read_labels(self.app_engine, self.user_id), [])


class TestPickLabelingCandidate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture candidate user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id LIKE 'zzfixture-cand-%'"
                )
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _insert_job(self, job_group_id: str) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company, location, engagement_type, description) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co', 'London', "
                    "'contract', 'zzfixture description')"
                ),
                {"j": job_group_id},
            )

    def _insert_score(
        self, job_group_id: str, *, all_four_present: bool = True
    ) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, reranker_score, "
                    "skill_coverage_score, llm_fit_score, final_score) "
                    "VALUES (:u, :j, true, 0.5, 0.5, 0.5, :llm, 0.5)"
                ),
                {
                    "u": self.user_id,
                    "j": job_group_id,
                    "llm": 70 if all_four_present else None,
                },
            )

    def test_never_returns_a_job_missing_any_of_the_four_components(self) -> None:
        self._insert_job("zzfixture-cand-incomplete")
        self._insert_score("zzfixture-cand-incomplete", all_four_present=False)
        candidate = pick_labeling_candidate(self.app_engine, self.user_id)
        self.assertIsNone(candidate)

    def test_never_returns_an_already_labeled_job(self) -> None:
        self._insert_job("zzfixture-cand-labeled")
        self._insert_score("zzfixture-cand-labeled")
        write_label(self.app_engine, self.user_id, "zzfixture-cand-labeled", "strong")
        candidate = pick_labeling_candidate(self.app_engine, self.user_id)
        self.assertIsNone(candidate)

    def test_returns_an_eligible_unlabeled_job_with_full_context(self) -> None:
        self._insert_job("zzfixture-cand-eligible")
        self._insert_score("zzfixture-cand-eligible")
        candidate = pick_labeling_candidate(self.app_engine, self.user_id)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.job_group_id, "zzfixture-cand-eligible")
        self.assertEqual(candidate.title, "zzfixture role")
        self.assertEqual(candidate.vector_similarity_score, 0.5)
        self.assertEqual(candidate.llm_fit_score, 70)

    def test_preserves_empty_llm_missing_skills_list_not_none(self) -> None:
        self._insert_job("zzfixture-cand-no-missing")
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, reranker_score, "
                    "skill_coverage_score, llm_fit_score, llm_missing_skills, "
                    "final_score) "
                    "VALUES (:u, :j, true, 0.5, 0.5, 0.5, 70, "
                    "ARRAY[]::text[], 0.5)"
                ),
                {"u": self.user_id, "j": "zzfixture-cand-no-missing"},
            )
        candidate = pick_labeling_candidate(self.app_engine, self.user_id)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.llm_missing_skills, [])


if __name__ == "__main__":
    unittest.main()
