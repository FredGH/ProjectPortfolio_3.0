"""Integration tests for row-level security isolation across user boundaries."""

from __future__ import annotations

import datetime
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
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(
            f"Postgres not reachable ({exc}); "
            "run `docker compose up -d postgres` first."
        ) from None
    return engine


class TestRowLevelSecurityIsolation(unittest.TestCase):
    """Negative tests per-user table — proves Step 1a acceptance criterion."""

    @classmethod
    def setUpClass(cls) -> None:
        """Initialise engines once per test class."""
        cls.migration_engine = _live_migration_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        """Insert test users and quotas before each test."""
        self.user_a = uuid.uuid4()
        self.user_b = uuid.uuid4()
        with session_scope(self.migration_engine) as conn:
            for user_id in (self.user_a, self.user_b):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'Test User')"
                    ),
                    {"id": user_id, "email": f"{user_id}@example.com"},
                )
                conn.execute(
                    text(
                        "INSERT INTO user_quota "
                        "(user_id, period_start, monthly_llm_spend_cap_usd, "
                        " artefact_generation_cap, alert_cap) "
                        "VALUES (:user_id, :period_start, 20, 50, 10)"
                    ),
                    {
                        "user_id": user_id,
                        "period_start": datetime.date(2026, 9, 1),
                    },
                )

    def tearDown(self) -> None:
        """Clean up test users and quotas via migration engine."""
        with session_scope(self.migration_engine) as conn:
            for user_id in (self.user_a, self.user_b):
                conn.execute(
                    text("DELETE FROM user_quota WHERE user_id = :id"),
                    {"id": user_id},
                )
                conn.execute(
                    text("DELETE FROM app_user WHERE id = :id"),
                    {"id": user_id},
                )

    def test_app_user_query_without_where_returns_only_the_session_users_row(
        self,
    ) -> None:
        """App role without WHERE sees only its own user — not others."""
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            rows = conn.execute(text("SELECT id FROM app_user")).fetchall()
        ids = {row.id for row in rows}
        self.assertIn(self.user_a, ids)
        self.assertNotIn(self.user_b, ids)

    def test_user_quota_query_without_where_returns_only_the_session_users_row(
        self,
    ) -> None:
        """App role without WHERE sees only its own quota — not others."""
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            rows = conn.execute(text("SELECT user_id FROM user_quota")).fetchall()
        user_ids = {row.user_id for row in rows}
        self.assertIn(self.user_a, user_ids)
        self.assertNotIn(self.user_b, user_ids)

    def test_switching_session_user_flips_visibility(self) -> None:
        """Session user switch reverses which user's data is visible."""
        with session_scope(self.app_engine, user_id=self.user_b) as conn:
            rows = conn.execute(text("SELECT user_id FROM user_quota")).fetchall()
        user_ids = {row.user_id for row in rows}
        self.assertIn(self.user_b, user_ids)
        self.assertNotIn(self.user_a, user_ids)


if __name__ == "__main__":
    unittest.main()
