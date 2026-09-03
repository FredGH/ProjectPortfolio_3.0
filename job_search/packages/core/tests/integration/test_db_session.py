"""Integration tests for session_scope and RLS visibility against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine, session_scope
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
            f"Postgres not reachable ({exc}); run `docker compose up -d postgres` first."
        ) from None
    return engine


class TestSessionScope(unittest.TestCase):
    """Session scope context manager enforces RLS via app.current_user_id GUC."""

    @classmethod
    def setUpClass(cls) -> None:
        """Initialise engines once per test class."""
        cls.migration_engine = _live_migration_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        """Insert a test user before each test."""
        with session_scope(self.migration_engine) as conn:
            self.user_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'Test User')"
                ),
                {"id": self.user_id, "email": f"{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        """Delete the test user after each test."""
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_session_scope_sets_the_session_guc_and_scopes_visibility(
        self,
    ) -> None:
        """App role sees only the specified user when GUC is set."""
        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            rows = conn.execute(text("SELECT id FROM app_user")).fetchall()
        self.assertEqual([row.id for row in rows], [self.user_id])

    def test_session_scope_without_user_id_sees_nothing_via_the_app_role(
        self,
    ) -> None:
        """App role sees no rows when GUC is not set (fail-closed)."""
        with session_scope(self.app_engine) as conn:
            rows = conn.execute(text("SELECT id FROM app_user")).fetchall()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
