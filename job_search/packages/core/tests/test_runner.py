"""Tests for the shared ingestion runner (PLAN.md Step 3)."""

from __future__ import annotations

import datetime
import unittest
from collections.abc import Iterator

from core.ingestion.raw_job import RawJob
from core.ingestion.runner import run_connector


class _FakeConnector:
    """A test double yielding a fixed, injectable list of RawJobs."""

    def __init__(self, jobs: list[RawJob], *, fail_times: int = 0) -> None:
        """Store the jobs to yield and how many initial calls should fail.

        Args:
            jobs: The `RawJob`s to yield once failures (if any) are done.
            fail_times: Number of leading `fetch()` calls that raise
                instead of yielding.
        """
        self._jobs = jobs
        self._fail_times = fail_times
        self._calls = 0

    def fetch(
        self, query: object, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield the configured jobs, failing the first `fail_times` calls."""
        self._calls += 1
        if self._calls <= self._fail_times:
            raise RuntimeError("transient failure")
        for job in self._jobs:
            yield RawJob(
                source_name=job.source_name,
                source_job_id=job.source_job_id,
                job_url=job.job_url,
                job_url_canonical=job.job_url_canonical,
                payload=job.payload,
                fetched_at=job.fetched_at,
                run_id=run_id,
                request_params=job.request_params,
                payload_sha256=job.payload_sha256,
            )


def _sample_job(source_job_id: str = "1") -> RawJob:
    """Build a minimal RawJob for tests, keyed by `source_job_id`.

    Args:
        source_job_id: The source job id to embed.

    Returns:
        A `RawJob` with fixed sample values.
    """
    return RawJob(
        source_name="fake",
        source_job_id=source_job_id,
        job_url="https://example.com/job",
        job_url_canonical="https://example.com/job",
        payload={"raw_text": "hello"},
        fetched_at=datetime.datetime(2026, 9, 3, tzinfo=datetime.UTC),
        run_id="unused",
        request_params={},
        payload_sha256="abc",
    )


class TestRunConnector(unittest.TestCase):
    """Tests run_connector's wiring: rate limit, retry, landing, bronze, metadata."""

    def setUp(self) -> None:
        """Reset the fake-writer call logs before each test."""
        self.landing_calls: list[dict[str, object]] = []
        self.bronze_calls: list[dict[str, object]] = []
        self.metadata_calls: list[object] = []

    def _fake_write_landing_record(self, landing_uri: str, **kwargs: object) -> str:
        """Record a landing write and return a fake path."""
        self.landing_calls.append(kwargs)
        return f"{landing_uri}/fake/path.jsonl.gz"

    def _fake_load_to_bronze(self, **kwargs: object) -> None:
        """Record a bronze load without touching a real database."""
        self.bronze_calls.append(kwargs)

    def _fake_write_run_metadata(self, landing_uri: str, metadata: object) -> str:
        """Record a run-metadata write and return a fake path."""
        self.metadata_calls.append(metadata)
        return f"{landing_uri}/fake/run.json"

    def test_writes_landing_and_bronze_for_every_yielded_job(self) -> None:
        """Each yielded RawJob gets one landing write and one bronze load."""
        connector = _FakeConnector([_sample_job("1"), _sample_job("2")])
        result = run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(len(self.landing_calls), 2)
        self.assertEqual(len(self.bronze_calls), 2)
        self.assertEqual(len(result.raw_jobs), 2)
        self.assertEqual(len(result.landing_paths), 2)

    def test_all_yielded_jobs_share_one_run_id_assigned_by_the_runner(self) -> None:
        """run_id is generated once by the runner, not per-item by the connector."""
        connector = _FakeConnector([_sample_job("1"), _sample_job("2")])
        result = run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        run_ids = {job.run_id for job in result.raw_jobs}
        self.assertEqual(len(run_ids), 1)
        self.assertEqual(result.run_metadata.run_id, run_ids.pop())

    def test_rate_limiter_is_acquired_once_per_run_when_given(self) -> None:
        """A configured rate limiter's acquire() is called exactly once."""
        acquire_calls = {"count": 0}

        class _CountingBucket:
            def acquire(self) -> None:
                acquire_calls["count"] += 1

        connector = _FakeConnector([_sample_job()])
        run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            rate_limiter=_CountingBucket(),  # type: ignore[arg-type]
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(acquire_calls["count"], 1)

    def test_no_rate_limiter_means_no_wait(self) -> None:
        """Omitting rate_limiter is valid — the run proceeds unthrottled."""
        connector = _FakeConnector([_sample_job()])
        result = run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(len(result.raw_jobs), 1)

    def test_transient_fetch_failure_is_retried_then_succeeds(self) -> None:
        """A connector that fails once then succeeds still completes the run."""
        connector = _FakeConnector([_sample_job()], fail_times=1)
        sleeps: list[float] = []
        result = run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            retry_base=0.01,
            retry_max_retries=3,
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=sleeps.append,
            jitter_fn=lambda: 0.0,
        )
        self.assertEqual(len(result.raw_jobs), 1)
        self.assertEqual(len(sleeps), 1)

    def test_exhausted_retries_writes_a_failed_run_metadata_and_reraises(self) -> None:
        """A connector that always fails writes status='failed' and raises."""
        connector = _FakeConnector([_sample_job()], fail_times=99)
        with self.assertRaises(RuntimeError):
            run_connector(
                connector_key="fake",
                connector=connector,
                query="q",
                since=None,
                entry_method="api",
                landing_uri="file:///tmp/unused",
                database_url="unused",
                retry_base=0.01,
                retry_max_retries=1,
                load_to_bronze_fn=self._fake_load_to_bronze,
                write_landing_record_fn=self._fake_write_landing_record,
                write_run_metadata_fn=self._fake_write_run_metadata,
                sleep_fn=lambda s: None,
                jitter_fn=lambda: 0.0,
            )
        self.assertEqual(len(self.metadata_calls), 1)
        self.assertEqual(self.metadata_calls[0].status, "failed")
        self.assertEqual(len(self.landing_calls), 0)
        self.assertEqual(len(self.bronze_calls), 0)

    def test_successful_run_writes_metadata_with_correct_record_count(self) -> None:
        """The success-path metadata's records count matches what was yielded."""
        connector = _FakeConnector(
            [_sample_job("1"), _sample_job("2"), _sample_job("3")]
        )
        run_connector(
            connector_key="fake",
            connector=connector,
            query="my query",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(self.metadata_calls[0].records, 3)
        self.assertEqual(self.metadata_calls[0].status, "success")
        self.assertEqual(self.metadata_calls[0].query, "my query")

    def test_long_query_is_truncated_in_run_metadata(self) -> None:
        """A query whose str() is long gets capped, not embedded whole."""
        connector = _FakeConnector([_sample_job()])
        long_query = "x" * 5000
        run_connector(
            connector_key="fake",
            connector=connector,
            query=long_query,
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        recorded_query = self.metadata_calls[0].query
        self.assertLess(len(recorded_query), len(long_query))
        self.assertTrue(recorded_query.endswith("(4800 more chars)"))

    def test_retry_on_scopes_retries_to_given_exception_types(self) -> None:
        """A retry_on that excludes the raised exception retries zero times."""
        connector = _FakeConnector([_sample_job()], fail_times=99)
        sleeps: list[float] = []
        with self.assertRaises(RuntimeError):
            run_connector(
                connector_key="fake",
                connector=connector,
                query="q",
                since=None,
                entry_method="api",
                landing_uri="file:///tmp/unused",
                database_url="unused",
                retry_base=0.01,
                retry_max_retries=3,
                retry_on=(ValueError,),
                load_to_bronze_fn=self._fake_load_to_bronze,
                write_landing_record_fn=self._fake_write_landing_record,
                write_run_metadata_fn=self._fake_write_run_metadata,
                sleep_fn=sleeps.append,
                jitter_fn=lambda: 0.0,
            )
        self.assertEqual(sleeps, [])
        self.assertEqual(len(self.metadata_calls), 1)
        self.assertEqual(self.metadata_calls[0].status, "failed")


if __name__ == "__main__":
    unittest.main()
