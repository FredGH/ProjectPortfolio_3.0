from __future__ import annotations

import datetime
import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine, session_scope
from core.ingestion.bronze import load_to_bronze
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


class TestLoadToBronze(unittest.TestCase):
    """Tests for load_to_bronze's dedup-on-unchanged-payload merge semantics."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.migration_engine = _live_migration_engine()

    def setUp(self) -> None:
        self.source_name = f"test_source_{uuid.uuid4().hex[:8]}"
        self.source_job_id = "test-job-1"
        self.fetched_at = datetime.datetime.now(datetime.UTC)

    def tearDown(self) -> None:
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text("DELETE FROM bronze.raw_jobs WHERE source_name = :name"),
                {"name": self.source_name},
            )

    def _load(self, *, payload_sha256: str, payload: dict[str, object]) -> None:
        load_to_bronze(
            database_url=get_settings().database_url,
            source_name=self.source_name,
            source_job_id=self.source_job_id,
            job_url="https://example.com/job",
            job_url_canonical="https://example.com/job",
            entry_method="manual",
            fetched_at=self.fetched_at,
            run_id="01J000000000000000000000",
            request_params={},
            payload=payload,
            payload_sha256=payload_sha256,
        )

    def _row_count(self) -> int:
        with session_scope(self.migration_engine) as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM bronze.raw_jobs WHERE source_name = :name"),
                {"name": self.source_name},
            )
            return result.scalar_one()

    def test_reloading_the_same_payload_is_a_no_op(self) -> None:
        """Two loads with identical payload_sha256 leave exactly one row."""
        self._load(payload_sha256="abc123", payload={"raw_text": "hello"})
        self._load(payload_sha256="abc123", payload={"raw_text": "hello"})
        self.assertEqual(self._row_count(), 1)

    def test_a_changed_payload_produces_a_new_version_row(self) -> None:
        """A different payload_sha256 for the same job adds a second row."""
        self._load(payload_sha256="abc123", payload={"raw_text": "hello"})
        self._load(payload_sha256="def456", payload={"raw_text": "hello, edited"})
        self.assertEqual(self._row_count(), 2)

    def test_payload_is_stored_as_a_navigable_jsonb_object_not_flattened(self) -> None:
        """payload lands as one JSONB column, not flattened into subcolumns."""
        self._load(
            payload_sha256="jsonb-check",
            payload={"raw_text": "hello", "nested": {"a": 1}},
        )
        with session_scope(self.migration_engine) as conn:
            result = conn.execute(
                text(
                    "SELECT payload->>'raw_text', payload->'nested'->>'a' "
                    "FROM bronze.raw_jobs "
                    "WHERE source_name = :name AND payload_sha256 = 'jsonb-check'"
                ),
                {"name": self.source_name},
            )
            raw_text, nested_a = result.one()
        self.assertEqual(raw_text, "hello")
        self.assertEqual(nested_a, "1")


if __name__ == "__main__":
    unittest.main()
