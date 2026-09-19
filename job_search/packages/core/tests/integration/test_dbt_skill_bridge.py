"""Builds the Step 14 dbt models against fixture data and checks the bridge."""

from __future__ import annotations

import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    insert_job_skills,
    insert_mapping,
    live_owner_engine,
    purge_fixtures,
)

from core.skills.esco_load import load_esco

_DBT_DIR = Path(__file__).resolve().parents[4] / "dbt"
_SELECT = ["--select", "silver__skill", "silver__bridge_job_skill"]


def _dbt_run() -> None:
    subprocess.run(
        [
            "dbt",
            "run",
            "--project-dir",
            str(_DBT_DIR),
            "--profiles-dir",
            str(_DBT_DIR),
            *_SELECT,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(shutil.which("dbt"), "dbt is not installed")
@unittest.skipUnless((_DBT_DIR / "dbt_packages").is_dir(), "run `dbt deps` first")
class TestSkillBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        sfx = uuid.uuid4().hex[:8]
        self.job_a = f"fixture-job-a-{sfx}"
        self.job_b = f"fixture-job-b-{sfx}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
                    "VALUES ('custom:fixture-c', 'Fixture Custom')"
                )
            )
            for norm, skill_id, method in (
                ("zzfixture cloud platforms", "fixture-cloud", "label"),
                ("zzfixture cloud computing", "fixture-cloud", "label"),
                ("zzfixture sql", "fixture-sql", "label"),
                ("zzfixture python", "fixture-python", "label"),
                ("zzfixture custom", "custom:fixture-c", "alias"),
            ):
                insert_mapping(
                    conn, norm, skill_id=skill_id, method=method, review_status=None
                )
            insert_mapping(conn, "zzfixture unmapped")  # open: must be excluded
            insert_job_skills(
                conn,
                self.job_a,
                "local.v1",
                [
                    (
                        "ZZFixture Cloud Platforms",
                        "zzfixture cloud platforms",
                        "nice_to_have",
                    ),
                    (
                        "zzfixture cloud computing",
                        "zzfixture cloud computing",
                        "must_have",
                    ),
                    ("zzfixture unmapped", "zzfixture unmapped", "must_have"),
                    ("zzfixture custom", "zzfixture custom", "nice_to_have"),
                ],
            )
            insert_job_skills(
                conn,
                self.job_b,
                "local.v0",
                [("zzfixture python", "zzfixture python", "must_have")],
            )
            conn.execute(
                text(
                    "UPDATE silver.job_skill_extraction "
                    "SET extracted_at = now() - interval '1 day' "
                    "WHERE job_group_id = :j"
                ),
                {"j": self.job_b},
            )
            insert_job_skills(
                conn,
                self.job_b,
                "local.v1",
                [("zzfixture sql", "zzfixture sql", "nice_to_have")],
            )
        _dbt_run()

    def tearDown(self) -> None:
        purge_fixtures(self.engine)
        _dbt_run()  # rebuild without the fixtures so later `dbt test` runs stay clean

    def _bridge(self) -> dict[tuple[str, str], tuple[str, int]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT job_group_id, skill_id, requirement_level, mention_count "
                    "FROM silver.silver__bridge_job_skill "
                    "WHERE job_group_id IN (:a, :b)"
                ),
                {"a": self.job_a, "b": self.job_b},
            ).all()
        return {
            (r.job_group_id, r.skill_id): (r.requirement_level, r.mention_count)
            for r in rows
        }

    def test_collapses_to_job_and_skill_with_must_have_winning(self) -> None:
        self.assertEqual(
            self._bridge()[(self.job_a, "fixture-cloud")], ("must_have", 2)
        )

    def test_excludes_unmapped_strings(self) -> None:
        skills = {skill for (_job, skill) in self._bridge()}
        self.assertNotIn(None, skills)
        self.assertEqual(
            {s for (j, s) in self._bridge() if j == self.job_a},
            {"fixture-cloud", "custom:fixture-c"},
        )

    def test_uses_only_each_jobs_latest_extraction(self) -> None:
        self.assertEqual(
            {s for (j, s) in self._bridge() if j == self.job_b}, {"fixture-sql"}
        )

    def test_skill_vocabulary_unions_esco_and_custom(self) -> None:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT skill_id, source FROM silver.silver__skill "
                    "WHERE skill_id IN ('fixture-cloud', 'custom:fixture-c')"
                )
            ).all()
        self.assertEqual(
            {(r.skill_id, r.source) for r in rows},
            {("fixture-cloud", "esco"), ("custom:fixture-c", "custom")},
        )


if __name__ == "__main__":
    unittest.main()
