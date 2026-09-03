from __future__ import annotations

import datetime
import unittest
from collections.abc import Iterator

from core.ingestion.connector import Connector
from core.ingestion.raw_job import RawJob


class TestRawJob(unittest.TestCase):
    """Tests for the RawJob envelope's shape and immutability."""

    def test_constructs_with_all_required_fields(self) -> None:
        """RawJob accepts exactly the fields PLAN.md Step 3 specifies."""
        job = RawJob(
            source_name="adzuna",
            source_job_id="123",
            job_url="https://example.com/job/123",
            job_url_canonical="https://example.com/job/123",
            payload={"title": "Data Engineer"},
            fetched_at=datetime.datetime(2026, 9, 3, tzinfo=datetime.UTC),
            run_id="01J000000000000000000000",
            request_params={"query": "data engineer"},
            payload_sha256="abc123",
        )
        self.assertEqual(job.source_name, "adzuna")
        self.assertEqual(job.payload, {"title": "Data Engineer"})

    def test_is_frozen(self) -> None:
        """RawJob instances are immutable matching PLAN.md immutable philosophy."""
        job = RawJob(
            source_name="adzuna",
            source_job_id="123",
            job_url="https://example.com/job/123",
            job_url_canonical="https://example.com/job/123",
            payload={},
            fetched_at=datetime.datetime(2026, 9, 3, tzinfo=datetime.UTC),
            run_id="01J000000000000000000000",
            request_params={},
            payload_sha256="abc123",
        )
        with self.assertRaises(AttributeError):
            job.source_name = "changed"  # type: ignore[misc]


class _FakeConnector:
    """A minimal Connector implementation, proving the Protocol is satisfiable
    with just one method — the whole point of Task 1's contract."""

    def fetch(
        self, query: object, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield one hardcoded RawJob, ignoring query/since."""
        yield RawJob(
            source_name="fake",
            source_job_id="1",
            job_url="https://example.com/1",
            job_url_canonical="https://example.com/1",
            payload={},
            fetched_at=datetime.datetime(2026, 9, 3, tzinfo=datetime.UTC),
            run_id=run_id,
            request_params={},
            payload_sha256="x",
        )


class TestConnectorProtocol(unittest.TestCase):
    """Tests proving Connector is a genuine structural Protocol."""

    def test_a_class_with_a_matching_fetch_method_satisfies_the_protocol(self) -> None:
        """isinstance-style structural check: _FakeConnector IS-A Connector."""
        connector: Connector = _FakeConnector()
        jobs = list(connector.fetch(None, None, run_id="01J000000000000000000000"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "fake")


if __name__ == "__main__":
    unittest.main()
