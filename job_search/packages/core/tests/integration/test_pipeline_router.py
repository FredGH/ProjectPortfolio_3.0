"""Router tests for the pipeline dashboard endpoints. Real ASGI
requests against the real app, same TestClient pattern as
test_scoring_router_calibration.py."""

from __future__ import annotations

import json
import sys
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import bindparam, text
from tests.integration.skills_fixtures import live_owner_engine, purge_fixtures
from tests.skills_fakes import FakeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.dependencies import (  # noqa: E402
    get_app_db_engine,
    get_http_client,
    get_llm_adapters,
    get_native_ollama_adapter,
    get_skill_mapping_hook_factory,
)
from app.main import app  # noqa: E402

from core.db.session import build_engine  # noqa: E402
from core.pipeline.registry import STAGES  # noqa: E402
from core.pipeline.runner import start_run  # noqa: E402
from core.settings import get_settings  # noqa: E402
from core.skills.extraction_run import start_run as start_extraction_run  # noqa: E402


class TestPipelineRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)
        cls.client = TestClient(app)

    def setUp(self) -> None:
        self._run_ids: list = []

    def _start(self, **kwargs) -> uuid.UUID:
        run_id = start_run(self.app_engine, **kwargs)
        self._run_ids.append(run_id)
        return run_id

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        purge_fixtures(self.owner_engine)
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM pipeline.stage_run WHERE stage LIKE 'zzfixture-%'")
            )
            # Only rows this test class created (tracked by run_id) -- the dev
            # DB holds real enrich-engagement-terms history.
            if self._run_ids:
                conn.execute(
                    text(
                        "DELETE FROM pipeline.stage_run WHERE run_id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": self._run_ids},
                )

    def test_get_stages_without_user_id_returns_422(self) -> None:
        response = self.client.get("/pipeline/stages")
        self.assertEqual(response.status_code, 422)

    def test_get_stages_returns_every_catalog_entry(self) -> None:
        user_id = uuid.uuid4()
        response = self.client.get(f"/pipeline/stages?user_id={user_id}")
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertIn("classify-jobs", {r["name"] for r in rows if r["kind"] == "automated"})
        review = {r["key"]: r["name"] for r in rows if r["kind"] == "review"}
        self.assertEqual(review["dedup-review"], "Dedup Review")

    def test_get_users_lists_every_user_across_rls(self) -> None:
        user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture router user')"
                ),
                {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
            )
        try:
            response = self.client.get("/pipeline/users")
            self.assertEqual(response.status_code, 200)
            ids = {row["id"] for row in response.json()}
            self.assertIn(str(user_id), ids)
        finally:
            with self.owner_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM app_user WHERE id = :id"), {"id": user_id}
                )

    def test_starting_a_run_then_double_starting_returns_409(self) -> None:
        # TestClient runs BackgroundTasks before returning, so a run
        # started via POST is already finished; hold the lock with a
        # run started directly through the runner instead.
        self._start(stage="enrich-engagement-terms", user_id=None, params={})
        response2 = self.client.post(
            "/pipeline/stages/compute-blocking-keys/run", json={}
        )
        self.assertEqual(response2.status_code, 409)

    def test_cancel_on_the_active_run_returns_204(self) -> None:
        run_id = self._start(stage="enrich-engagement-terms", user_id=None, params={})
        response = self.client.post("/pipeline/stages/enrich-engagement-terms/cancel")
        self.assertEqual(response.status_code, 204)
        with self.owner_engine.connect() as conn:
            cancel_requested = conn.execute(
                text(
                    "SELECT cancel_requested FROM pipeline.stage_run WHERE run_id = :r"
                ),
                {"r": run_id},
            ).scalar_one()
        self.assertTrue(cancel_requested)

    def test_cancel_on_unknown_run_returns_404(self) -> None:
        response = self.client.post("/pipeline/stages/whatever/cancel")
        self.assertEqual(response.status_code, 404)

    def test_a_per_user_stage_run_requires_user_id_in_the_body(self) -> None:
        response = self.client.post("/pipeline/stages/score-blend/run", json={})
        self.assertEqual(response.status_code, 400)

    def test_active_run_includes_updated_at_for_stalled_detection(self) -> None:
        # TestClient runs BackgroundTasks before returning, so a run started
        # via POST has already finished; hold a genuinely active run via the
        # runner instead so the endpoint really returns a snapshot.
        run_id = self._start(stage="enrich-engagement-terms", user_id=None, params={})
        active = self.client.get("/pipeline/stages/enrich-engagement-terms/active").json()
        self.assertIsNotNone(active)
        self.assertEqual(active["run_id"], str(run_id))
        self.assertIn("updated_at", active)

    def test_get_stages_includes_a_failed_runs_error_message(self) -> None:
        with self.owner_engine.begin() as conn:
            run_id = conn.execute(
                text(
                    "INSERT INTO pipeline.stage_run "
                    "(stage, status, finished_at, error_message) "
                    "VALUES ('enrich-engagement-terms', 'failed', now(), 'zzfixture boom') "
                    "RETURNING run_id"
                )
            ).scalar_one()
        self._run_ids.append(run_id)
        response = self.client.get(f"/pipeline/stages?user_id={uuid.uuid4()}")
        row = next(r for r in response.json() if r["name"] == "enrich-engagement-terms")
        self.assertEqual(row["last_status"], "failed")
        self.assertEqual(row["last_error_message"], "zzfixture boom")

    def test_running_a_no_run_button_stage_returns_400(self) -> None:
        # `ingest`/`run-evals` are not in the STAGES catalog, so a
        # no-run-button spec is simulated by patching one in.
        spec = replace(STAGES["enrich-engagement-terms"], has_run_button=False)
        with patch.dict(STAGES, {"enrich-engagement-terms": spec}):
            response = self.client.post(
                "/pipeline/stages/enrich-engagement-terms/run", json={}
            )
        self.assertEqual(response.status_code, 400)

    def test_a_per_user_start_returns_202_and_stores_a_json_safe_user_id(self) -> None:
        user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture router user')"
                ),
                {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
            )
        seen: dict = {}

        def fake_run(params: dict) -> dict:
            seen.update(params)
            return {"ok": True}

        spec = replace(STAGES["score-blend"], run=fake_run)
        try:
            with patch.dict(STAGES, {"score-blend": spec}):
                response = self.client.post(
                    "/pipeline/stages/score-blend/run", json={"user_id": str(user_id)}
                )
            self.assertEqual(response.status_code, 202)
            # run_stage hands the wrapper the real UUID...
            self.assertEqual(seen["user_id"], user_id)
            # ...while the stored row holds it as a string.
            with self.owner_engine.connect() as conn:
                stored = conn.execute(
                    text("SELECT params FROM pipeline.stage_run WHERE run_id = :r"),
                    {"r": response.json()["run_id"]},
                ).scalar_one()
            self.assertEqual(stored["user_id"], str(user_id))
        finally:
            with self.owner_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM pipeline.stage_run WHERE user_id = :id"),
                    {"id": user_id},
                )
                conn.execute(
                    text("DELETE FROM app_user WHERE id = :id"), {"id": user_id}
                )


class TestExtractJobSkillsViaPipelineRouter(unittest.TestCase):
    """POST /pipeline/stages/extract-job-skills/run drives extraction_run."""

    _SOURCES = ["zzfixture-source"]

    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        purge_fixtures(self.owner)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.docker_adapter = FakeAdapter('{"skills": []}')
        self.native_adapter = FakeAdapter('{"skills": []}')
        app.dependency_overrides[get_llm_adapters] = lambda: {
            "ollama": self.docker_adapter
        }
        app.dependency_overrides[get_native_ollama_adapter] = (
            lambda: self.native_adapter
        )
        self.requested_urls: list[str] = []

        def _answer(request: httpx.Request) -> httpx.Response:
            self.requested_urls.append(str(request.url))
            json.loads(request.content)
            return httpx.Response(200, json={"done_reason": "unload"})

        app.dependency_overrides[get_http_client] = lambda: httpx.Client(
            transport=httpx.MockTransport(_answer)
        )
        self.mapping_targets: list[str] = []

        def fake_factory(
            engine, *, ollama_base_url, embedding_model, http_client, llm_adapters=None
        ):
            self.mapping_targets.append(ollama_base_url)
            return lambda: "fake mapping summary"

        app.dependency_overrides[get_skill_mapping_hook_factory] = lambda: fake_factory
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        purge_fixtures(self.owner)

    def _add_survivor(self, job: str) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "(:g, 'Python required.', 'zzfixture-source', :g, "
                    "'https://example.test/x', 'Data Engineer')"
                ),
                {"g": job},
            )

    def _run(self, **params):
        return self.client.post(
            "/pipeline/stages/extract-job-skills/run",
            json={"params": {"sources": self._SOURCES, **params}},
        )

    def _row(self, run_id: str):
        with self.owner.connect() as conn:
            return conn.execute(
                text(
                    "SELECT status, COALESCE(progress_current, 0) AS done, result "
                    "FROM pipeline.stage_run WHERE run_id = :r"
                ),
                {"r": run_id},
            ).one()

    def test_default_location_uses_docker_ollama_and_completes(self) -> None:
        self._add_survivor("fixture-job-pipe1")
        response = self._run()
        self.assertEqual(response.status_code, 202)
        row = self._row(response.json()["run_id"])
        self.assertEqual(row.status, "completed")
        self.assertEqual(row.done, 1)
        self.assertEqual(
            self.requested_urls, [f"{get_settings().ollama_base_url}/api/generate"]
        )
        self.assertEqual(len(self.docker_adapter.calls), 1)
        self.assertEqual(len(self.native_adapter.calls), 0)
        self.assertEqual(self.mapping_targets, [get_settings().ollama_base_url])

    def test_native_location_targets_the_host_and_maps_against_it(self) -> None:
        self._add_survivor("fixture-job-pipe2")
        response = self._run(ollama_location="native")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            self.requested_urls, ["http://host.docker.internal:11434/api/generate"]
        )
        self.assertEqual(len(self.native_adapter.calls), 1)
        self.assertEqual(len(self.docker_adapter.calls), 0)
        self.assertEqual(self.mapping_targets, ["http://host.docker.internal:11434"])
        row = self._row(response.json()["run_id"])
        self.assertEqual(row.status, "completed")
        self.assertEqual(row.result["mapping_summary"], "fake mapping summary")

    def test_zero_pending_records_a_completed_run_without_running_the_loop(
        self,
    ) -> None:
        response = self._run()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self._row(response.json()["run_id"]).status, "completed")
        self.assertEqual(self.requested_urls, [])
        self.assertEqual(self.mapping_targets, [])

    def test_start_while_another_run_is_active_returns_409(self) -> None:
        self._add_survivor("fixture-job-pipe3")
        start_extraction_run(self.app_engine, sources=self._SOURCES, countries=None)
        self.assertEqual(self._run().status_code, 409)

    def test_an_invalid_ollama_location_returns_400(self) -> None:
        self.assertEqual(self._run(ollama_location="cloud").status_code, 400)


if __name__ == "__main__":
    unittest.main()
