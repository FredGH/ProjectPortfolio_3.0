"""Schema tests for pipeline.stage_run (migration 0030)."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine


class TestStageRunSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM pipeline.stage_run WHERE stage LIKE 'zzfixture-%'")
            )

    def test_app_role_can_insert_and_read_its_own_run(self) -> None:
        with self.app_engine.begin() as conn:
            run_id = conn.execute(
                text(
                    "INSERT INTO pipeline.stage_run (stage, status) "
                    "VALUES ('zzfixture-stage', 'completed') RETURNING run_id"
                )
            ).scalar_one()
        with self.owner.connect() as conn:
            row = conn.execute(
                text("SELECT stage, status FROM pipeline.stage_run WHERE run_id = :id"),
                {"id": run_id},
            ).one()
        self.assertEqual(row.stage, "zzfixture-stage")
        self.assertEqual(row.status, "completed")

    def test_only_one_running_row_allowed_across_the_whole_table(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO pipeline.stage_run (stage, status) "
                    "VALUES ('zzfixture-a', 'running')"
                )
            )
        with self.assertRaises(IntegrityError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO pipeline.stage_run (stage, status) "
                        "VALUES ('zzfixture-b', 'running')"
                    )
                )
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pipeline.stage_run SET status = 'completed' "
                    "WHERE stage = 'zzfixture-a'"
                )
            )

    def test_invalid_status_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO pipeline.stage_run (stage, status) "
                        "VALUES ('zzfixture-bad', 'bogus')"
                    )
                )


if __name__ == "__main__":
    unittest.main()
