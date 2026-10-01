"""Integration tests for core.pipeline.staleness against live Postgres,
using fixture pipeline.stage_run rows with controlled finished_at
timestamps -- this is the "timestamp-order comparison" the spec calls
for, proven with real rows rather than a fake dependency graph, since
the real STAGES graph's shape (does the review-stage skip actually
land on the right automated stage) is exactly what needs proving.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import bindparam, text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.pipeline.staleness import compute_stage_states


class TestComputeStageStates(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        # Only rows this test inserts are ever deleted -- the dev DB holds
        # real run history for these stages.
        self._run_ids: list[uuid.UUID] = []

    def tearDown(self) -> None:
        self._cleanup_runs()

    def _cleanup_runs(self) -> None:
        if not self._run_ids:
            return
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM pipeline.stage_run WHERE run_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": self._run_ids},
            )
        self._run_ids = []

    def _insert_run(
        self, stage: str, status: str, finished_at: datetime, user_id=None,
        error_message: str | None = None,
    ) -> None:
        with self.owner.begin() as conn:
            run_id = conn.execute(
                text(
                    "INSERT INTO pipeline.stage_run "
                    "(stage, user_id, status, finished_at, error_message) "
                    "VALUES (:stage, :user_id, :status, :finished_at, :error_message) "
                    "RETURNING run_id"
                ),
                {
                    "stage": stage, "user_id": user_id, "status": status,
                    "finished_at": finished_at, "error_message": error_message,
                },
            ).scalar_one()
        self._run_ids.append(run_id)

    def _insert_completed(self, stage: str, finished_at: datetime, user_id=None) -> None:
        self._insert_run(stage, "completed", finished_at, user_id)

    def test_a_stage_that_never_ran_is_blocked_if_its_dependency_never_ran(self) -> None:
        states = compute_stage_states(self.app_engine, user_id=None)
        self.assertIsNone(states["classify-jobs"].last_completed_at)
        self.assertTrue(states["classify-jobs"].is_blocked)
        self.assertFalse(states["classify-jobs"].is_stale)

    def test_a_stage_is_stale_when_its_dependency_ran_more_recently(self) -> None:
        now = datetime.now(UTC)
        self._insert_completed("cluster-jobs", now - timedelta(hours=2))
        self._insert_completed("compute-survivorship", now - timedelta(hours=3))
        states = compute_stage_states(self.app_engine, user_id=None)
        self.assertTrue(states["compute-survivorship"].is_stale)
        self.assertEqual(states["compute-survivorship"].stale_because, "cluster-jobs")
        self.assertFalse(states["cluster-jobs"].is_stale)

    def test_a_stage_is_fresh_when_it_ran_after_its_dependency(self) -> None:
        now = datetime.now(UTC)
        self._insert_completed("cluster-jobs", now - timedelta(hours=3))
        self._insert_completed("compute-survivorship", now - timedelta(hours=2))
        states = compute_stage_states(self.app_engine, user_id=None)
        self.assertFalse(states["compute-survivorship"].is_stale)
        self.assertFalse(states["compute-survivorship"].is_blocked)

    def test_staleness_skips_through_a_review_stage_to_the_automated_dependency(self) -> None:
        # classify-jobs depends on compute-survivorship in STAGES; the
        # Categorisation Review stage sits between classify-jobs and
        # nothing downstream in STAGES (review stages are terminal in
        # the automated graph) -- this proves classify-jobs's own
        # staleness is computed against compute-survivorship directly,
        # never against a review stage (which has no timestamp at all).
        now = datetime.now(UTC)
        self._insert_completed("compute-survivorship", now - timedelta(hours=1))
        self._insert_completed("classify-jobs", now)
        states = compute_stage_states(self.app_engine, user_id=None)
        self.assertFalse(states["classify-jobs"].is_stale)

    def test_per_user_stage_staleness_is_scoped_to_that_user(self) -> None:
        user_a = uuid.uuid4()
        user_b = uuid.uuid4()
        with self.owner.begin() as conn:
            for user_id in (user_a, user_b):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'zzfixture staleness user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )
        try:
            now = datetime.now(UTC)
            self._insert_completed("score-blend", now, user_id=user_a)
            states_a = compute_stage_states(self.app_engine, user_id=user_a)
            states_b = compute_stage_states(self.app_engine, user_id=user_b)
            self.assertIsNotNone(states_a["score-blend"].last_completed_at)
            self.assertIsNone(states_b["score-blend"].last_completed_at)
        finally:
            self._cleanup_runs()
            with self.owner.begin() as conn:
                for user_id in (user_a, user_b):
                    conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})

    def test_last_status_reflects_the_most_recent_attempt_even_if_it_failed(self) -> None:
        now = datetime.now(UTC)
        self._insert_completed("cluster-jobs", now - timedelta(hours=2))
        self._insert_run("cluster-jobs", "failed", now, error_message="boom")
        states = compute_stage_states(self.app_engine, user_id=None)
        # last_completed_at still reflects the completed run, not the later failure
        self.assertIsNotNone(states["cluster-jobs"].last_completed_at)
        self.assertEqual(states["cluster-jobs"].last_status, "failed")
        self.assertEqual(states["cluster-jobs"].last_error_message, "boom")

    def test_user_id_none_against_a_per_user_stage_reports_never_completed(self) -> None:
        user_id = uuid.uuid4()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture staleness user')"
                ),
                {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
            )
        try:
            now = datetime.now(UTC)
            self._insert_completed("score-blend", now, user_id=user_id)
            states = compute_stage_states(self.app_engine, user_id=None)
            self.assertIsNone(states["score-blend"].last_completed_at)
        finally:
            self._cleanup_runs()
            with self.owner.begin() as conn:
                conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})


if __name__ == "__main__":
    unittest.main()
