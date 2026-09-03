"""Integration test proving run_connector's bronze write lands, end to end."""

from __future__ import annotations

import datetime
import tempfile
import unittest
import uuid
from collections.abc import Iterator

from sqlalchemy import text

from core.db.session import build_engine, session_scope
from core.ingestion.raw_job import RawJob
from core.ingestion.runner import run_connector
from core.settings import get_settings


def _live_migration_engine():
    """Connect to Postgres, skip test if unreachable."""
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any connection failure means "skip"
        raise unittest.SkipTest(
            f"Postgres not reachable ({exc}); run "
            "`docker compose up -d postgres` first."
        ) from None
    return engine


class _OneJobConnector:
    """A connector yielding exactly one hardcoded RawJob, for a real
    end-to-end proof that run_connector's bronze write actually lands."""

    def __init__(self, source_job_id: str) -> None:
        """Store the source_job_id this connector's one job will carry.

        Args:
            source_job_id: The source job id to embed in the yielded job.
        """
        self._source_job_id = source_job_id

    def fetch(
        self, query: object, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield one job, ignoring query/since."""
        yield RawJob(
            source_name="runner_it_test",
            source_job_id=self._source_job_id,
            job_url="https://example.com/job",
            job_url_canonical="https://example.com/job",
            payload={"raw_text": "integration test payload"},
            fetched_at=datetime.datetime.now(datetime.UTC),
            run_id=run_id,
            request_params={},
            payload_sha256=f"sha-{self._source_job_id}",
        )


class TestRunConnectorBronzeIntegration(unittest.TestCase):
    """Proves run_connector's bronze write actually lands, end to end."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect to live Postgres once for the whole test class."""
        cls.migration_engine = _live_migration_engine()

    def setUp(self) -> None:
        """Set up a scratch landing dir and a unique source_job_id."""
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.landing_uri = f"file://{self._tmp_dir.name}"
        self.source_job_id = f"test-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        """Clean up the scratch landing dir and the row this test wrote."""
        self._tmp_dir.cleanup()
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text(
                    "DELETE FROM bronze.raw_jobs WHERE source_name = 'runner_it_test' "
                    "AND source_job_id = :sjid"
                ),
                {"sjid": self.source_job_id},
            )

    def test_run_connector_lands_a_real_bronze_row(self) -> None:
        """A real connector run produces exactly one queryable bronze row."""
        result = run_connector(
            connector_key="runner_it_test",
            connector=_OneJobConnector(self.source_job_id),
            query="integration-test-query",
            since=None,
            entry_method="api",
            landing_uri=self.landing_uri,
            database_url=get_settings().database_url,
        )
        self.assertEqual(len(result.raw_jobs), 1)

        with session_scope(self.migration_engine) as conn:
            row = conn.execute(
                text(
                    "SELECT payload->>'raw_text' AS raw_text, entry_method "
                    "FROM bronze.raw_jobs "
                    "WHERE source_name = 'runner_it_test' AND source_job_id = :sjid"
                ),
                {"sjid": self.source_job_id},
            ).one()
        self.assertEqual(row.raw_text, "integration test payload")
        self.assertEqual(row.entry_method, "api")


if __name__ == "__main__":
    unittest.main()
