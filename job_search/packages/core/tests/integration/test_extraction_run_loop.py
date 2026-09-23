"""Integration tests for core.skills.extraction_run.run_loop against live
Postgres, with a fake LLM adapter and a fake Ollama HTTP transport (no
real model, matching how test_skills_jd_extract.py avoids one)."""

from __future__ import annotations

import json
import unittest

import httpx
from sqlalchemy import text
from tests.integration.skills_fixtures import (
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)
from tests.skills_fakes import FakeAdapter

from core.skills.extraction_run import get_run, run_loop, start_run

_FIXTURE_SOURCES = ["zzfixture-source"]


def _reply(*skills: str) -> str:
    return json.dumps(
        {"skills": [{"skill": s, "requirement_level": "must_have"} for s in skills]}
    )


class _UnloadRecordingTransport(httpx.MockTransport):
    """Records every unload POST and answers it like real Ollama does."""

    def __init__(self) -> None:
        self.unload_calls: list[dict] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.unload_calls.append(body)
        return httpx.Response(200, json={"done_reason": "unload"})


class TestRunLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)

    def tearDown(self) -> None:
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

    def test_runs_to_completion_and_unloads_between_sub_batches(self) -> None:
        for i in range(3):
            self._add_survivor(f"fixture-job-loop{i}")
        run_id, total_pending = start_run(
            self.app, sources=_FIXTURE_SOURCES, countries=None
        )
        self.assertEqual(total_pending, 3)
        adapter = FakeAdapter(_reply("Python"))
        transport = _UnloadRecordingTransport()
        http_client = httpx.Client(transport=transport)
        run_loop(
            run_id,
            self.app,
            adapters={"ollama": adapter},
            http_client=http_client,
            ollama_base_url="http://fake-ollama:11434",
            model="test-model",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=30,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.extracted_count, 3)
        self.assertIsNotNone(status.finished_at)
        self.assertEqual(len(transport.unload_calls), 1)
        self.assertEqual(
            transport.unload_calls[0],
            {"model": "test-model", "keep_alive": 0},
        )

    def test_advances_across_multiple_sub_batches(self) -> None:
        for i in range(5):
            self._add_survivor(f"fixture-job-multi{i}")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        adapter = FakeAdapter(_reply("Python"))
        http_client = httpx.Client(transport=_UnloadRecordingTransport())
        run_loop(
            run_id,
            self.app,
            adapters={"ollama": adapter},
            http_client=http_client,
            ollama_base_url="http://fake-ollama:11434",
            model="test-model",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=2,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.extracted_count, 5)

    def test_a_cancel_requested_before_the_loop_starts_stops_it_after_one_sub_batch(
        self,
    ) -> None:
        for i in range(5):
            self._add_survivor(f"fixture-job-cancel{i}")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        with self.app.begin() as conn:
            conn.execute(
                text(
                    "UPDATE silver.skill_extraction_run "
                    "SET cancel_requested = TRUE WHERE run_id = :r"
                ),
                {"r": run_id},
            )
        adapter = FakeAdapter(_reply("Python"))
        http_client = httpx.Client(transport=_UnloadRecordingTransport())
        run_loop(
            run_id,
            self.app,
            adapters={"ollama": adapter},
            http_client=http_client,
            ollama_base_url="http://fake-ollama:11434",
            model="test-model",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=2,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "cancelled")
        self.assertEqual(status.extracted_count, 2)

    def test_an_unreachable_ollama_marks_the_run_failed(self) -> None:
        self._add_survivor("fixture-job-fail1")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        adapter = FakeAdapter(_reply("Python"))

        def _raise(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        http_client = httpx.Client(transport=httpx.MockTransport(_raise))
        run_loop(
            run_id,
            self.app,
            adapters={"ollama": adapter},
            http_client=http_client,
            ollama_base_url="http://fake-ollama:11434",
            model="test-model",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=30,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "failed")
        self.assertIn("connection refused", status.error_message)
        # The job itself was still extracted and committed before the
        # unload call failed — only the run's own bookkeeping stops.
        self.assertEqual(status.extracted_count, 1)


if __name__ == "__main__":
    unittest.main()
