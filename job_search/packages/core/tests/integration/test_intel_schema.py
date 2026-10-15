"""Schema tests for the intel tables (migration 0037)."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

_KEY = "zzfixture-intel-key"


class TestIntelSchema(unittest.TestCase):
    """The intel tables exist with the constraints and grants the spec needs."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove every fixture row."""
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM intel.company_intel WHERE company_key = :k"),
                {"k": _KEY},
            )
            conn.execute(
                text(
                    "DELETE FROM intel.crawl_cache WHERE url LIKE 'https://zzfixture%'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM intel.job_company WHERE job_group_id LIKE 'zzfixture%'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM intel.job_competitive_angle "
                    "WHERE job_group_id LIKE 'zzfixture%'"
                )
            )

    def _insert_intel(self, conn, version: int) -> None:
        """Insert one company_intel row as the app role.

        Args:
            conn: An open connection.
            version: The version number to insert.
        """
        conn.execute(
            text(
                "INSERT INTO intel.company_intel "
                "(company_key, version, display_name, status, content, pages, usage) "
                "VALUES (:k, :v, 'Zz Co', 'ok', '{}'::jsonb, '[]'::jsonb, '{}'::jsonb)"
            ),
            {"k": _KEY, "v": version},
        )

    def test_a_version_is_unique_per_company(self) -> None:
        with self.app_engine.begin() as conn:
            self._insert_intel(conn, 1)
        with self.assertRaises(IntegrityError):
            with self.app_engine.begin() as conn:
                self._insert_intel(conn, 1)

    def test_intel_is_append_only_for_the_app_role(self) -> None:
        with self.app_engine.begin() as conn:
            self._insert_intel(conn, 1)
        with self.assertRaises(ProgrammingError):
            with self.app_engine.begin() as conn:
                conn.execute(text("UPDATE intel.company_intel SET display_name = 'x'"))

    def test_status_must_be_a_known_value(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.app_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO intel.company_intel "
                        "(company_key, version, display_name, status, content, pages, "
                        "usage) VALUES (:k, 1, 'Zz', 'bogus', '{}'::jsonb, "
                        "'[]'::jsonb, '{}'::jsonb)"
                    ),
                    {"k": _KEY},
                )

    def test_the_cache_keeps_one_row_per_url_and_day(self) -> None:
        insert = text(
            "INSERT INTO intel.crawl_cache (url, fetched_on, status, markdown) "
            "VALUES ('https://zzfixture.example/', current_date, 'ok', 'x')"
        )
        with self.app_engine.begin() as conn:
            conn.execute(insert)
        with self.assertRaises(IntegrityError):
            with self.app_engine.begin() as conn:
                conn.execute(insert)

    def test_the_resolution_method_must_be_known_and_is_updatable(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.app_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO intel.job_company (job_group_id, poster_kind, "
                        "method) VALUES ('zzfixture-j', 'employer', 'bogus')"
                    )
                )
        with self.app_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO intel.job_company (job_group_id, poster_kind, method) "
                    "VALUES ('zzfixture-j', 'employer', 'employer')"
                )
            )
            conn.execute(
                text(
                    "UPDATE intel.job_company SET method = 'manual' "
                    "WHERE job_group_id = 'zzfixture-j'"
                )
            )

    def test_angles_can_be_appended_but_not_changed(self) -> None:
        with self.app_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO intel.job_competitive_angle (job_group_id, "
                    "company_key, intel_version, content, usage) VALUES "
                    "('zzfixture-j', :k, 1, '{}'::jsonb, '{}'::jsonb)"
                ),
                {"k": _KEY},
            )
        with self.assertRaises(ProgrammingError):
            with self.app_engine.begin() as conn:
                conn.execute(text("UPDATE intel.job_competitive_angle SET model = 'x'"))


if __name__ == "__main__":
    unittest.main()
