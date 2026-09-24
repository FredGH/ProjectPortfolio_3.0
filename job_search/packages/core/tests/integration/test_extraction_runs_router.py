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

from core.settings import get_settings  # noqa: E402
from core.skills.extraction_run import start_run  # noqa: E402

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
        app.dependency_overrides[get_llm_adapters] = lambda: {
            "ollama": FakeAdapter('{"skills": []}')
        }
        app.dependency_overrides[get_http_client] = lambda: httpx.Client(
            transport=httpx.MockTransport(_fake_ollama_unload_response)
        )
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

    def test_start_completes_synchronously_and_updates_the_run_row(self) -> None:
        # Starlette's TestClient runs a BackgroundTasks callback before
        # the triggering request returns (same behaviour
        # test_cv_router.py's POST /cv/extract tests already rely on
        # — no polling needed, unlike a real deployment where run_loop
        # keeps going after the response is sent).
        self._add_survivor("fixture-job-api3")
        start = self.client.post(
            "/skills/extraction-runs", json={"sources": _FIXTURE_SOURCES}
        )
        self.assertEqual(start.status_code, 202)
        run_id = start.json()["run_id"]

        self.assertIsNone(self.client.get("/skills/extraction-runs/active").json())

        with self.owner.connect() as conn:
            status, extracted = conn.execute(
                text(
                    "SELECT status, extracted_count "
                    "FROM silver.skill_extraction_run WHERE run_id = :r"
                ),
                {"r": run_id},
            ).one()
        self.assertEqual(status, "completed")
        self.assertEqual(extracted, 1)

    def test_start_defaults_to_the_docker_ollama_location(self) -> None:
        self._add_survivor("fixture-job-api-loc1")
        requested_urls: list[str] = []

        def _record_and_answer(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            return httpx.Response(200, json={"done_reason": "unload"})

        app.dependency_overrides[get_http_client] = lambda: httpx.Client(
            transport=httpx.MockTransport(_record_and_answer)
        )
        start = self.client.post(
            "/skills/extraction-runs", json={"sources": _FIXTURE_SOURCES}
        )
        self.assertEqual(start.status_code, 202)
        self.assertEqual(
            requested_urls,
            [f"{get_settings().ollama_base_url}/api/generate"],
        )

    def test_start_with_native_ollama_location_targets_the_host(self) -> None:
        self._add_survivor("fixture-job-api-loc2")
        requested_urls: list[str] = []

        def _record_and_answer(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            return httpx.Response(200, json={"done_reason": "unload"})

        app.dependency_overrides[get_http_client] = lambda: httpx.Client(
            transport=httpx.MockTransport(_record_and_answer)
        )
        start = self.client.post(
            "/skills/extraction-runs",
            json={"sources": _FIXTURE_SOURCES, "ollama_location": "native"},
        )
        self.assertEqual(start.status_code, 202)
        self.assertEqual(
            requested_urls,
            ["http://host.docker.internal:11434/api/generate"],
        )

    def test_start_while_one_is_active_returns_409(self) -> None:
        # Starts the first run directly through the core function
        # (bypassing the router) so it's left `running` deterministically
        # — going through the router's own POST would run it to
        # completion synchronously (see the test above) before a
        # "second" call could ever race it.
        self._add_survivor("fixture-job-api4")
        start_run(self.app_engine, sources=_FIXTURE_SOURCES, countries=None)

        response = self.client.post(
            "/skills/extraction-runs", json={"sources": _FIXTURE_SOURCES}
        )
        self.assertEqual(response.status_code, 409)

    def test_cancel_unknown_run_returns_404(self) -> None:
        response = self.client.post(
            "/skills/extraction-runs/" "00000000-0000-0000-0000-000000000000/cancel"
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
