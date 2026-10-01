"""Integration tests for the skill-extraction-run API (no mocking the
database — .claude/rules/python-testing.md)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import (  # noqa: E402
    get_app_db_engine,
    get_http_client,
    get_llm_adapters,
    get_native_ollama_adapter,
    get_skill_mapping_hook_factory,
)
from app.routers import extraction_runs  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.integration.skills_fixtures import (  # noqa: E402
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)
from tests.skills_fakes import FakeAdapter  # noqa: E402


app = FastAPI()
app.include_router(extraction_runs.router)

_FIXTURE_SOURCES = ["zzfixture-source"]


def _fake_ollama_unload_response(request: httpx.Request) -> httpx.Response:
    """Answer an Ollama unload POST the way a real server would.

    `run_loop` runs as a real FastAPI `BackgroundTasks` callback in
    these tests (not called directly, unlike
    `test_extraction_run_loop.py`), so its unload call must be faked
    at the `get_http_client` dependency, not just skipped, or it would
    try to reach a real network address and fail every run.

    Args:
        request: The intercepted request.

    Returns:
        A 200 response shaped like Ollama's own unload reply.
    """
    json.loads(request.content)  # sanity: body is valid JSON
    return httpx.Response(200, json={"done_reason": "unload"})


class TestExtractionRunsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        # Two distinguishable fakes: a "docker" one is what get_llm_adapters
        # yields, a "native" one is what get_native_ollama_adapter yields —
        # keeping them separate lets a test prove which one actually served
        # the extraction calls, not just which URL the unload ping hit.
        self.docker_adapter = FakeAdapter('{"skills": []}')
        self.native_adapter = FakeAdapter('{"skills": []}')
        app.dependency_overrides[get_llm_adapters] = lambda: {
            "ollama": self.docker_adapter
        }
        app.dependency_overrides[get_native_ollama_adapter] = (
            lambda: self.native_adapter
        )
        app.dependency_overrides[get_http_client] = lambda: httpx.Client(
            transport=httpx.MockTransport(_fake_ollama_unload_response)
        )
        # Never build the real mapping (it would call a real embedding server):
        # record which Ollama it was aimed at and return a canned summary.
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

    def test_filters_lists_pending_sources(self) -> None:
        self._add_survivor("fixture-job-api1")
        body = self.client.get("/skills/extraction-runs/filters").json()
        self.assertIn("zzfixture-source", body["sources"])

    def test_pending_count_reflects_scope(self) -> None:
        self._add_survivor("fixture-job-api2")
        body = self.client.get(
            "/skills/extraction-runs/pending-count",
            params={"sources": _FIXTURE_SOURCES},
        ).json()
        self.assertEqual(body["pending"], 1)


if __name__ == "__main__":
    unittest.main()
