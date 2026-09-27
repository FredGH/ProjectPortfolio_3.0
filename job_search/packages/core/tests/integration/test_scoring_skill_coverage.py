"""Integration tests for core.scoring.skill_coverage against live Postgres."""

from __future__ import annotations

import datetime
import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.cv.schema import CVTruthBase, Skill
from core.cv.store import write_truth_base
from core.db.session import build_engine
from core.scoring.skill_coverage import run_skill_coverage
from core.settings import get_settings


class TestSkillCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.job_must = "fixture-job-cov-must"
        self.job_nice = "fixture-job-cov-nice"
        self.job_mixed = "fixture-job-cov-mixed"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture coverage user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            for job in (self.job_must, self.job_nice, self.job_mixed):
                conn.execute(
                    text(
                        "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                        "company) VALUES (:j, 'zzfixture role', 'zzfixture co')"
                    ),
                    {"j": job},
                )
                conn.execute(
                    text(
                        "INSERT INTO scoring.job_score (user_id, job_group_id, "
                        "hard_filter_passed) VALUES (:u, :j, true)"
                    ),
                    {"u": self.user_id, "j": job},
                )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__bridge_job_skill "
                    "(job_group_id, skill_id, requirement_level, mention_count) "
                    "VALUES (:j, 'fixture-python', 'must_have', 1)"
                ),
                {"j": self.job_must},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__bridge_job_skill "
                    "(job_group_id, skill_id, requirement_level, mention_count) "
                    "VALUES (:j, 'fixture-python', 'nice_to_have', 1)"
                ),
                {"j": self.job_nice},
            )
            # A job that lists BOTH a must-have and a nice-to-have skill, so
            # partial coverage within one job can be compared: covering the
            # must-have half should outscore covering the nice-to-have half,
            # even though both are "one of two skills" matched.
            conn.execute(
                text(
                    "INSERT INTO silver.silver__bridge_job_skill "
                    "(job_group_id, skill_id, requirement_level, mention_count) "
                    "VALUES (:j, 'fixture-python', 'must_have', 1)"
                ),
                {"j": self.job_mixed},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__bridge_job_skill "
                    "(job_group_id, skill_id, requirement_level, mention_count) "
                    "VALUES (:j, 'fixture-sql', 'nice_to_have', 1)"
                ),
                {"j": self.job_mixed},
            )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__bridge_job_skill "
                    "WHERE job_group_id LIKE 'fixture-job-cov-%'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id LIKE 'fixture-job-cov-%'"
                )
            )
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _write_cv(
        self, last_used: str | None, canonical_id: str = "fixture-python"
    ) -> None:
        truth_base = CVTruthBase(
            identity="zzfixture Person",
            headline="Engineer",
            skills=[
                Skill(name="Skill", canonical_id=canonical_id, last_used=last_used)
            ],
        )
        write_truth_base(
            self.app_engine, self.user_id, "zzfixture markdown", truth_base, label="f"
        )

    def _coverage(self, job_group_id: str) -> float:
        with self.owner.connect() as conn:
            return float(
                conn.execute(
                    text(
                        "SELECT skill_coverage_score FROM scoring.job_score "
                        "WHERE user_id = :u AND job_group_id = :j"
                    ),
                    {"u": self.user_id, "j": job_group_id},
                ).scalar_one()
            )

    def test_covering_the_must_have_half_of_a_mixed_job_scores_higher(
        self,
    ) -> None:
        # fixture-job-cov-mixed lists one must-have (fixture-python) and one
        # nice-to-have (fixture-sql) skill. Matching only the must-have half
        # and matching only the nice-to-have half are both "one of two
        # skills" in raw count, but the weighted coverage score must favour
        # the must-have match — this is where must-have weighting actually
        # matters (a mix within one job), unlike full-vs-full across two
        # separate single-skill jobs.
        self._write_cv(last_used="2026-06", canonical_id="fixture-python")
        run_skill_coverage(
            self.app_engine, self.user_id, as_of=datetime.date(2026, 9, 27)
        )
        must_half_score = self._coverage(self.job_mixed)

        self._write_cv(last_used="2026-06", canonical_id="fixture-sql")
        run_skill_coverage(
            self.app_engine, self.user_id, as_of=datetime.date(2026, 9, 27)
        )
        nice_half_score = self._coverage(self.job_mixed)

        self.assertGreater(must_half_score, nice_half_score)

    def test_a_skill_unused_for_six_years_scores_lower_than_one_used_last_year(
        self,
    ) -> None:
        self._write_cv(last_used="2020-01")
        run_skill_coverage(
            self.app_engine, self.user_id, as_of=datetime.date(2026, 9, 27)
        )
        stale_score = self._coverage(self.job_must)
        self._write_cv(last_used="2025-06")
        run_skill_coverage(
            self.app_engine, self.user_id, as_of=datetime.date(2026, 9, 27)
        )
        fresh_score = self._coverage(self.job_must)
        self.assertLess(stale_score, fresh_score)

    def test_a_job_with_no_bridge_rows_stays_null_not_zero(self) -> None:
        # A job scoring.job_score row with hard_filter_passed=true but no
        # matching silver.silver__bridge_job_skill rows (e.g. extraction hasn't run
        # for it yet) has no data to score coverage from — that is "unknown,"
        # not "zero coverage," so it must be excluded from the blend later
        # (core.scoring.blend), not scored as a fit failure.
        no_skills_job = "fixture-job-cov-no-skills"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company) VALUES (:j, 'zzfixture role', 'zzfixture co')"
                ),
                {"j": no_skills_job},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, :j, true)"
                ),
                {"u": self.user_id, "j": no_skills_job},
            )
        self._write_cv(last_used="2026-06")
        run_skill_coverage(
            self.app_engine, self.user_id, as_of=datetime.date(2026, 9, 27)
        )
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT skill_coverage_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": no_skills_job},
            ).scalar_one()
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = :j"),
                {"j": no_skills_job},
            )
        self.assertIsNone(score)


if __name__ == "__main__":
    unittest.main()
