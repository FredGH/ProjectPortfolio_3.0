from __future__ import annotations

import datetime
import unittest
import uuid

from sqlalchemy import text

from core.db.quota import check_and_increment_shared_quota
from core.db.session import build_engine, session_scope
from core.settings import get_settings


def _live_migration_engine():
    """Create and verify connection to live Postgres for integration tests."""
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(
            f"Postgres not reachable ({exc}); run `docker compose up -d"
            " postgres` first."
        ) from None
    return engine


class TestSharedQuotaGuard(unittest.TestCase):
    """Test the shared quota guard for fair-use enforcement."""

    @classmethod
    def setUpClass(cls) -> None:
        """Set up the live migration engine once for all tests."""
        cls.migration_engine = _live_migration_engine()

    def setUp(self) -> None:
        """Insert a test quota row with limit of 2."""
        self.resource_name = f"test_resource_{uuid.uuid4().hex[:8]}"
        self.period_start = datetime.date(2026, 9, 1)
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text(
                    "INSERT INTO shared_api_quota "
                    "(resource_name, period_start, total_limit) "
                    "VALUES (:name, :period, 2)"
                ),
                {"name": self.resource_name, "period": self.period_start},
            )

    def tearDown(self) -> None:
        """Clean up the test quota row."""
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text("DELETE FROM shared_api_quota WHERE resource_name = :name"),
                {"name": self.resource_name},
            )

    def test_allows_calls_up_to_the_limit_then_blocks(self) -> None:
        """Verify first two calls succeed and third call is blocked."""
        with session_scope(self.migration_engine) as conn:
            first = check_and_increment_shared_quota(
                conn, resource_name=self.resource_name, period_start=self.period_start
            )
            second = check_and_increment_shared_quota(
                conn, resource_name=self.resource_name, period_start=self.period_start
            )
            third = check_and_increment_shared_quota(
                conn, resource_name=self.resource_name, period_start=self.period_start
            )
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertFalse(third)

    def test_unknown_resource_period_is_blocked_not_an_error(self) -> None:
        """Verify unknown resource silently returns False, not error."""
        with session_scope(self.migration_engine) as conn:
            allowed = check_and_increment_shared_quota(
                conn, resource_name="never_configured", period_start=self.period_start
            )
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
