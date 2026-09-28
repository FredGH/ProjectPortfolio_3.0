"""Tests for core.scoring.calibration's label CRUD (PLAN.md Step 16).

Uses live Postgres via zzfixture-scoped rows, same pattern as
test_scoring_hard_filters.py.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.scoring.calibration import delete_label, read_labels, write_label
from core.settings import get_settings


class TestJobLabelCrud(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture label crud user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_write_then_read_round_trips_the_label(self) -> None:
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "strong")
        labels = read_labels(self.app_engine, self.user_id)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0].job_group_id, "zzfixture-job-1")
        self.assertEqual(labels[0].label, "strong")

    def test_writing_the_same_job_again_overwrites_not_duplicates(self) -> None:
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "strong")
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "no")
        labels = read_labels(self.app_engine, self.user_id)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0].label, "no")

    def test_delete_removes_the_label(self) -> None:
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "maybe")
        delete_label(self.app_engine, self.user_id, "zzfixture-job-1")
        labels = read_labels(self.app_engine, self.user_id)
        self.assertEqual(labels, [])

    def test_read_labels_returns_empty_list_when_none_exist(self) -> None:
        self.assertEqual(read_labels(self.app_engine, self.user_id), [])


if __name__ == "__main__":
    unittest.main()
