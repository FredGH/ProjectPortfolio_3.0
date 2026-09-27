"""Integration tests for core.scoring.hard_filters against live Postgres.

Uses real gold.dim_job rows via fixture job_group_ids so these tests never
touch real job data. dim_job is built by dbt from silver sources this suite
does not have access to seed directly, so these tests insert straight into
gold.dim_job (owner role) and clean it up — the same tactic
test_skills_router.py's _insert_esco_skill uses for esco.skill.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.scoring.hard_filters import run_hard_filters
from core.scoring.preferences import UserPreference, write_preference


class TestHardFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture filters user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.user_preference WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE 'fixture-job-%'")
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _insert_job(self, job_group_id: str, **overrides) -> None:
        """Insert one minimal gold.dim_job fixture row.

        Note: gold.dim_job has no `title` column (real columns are
        `title_raw`/`title_for_display`) — `title_for_display` is used
        here instead of the brief's placeholder `title` key.
        """
        defaults = {
            "job_group_id": job_group_id,
            "title_for_display": "zzfixture role",
            "company": "zzfixture co",
            "location": "London",
            "engagement_type": "contract",
            "ir35_status": "outside",
            "seniority_band": "senior",
            "rate_currency": "GBP",
            "rate_daily_equivalent": 600,
            "rate_annualised": None,
            "posted_at": "2026-09-01",
        }
        defaults.update(overrides)
        columns = ", ".join(defaults)
        placeholders = ", ".join(f":{k}" for k in defaults)
        with self.engine.begin() as conn:
            conn.execute(
                text(f"INSERT INTO gold.dim_job ({columns}) VALUES ({placeholders})"),
                defaults,
            )

    def _passed(self, job_group_id: str) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT hard_filter_passed FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": job_group_id},
            ).scalar_one()

    def test_no_preference_row_at_all_passes_every_job(self) -> None:
        self._insert_job("fixture-job-nopref")
        run_hard_filters(self.engine, self.user_id)
        self.assertTrue(self._passed("fixture-job-nopref"))

    def test_excluded_ir35_status_fails_a_matching_job_but_not_others(self) -> None:
        self._insert_job("fixture-job-inside", ir35_status="inside")
        self._insert_job("fixture-job-outside", ir35_status="outside")
        write_preference(
            self.engine, self.user_id, UserPreference(excluded_ir35_statuses=["inside"])
        )
        run_hard_filters(self.engine, self.user_id)
        self.assertFalse(self._passed("fixture-job-inside"))
        self.assertTrue(self._passed("fixture-job-outside"))

    def test_unknown_ir35_status_is_never_auto_excluded(self) -> None:
        self._insert_job("fixture-job-unknown-ir35", ir35_status="unknown")
        write_preference(
            self.engine, self.user_id, UserPreference(excluded_ir35_statuses=["inside"])
        )
        run_hard_filters(self.engine, self.user_id)
        self.assertTrue(self._passed("fixture-job-unknown-ir35"))

    def test_min_rate_daily_excludes_a_lower_rate(self) -> None:
        self._insert_job("fixture-job-lowrate", rate_daily_equivalent=300)
        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=500))
        run_hard_filters(self.engine, self.user_id)
        self.assertFalse(self._passed("fixture-job-lowrate"))

    def test_a_rate_in_a_different_currency_is_never_compared_to_the_floor(
        self,
    ) -> None:
        # rate_annualised/rate_daily_equivalent are in the job's OWN
        # rate_currency, never converted (_gold.yml's own documented
        # caveat) — a non-GBP rate must not be treated as clearing or
        # missing a GBP floor by accident.
        self._insert_job(
            "fixture-job-usd", rate_currency="USD", rate_daily_equivalent=900
        )
        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=500))
        run_hard_filters(self.engine, self.user_id)
        self.assertFalse(self._passed("fixture-job-usd"))

    def test_seniority_band_range_is_inclusive(self) -> None:
        self._insert_job("fixture-job-junior", seniority_band="junior")
        write_preference(
            self.engine,
            self.user_id,
            UserPreference(min_seniority_band="mid", max_seniority_band="lead"),
        )
        run_hard_filters(self.engine, self.user_id)
        self.assertFalse(self._passed("fixture-job-junior"))

    def test_rerunning_updates_rather_than_duplicating(self) -> None:
        self._insert_job("fixture-job-rerun")
        run_hard_filters(self.engine, self.user_id)
        run_hard_filters(self.engine, self.user_id)
        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-rerun'"
                ),
                {"u": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
