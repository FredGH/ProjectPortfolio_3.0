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

from core.skills.extraction_run import (
    MAPPING_SKIPPED_FAILED,
    MAPPING_SKIPPED_STOPPED,
    get_run,
    run_loop,
    start_run,
)

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


class _CancelAfterFirstCall(FakeAdapter):
    """Requests cancellation the moment the first extraction call is made."""

    def __init__(self, engine, run_id, *responses: str) -> None:
        super().__init__(*responses)
        self._engine = engine
        self._run_id = run_id

    def complete(self, **kwargs):
        response = super().complete(**kwargs)
        if len(self.calls) == 1:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE silver.skill_extraction_run "
                        "SET cancel_requested = TRUE WHERE run_id = :r"
                    ),
                    {"r": self._run_id},
                )
        return response


class _ProgressProbe(FakeAdapter):
    """Records the run's committed extracted_count at the start of each call."""

    def __init__(self, engine, run_id, *responses: str) -> None:
        super().__init__(*responses)
        self._engine = engine
        self._run_id = run_id
        self.seen: list[int] = []

    def complete(self, **kwargs):
        self.seen.append(get_run(self._engine, self._run_id).extracted_count)
        return super().complete(**kwargs)


def _run(app, run_id, adapter, **overrides) -> None:
    """Run `run_loop` against fakes, with sensible defaults for these tests."""
    kwargs = dict(
        adapters={"ollama": adapter},
        http_client=httpx.Client(transport=_UnloadRecordingTransport()),
        ollama_base_url="http://fake-ollama:11434",
        model="test-model",
        provider="ollama",
        sources=_FIXTURE_SOURCES,
        countries=None,
        batch_size=30,
        pause_seconds=0,
    )
    kwargs.update(overrides)
    run_loop(run_id, app, **kwargs)


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
            provider="ollama",
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
            provider="ollama",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=2,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.extracted_count, 5)

    def test_a_cancel_requested_before_the_loop_starts_stops_it_before_any_job(
        self,
    ) -> None:
        for i in range(3):
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
        _run(self.app, run_id, adapter)
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "cancelled")
        self.assertEqual(status.extracted_count, 0)
        self.assertEqual(adapter.calls, [])

    def test_a_cancel_during_a_batch_stops_after_the_current_job(self) -> None:
        # One 30-job batch, five jobs pending: the old behaviour finished the
        # whole batch before noticing; now the run stops after the job in
        # flight, however large the batch.
        for i in range(5):
            self._add_survivor(f"fixture-job-midcancel{i}")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        adapter = _CancelAfterFirstCall(self.app, run_id, _reply("Python"))
        _run(self.app, run_id, adapter, batch_size=30)
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "cancelled")
        self.assertEqual(status.extracted_count, 1)
        self.assertEqual(len(adapter.calls), 1)

    def test_progress_is_committed_after_every_job_not_after_the_batch(self) -> None:
        for i in range(3):
            self._add_survivor(f"fixture-job-progress{i}")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        adapter = _ProgressProbe(self.app, run_id, _reply("Python"))
        _run(self.app, run_id, adapter, batch_size=30)
        # Each call saw the previous jobs already counted: 0, then 1, then 2.
        self.assertEqual(adapter.seen, [0, 1, 2])
        self.assertEqual(get_run(self.app, run_id).extracted_count, 3)

    def test_a_completed_run_maps_skills_and_stores_the_summary(self) -> None:
        self._add_survivor("fixture-job-map1")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        summary = "Mapped 2 new skill string(s) to ESCO; 1 need review."
        calls: list[int] = []

        def map_skills() -> str:
            calls.append(1)
            return summary

        _run(self.app, run_id, FakeAdapter(_reply("Python")), map_skills=map_skills)
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.mapping_summary, summary)
        self.assertEqual(calls, [1])

    def test_leftover_failures_after_progress_complete_the_run_and_map_skills(
        self,
    ) -> None:
        # A real batch always has a few jobs the model can never parse. Once
        # the rest are done and only those keep failing, the run has done all
        # it can: it must finish as "completed" (leftovers stay pending for a
        # later run) and still map skills — not be reported as "failed".
        prefix = "fixture-job-leftover"
        for c in "abc":
            self._add_survivor(f"{prefix}-{c}")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        # Job a parses; b and c are cut off on the first try AND the retry, in
        # the first pass and again in the second (the last flag repeats).
        adapter = FakeAdapter(_reply("Python"), truncated=[False, True])
        calls: list[int] = []
        _run(
            self.app,
            run_id,
            adapter,
            map_skills=lambda: calls.append(1) or "mapped",
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.extracted_count, 1)
        # Each failing job counts once, even though it was retried in pass two.
        self.assertEqual(status.failed_count, 2)
        self.assertEqual(calls, [1])
        self.assertEqual(status.mapping_summary, "mapped")

    def test_a_stopped_run_does_not_map_skills(self) -> None:
        for i in range(3):
            self._add_survivor(f"fixture-job-stopmap{i}")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        calls: list[int] = []
        adapter = _CancelAfterFirstCall(self.app, run_id, _reply("Python"))
        _run(self.app, run_id, adapter, map_skills=lambda: calls.append(1) or "x")
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "cancelled")
        self.assertEqual(calls, [])
        self.assertEqual(status.mapping_summary, MAPPING_SKIPPED_STOPPED)

    def test_a_failed_run_does_not_map_skills(self) -> None:
        self._add_survivor("fixture-job-failmap1")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        calls: list[int] = []
        _run(
            self.app,
            run_id,
            FakeAdapter("this is not JSON"),
            map_skills=lambda: calls.append(1) or "x",
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "failed")
        self.assertEqual(calls, [])
        self.assertEqual(status.mapping_summary, MAPPING_SKIPPED_FAILED)

    def test_a_mapping_failure_does_not_fail_the_run(self) -> None:
        self._add_survivor("fixture-job-maperr1")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)

        def boom() -> str:
            raise RuntimeError("embedding server down")

        _run(self.app, run_id, FakeAdapter(_reply("Python")), map_skills=boom)
        status = get_run(self.app, run_id)
        # The extraction itself succeeded and stays "completed"; only the
        # summary records that mapping did not.
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.extracted_count, 1)
        self.assertIn("failed", status.mapping_summary)
        self.assertIn("embedding server down", status.mapping_summary)

    def test_without_a_mapping_hook_the_summary_stays_empty(self) -> None:
        self._add_survivor("fixture-job-nomap1")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        _run(self.app, run_id, FakeAdapter(_reply("Python")))
        self.assertIsNone(get_run(self.app, run_id).mapping_summary)

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
            provider="ollama",
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

    def test_a_deterministically_failing_job_stops_the_run_instead_of_looping(
        self,
    ) -> None:
        # A reply that can never parse into skills makes write_job_skills
        # count every attempt at this job as failed_jobs=1, extracted_jobs=0
        # (see jd_extract.py's documented Ollama decoding-loop case) — the
        # same job would be re-selected forever without the forward-progress
        # check in run_loop, since a failed job never gets a
        # job_skill_extraction row. This proves the run stops after one
        # sub-batch instead of livelocking.
        self._add_survivor("fixture-job-neverparse1")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        adapter = FakeAdapter("this is not JSON")
        transport = _UnloadRecordingTransport()
        http_client = httpx.Client(transport=transport)
        run_loop(
            run_id,
            self.app,
            adapters={"ollama": adapter},
            http_client=http_client,
            ollama_base_url="http://fake-ollama:11434",
            model="test-model",
            provider="ollama",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=30,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "failed")
        self.assertIn("no progress", status.error_message)
        self.assertEqual(status.extracted_count, 0)
        self.assertEqual(status.failed_count, 1)
        # Only one sub-batch ran (the LLM was called exactly once) — the
        # fix stops before the unload call too, since there is no point
        # unloading/pausing before ending the run.
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(len(transport.unload_calls), 0)


if __name__ == "__main__":
    unittest.main()
