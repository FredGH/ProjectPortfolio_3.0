"""Integration test for the target_company registry module."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.db.target_company import list_active_companies, upsert_target_company
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


class TestTargetCompanyRegistry(unittest.TestCase):
    """Proves upsert + list round-trip against a real Postgres table."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect to live Postgres once for the whole test class."""
        cls.engine = _live_migration_engine()

    def setUp(self) -> None:
        """Give each test a unique board_slug so tests never collide."""
        self.board_slug = f"test-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        """Delete the row this test wrote."""
        with self.engine.connect() as conn:
            conn.execute(
                text(
                    "DELETE FROM target_company "
                    "WHERE ats_provider = 'greenhouse' AND board_slug = :slug"
                ),
                {"slug": self.board_slug},
            )
            conn.commit()

    def test_upsert_then_list_round_trips_a_real_row(self) -> None:
        """A freshly upserted active company appears in list_active_companies."""
        with self.engine.connect() as conn:
            upsert_target_company(
                conn,
                name="Test Co",
                ats_provider="greenhouse",
                board_slug=self.board_slug,
            )
            conn.commit()

        with self.engine.connect() as conn:
            companies = list_active_companies(conn, ats_provider="greenhouse")
        self.assertIn(self.board_slug, [c.board_slug for c in companies])

    def test_inactive_company_excluded_from_list(self) -> None:
        """A company upserted with active=False never appears in the list."""
        with self.engine.connect() as conn:
            upsert_target_company(
                conn,
                name="Inactive Co",
                ats_provider="greenhouse",
                board_slug=self.board_slug,
                active=False,
            )
            conn.commit()

        with self.engine.connect() as conn:
            companies = list_active_companies(conn, ats_provider="greenhouse")
        self.assertNotIn(self.board_slug, [c.board_slug for c in companies])

    def test_upsert_is_idempotent_on_ats_provider_and_board_slug(self) -> None:
        """Re-upserting the same (ats_provider, board_slug) updates, not duplicates."""
        with self.engine.connect() as conn:
            upsert_target_company(
                conn,
                name="First Name",
                ats_provider="greenhouse",
                board_slug=self.board_slug,
            )
            conn.commit()
        with self.engine.connect() as conn:
            upsert_target_company(
                conn,
                name="Second Name",
                ats_provider="greenhouse",
                board_slug=self.board_slug,
            )
            conn.commit()

        with self.engine.connect() as conn:
            companies = list_active_companies(conn, ats_provider="greenhouse")
        matches = [c for c in companies if c.board_slug == self.board_slug]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].name, "Second Name")


if __name__ == "__main__":
    unittest.main()
