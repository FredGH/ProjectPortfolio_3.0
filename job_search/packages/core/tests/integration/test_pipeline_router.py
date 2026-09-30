"""Router tests for the pipeline dashboard endpoints. Real ASGI
requests against the real app, same TestClient pattern as
test_scoring_router_calibration.py."""

from __future__ import annotations

import sys
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.main import app  # noqa: E402

from core.db.session import build_engine  # noqa: E402
from core.pipeline.registry import STAGES  # noqa: E402
from core.pipeline.runner import start_run  # noqa: E402
from core.settings import get_settings  # noqa: E402


class TestPipelineRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)
        cls.client = TestClient(app)

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM pipeline.stage_run WHERE stage LIKE 'zzfixture-%'")
            )
            conn.execute(
                text(
                    "DELETE FROM pipeline.stage_run "
                    "WHERE stage = 'enrich-engagement-terms'"
                )
            )

    def test_get_stages_without_user_id_returns_422(self) -> None:
        response = self.client.get("/pipeline/stages")
        self.assertEqual(response.status_code, 422)

    def test_get_stages_returns_every_catalog_entry(self) -> None:
        user_id = uuid.uuid4()
        response = self.client.get(f"/pipeline/stages?user_id={user_id}")
        self.assertEqual(response.status_code, 200)
        names = {row["name"] for row in response.json()}
        self.assertIn("classify-jobs", names)
        self.assertIn("dedup-review", names)

    def test_get_users_returns_the_app_user_table(self) -> None:
        response = self.client.get("/pipeline/users")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_starting_a_run_then_double_starting_returns_409(self) -> None:
        # TestClient runs BackgroundTasks before returning, so a run
        # started via POST is already finished; hold the lock with a
        # run started directly through the runner instead.
        start_run(
            self.app_engine, stage="enrich-engagement-terms", user_id=None, params={}
        )
        response2 = self.client.post(
            "/pipeline/stages/compute-blocking-keys/run", json={}
        )
        self.assertEqual(response2.status_code, 409)
        # Let the background task actually finish before the next test's
        # global-lock assumption (nothing else asserts on this run's
        # own completion here — it's real, cheap work against the live
        # DB, proven separately by test_pipeline_stage_functions.py).
        import time

        for _ in range(50):
            active = self.client.get(
                "/pipeline/stages/enrich-engagement-terms/active"
            ).json()
            if active is None:
                break
            time.sleep(0.2)

    def test_cancel_on_unknown_run_returns_404(self) -> None:
        response = self.client.post("/pipeline/stages/whatever/cancel")
        self.assertEqual(response.status_code, 404)

    def test_a_per_user_stage_run_requires_user_id_in_the_body(self) -> None:
        response = self.client.post("/pipeline/stages/score-blend/run", json={})
        self.assertEqual(response.status_code, 400)

    def test_active_run_includes_updated_at_for_stalled_detection(self) -> None:
        start = self.client.post(
            "/pipeline/stages/enrich-engagement-terms/run", json={}
        )
        self.assertEqual(start.status_code, 202)
        active = self.client.get(
            "/pipeline/stages/enrich-engagement-terms/active"
        ).json()
        # The run may already have completed by the time this reads it
        # (a cheap stage) -- either way, updated_at was present on the
        # snapshot while it was active; assert against the row directly
        # to avoid a flaky race with the background task.
        with self.owner_engine.connect() as conn:
            row = conn.execute(
                text("SELECT updated_at FROM pipeline.stage_run WHERE run_id = :id"),
                {"id": start.json()["run_id"]},
            ).one()
        self.assertIsNotNone(row.updated_at)
        if active is not None:
            self.assertIn("updated_at", active)

    def test_running_a_no_run_button_stage_returns_400(self) -> None:
        # `ingest`/`run-evals` are not in the STAGES catalog, so a
        # no-run-button spec is simulated by patching one in.
        spec = replace(STAGES["enrich-engagement-terms"], has_run_button=False)
        with patch.dict(STAGES, {"enrich-engagement-terms": spec}):
            response = self.client.post(
                "/pipeline/stages/enrich-engagement-terms/run", json={}
            )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
