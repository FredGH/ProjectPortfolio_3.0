"""Integration tests for core.pipeline.runner against live Postgres.
Uses one cheap fake StageSpec throughout — real stages' own correctness
is proven by test_pipeline_stage_functions.py; this only proves the
generic harness (lock, progress, cancel, failure)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.pipeline.registry import StageSpec
from core.pipeline.runner import (
    RunAlreadyActive,
    RunNotFound,
    get_active_run,
    get_run,
    request_cancel,
    run_stage,
    start_run,
)


def _ok_stage(params: dict) -> dict:
    return {"did": "work"}


def _failing_stage(params: dict) -> dict:
    raise ValueError("boom")


class TestPipelineRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(text("DELETE FROM pipeline.stage_run WHERE stage LIKE 'zzfixture-%'"))

    def test_start_run_then_get_active_run_round_trips(self) -> None:
        run_id = start_run(self.app_engine, stage="zzfixture-a", user_id=None, params={})
        active = get_active_run(self.app_engine)
        self.assertIsNotNone(active)
        self.assertEqual(active.run_id, run_id)
        self.assertEqual(active.status, "running")

    def test_start_run_while_one_is_active_raises_regardless_of_stage(self) -> None:
        start_run(self.app_engine, stage="zzfixture-a", user_id=None, params={})
        with self.assertRaises(RunAlreadyActive):
            start_run(self.app_engine, stage="zzfixture-b", user_id=None, params={})

    def test_start_run_with_invalid_user_id_raises_foreign_key_violation(self) -> None:
        # FK violations should propagate, not be mislabeled as RunAlreadyActive.
        invalid_user_id = uuid.uuid4()
        with self.assertRaises(IntegrityError):
            start_run(self.app_engine, stage="zzfixture-a", user_id=invalid_user_id, params={})

    def test_request_cancel_sets_the_flag(self) -> None:
        run_id = start_run(self.app_engine, stage="zzfixture-a", user_id=None, params={})
        request_cancel(self.app_engine, run_id)
        run = get_run(self.app_engine, run_id)
        self.assertTrue(run.cancel_requested)

    def test_request_cancel_on_unknown_run_raises(self) -> None:
        with self.assertRaises(RunNotFound):
            request_cancel(self.app_engine, uuid.uuid4())

    def test_run_stage_marks_completed_and_stores_result(self) -> None:
        spec = StageSpec(name="zzfixture-a", depends_on=(), per_user=False, run=_ok_stage)
        run_id = start_run(self.app_engine, stage=spec.name, user_id=None, params={})
        run_stage(run_id, self.app_engine, spec, {})
        run = get_run(self.app_engine, run_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.result, {"did": "work"})
        self.assertIsNotNone(run.finished_at)

    def test_run_stage_marks_failed_with_the_exception_message(self) -> None:
        spec = StageSpec(name="zzfixture-a", depends_on=(), per_user=False, run=_failing_stage)
        run_id = start_run(self.app_engine, stage=spec.name, user_id=None, params={})
        run_stage(run_id, self.app_engine, spec, {})
        run = get_run(self.app_engine, run_id)
        self.assertEqual(run.status, "failed")
        self.assertIn("boom", run.error_message)

    def test_run_stage_marks_cancelled_when_cancel_was_requested_before_run(self) -> None:
        # run_stage checks the cancel flag before calling spec.run — a
        # cancel requested between start_run and the BackgroundTasks
        # callback actually firing must not still run the stage's work.
        spec = StageSpec(name="zzfixture-a", depends_on=(), per_user=False, run=_ok_stage)
        run_id = start_run(self.app_engine, stage=spec.name, user_id=None, params={})
        request_cancel(self.app_engine, run_id)
        run_stage(run_id, self.app_engine, spec, {})
        run = get_run(self.app_engine, run_id)
        self.assertEqual(run.status, "cancelled")

    def test_get_active_run_is_none_when_nothing_is_running(self) -> None:
        self.assertIsNone(get_active_run(self.app_engine))

    def test_run_scoped_to_a_user_id_is_stored_and_read_back(self) -> None:
        user_id = uuid.uuid4()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture runner user')"
                ),
                {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
            )
        try:
            run_id = start_run(
                self.app_engine, stage="zzfixture-a", user_id=user_id, params={"top_n": 800}
            )
            run = get_run(self.app_engine, run_id)
            self.assertEqual(run.user_id, user_id)
            self.assertEqual(run.params, {"top_n": 800})
        finally:
            with self.owner.begin() as conn:
                conn.execute(
                    text("DELETE FROM pipeline.stage_run WHERE user_id = :id"), {"id": user_id}
                )
                conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})


if __name__ == "__main__":
    unittest.main()
