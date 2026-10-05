"""Schema tests for the tailoring tables (migration 0032)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.db.session import session_scope


class TestTailoringSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        self.user_a = uuid.uuid4()
        self.user_b = uuid.uuid4()
        with self.owner.begin() as conn:
            for user_id in (self.user_a, self.user_b):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'zzfixture tailoring user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM tailoring.tailored_cv WHERE user_id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )

    def _insert_run(self, user_id: uuid.UUID) -> uuid.UUID:
        with session_scope(self.app_engine, user_id=user_id) as conn:
            return conn.execute(
                text(
                    "INSERT INTO tailoring.tailored_cv "
                    "(user_id, job_group_id, truth_base_version, target_title) "
                    "VALUES (:user_id, 'zzfixture-job', 1, 'Zz Title') RETURNING id"
                ),
                {"user_id": user_id},
            ).scalar_one()

    def _insert_orphan(self, user_id: uuid.UUID, run_id: uuid.UUID) -> uuid.UUID:
        with session_scope(self.app_engine, user_id=user_id) as conn:
            return conn.execute(
                text(
                    "INSERT INTO tailoring.orphan_bullet "
                    "(tailored_cv_id, user_id, kind, section, experience_index, "
                    "bullet_index, text) "
                    "VALUES (:run_id, :user_id, 'orphan', 'experience', 0, 0, 'x') "
                    "RETURNING id"
                ),
                {"run_id": run_id, "user_id": user_id},
            ).scalar_one()

    def test_app_role_can_insert_and_read_its_own_run(self) -> None:
        run_id = self._insert_run(self.user_a)
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            row = conn.execute(
                text(
                    "SELECT status, attempts FROM tailoring.tailored_cv WHERE id = :id"
                ),
                {"id": run_id},
            ).one()
        self.assertEqual(row.status, "generating")
        self.assertEqual(row.attempts, 0)

    def test_another_user_cannot_see_the_run(self) -> None:
        run_id = self._insert_run(self.user_a)
        with session_scope(self.app_engine, user_id=self.user_b) as conn:
            rows = conn.execute(
                text("SELECT id FROM tailoring.tailored_cv WHERE id = :id"),
                {"id": run_id},
            ).all()
        self.assertEqual(rows, [])

    def test_another_user_cannot_see_the_orphans(self) -> None:
        run_id = self._insert_run(self.user_a)
        orphan_id = self._insert_orphan(self.user_a, run_id)
        with session_scope(self.app_engine, user_id=self.user_b) as conn:
            rows = conn.execute(
                text("SELECT id FROM tailoring.orphan_bullet WHERE id = :id"),
                {"id": orphan_id},
            ).all()
        self.assertEqual(rows, [])

    def test_progress_column_is_nullable_jsonb_the_app_role_can_write(self) -> None:
        run_id = self._insert_run(self.user_a)
        with self.owner.connect() as conn:
            col = conn.execute(
                text(
                    "SELECT data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'tailoring' AND table_name = 'tailored_cv' "
                    "AND column_name = 'progress'"
                )
            ).one()
        self.assertEqual((col.data_type, col.is_nullable), ("jsonb", "YES"))
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            self.assertIsNone(
                conn.execute(
                    text("SELECT progress FROM tailoring.tailored_cv WHERE id = :id"),
                    {"id": run_id},
                ).scalar_one()
            )
            conn.execute(
                text(
                    "UPDATE tailoring.tailored_cv "
                    "SET progress = CAST(:p AS jsonb) WHERE id = :id"
                ),
                {"p": '{"attempt": 1}', "id": run_id},
            )
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            value = conn.execute(
                text("SELECT progress FROM tailoring.tailored_cv WHERE id = :id"),
                {"id": run_id},
            ).scalar_one()
        self.assertEqual(value, {"attempt": 1})

    def test_tailor_backend_is_nullable_and_checked(self) -> None:
        run_id = self._insert_run(self.user_a)
        with self.owner.connect() as conn:
            col = conn.execute(
                text(
                    "SELECT data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'tailoring' AND table_name = 'tailored_cv' "
                    "AND column_name = 'tailor_backend'"
                )
            ).one()
            self.assertEqual((col.data_type, col.is_nullable), ("text", "YES"))
            self.assertIsNone(
                conn.execute(
                    text(
                        "SELECT tailor_backend FROM tailoring.tailored_cv "
                        "WHERE id = :id"
                    ),
                    {"id": run_id},
                ).scalar_one()
            )
        for value in ("claude", "native", "docker"):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE tailoring.tailored_cv SET tailor_backend = :v "
                        "WHERE id = :id"
                    ),
                    {"v": value, "id": run_id},
                )
        with self.assertRaises(IntegrityError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE tailoring.tailored_cv SET tailor_backend = 'bogus' "
                        "WHERE id = :id"
                    ),
                    {"id": run_id},
                )

    def test_invalid_run_status_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tailoring.tailored_cv "
                        "(user_id, job_group_id, truth_base_version, target_title, "
                        "status) VALUES (:u, 'zzfixture-job', 1, 'T', 'bogus')"
                    ),
                    {"u": self.user_a},
                )

    def test_cancelled_run_status_is_accepted(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tailoring.tailored_cv "
                    "(user_id, job_group_id, truth_base_version, target_title, "
                    "status) VALUES (:u, 'zzfixture-job', 1, 'T', 'cancelled')"
                ),
                {"u": self.user_a},
            )

    def test_unknown_orphan_kind_is_rejected(self) -> None:
        run_id = self._insert_run(self.user_a)
        with self.assertRaises(IntegrityError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tailoring.orphan_bullet "
                        "(tailored_cv_id, user_id, kind, section, text) "
                        "VALUES (:r, :u, 'bogus', 'summary', 'x')"
                    ),
                    {"r": run_id, "u": self.user_a},
                )

    def test_deleting_a_run_cascades_to_its_orphans(self) -> None:
        run_id = self._insert_run(self.user_a)
        orphan_id = self._insert_orphan(self.user_a, run_id)
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM tailoring.tailored_cv WHERE id = :id"),
                {"id": run_id},
            )
        with self.owner.connect() as conn:
            remaining = conn.execute(
                text("SELECT id FROM tailoring.orphan_bullet WHERE id = :id"),
                {"id": orphan_id},
            ).all()
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
