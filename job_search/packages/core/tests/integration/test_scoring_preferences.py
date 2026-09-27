"""Integration tests for core.scoring.preferences against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.scoring.preferences import UserPreference, read_preference, write_preference


class TestPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture prefs user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.user_preference WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_reading_an_unset_preference_returns_all_defaults(self) -> None:
        pref = read_preference(self.engine, self.user_id)
        self.assertEqual(pref, UserPreference())

    def test_writing_then_reading_round_trips(self) -> None:
        written = UserPreference(
            preferred_locations=["London", "Remote"],
            remote_ok="preferred",
            contract_types=["contract", "ftc"],
            excluded_ir35_statuses=["inside"],
            min_seniority_band="senior",
            max_seniority_band="principal",
            min_salary_annual=None,
            min_rate_daily=500,
            max_posting_age_days=30,
        )
        write_preference(self.engine, self.user_id, written)
        self.assertEqual(read_preference(self.engine, self.user_id), written)

    def test_writing_twice_updates_rather_than_duplicating(self) -> None:
        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=400))
        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=600))
        self.assertEqual(read_preference(self.engine, self.user_id).min_rate_daily, 600)
        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.user_preference WHERE user_id = :id"
                ),
                {"id": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 1)

    def test_a_user_cannot_read_another_users_preference_via_the_app_role(
        self,
    ) -> None:
        from core.db.session import build_engine, session_scope
        from core.settings import get_settings

        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=999))
        app_engine = build_engine(get_settings().app_database_url)
        other_user = uuid.uuid4()
        with session_scope(app_engine, user_id=other_user) as conn:
            row = conn.execute(
                text("SELECT * FROM scoring.user_preference WHERE user_id = :id"),
                {"id": self.user_id},
            ).one_or_none()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
