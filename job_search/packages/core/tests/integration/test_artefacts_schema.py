"""Schema tests for the artefacts tables (migration 0038)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.db.session import session_scope

_JOB = "zzfixture-artefacts-job"


class TestArtefactsSchema(unittest.TestCase):
    """The artefacts tables exist, are per-user and enforce their enums."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the RLS-subject app role."""
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        """Create two fixture users."""
        self.user_a, self.user_b = uuid.uuid4(), uuid.uuid4()
        with self.owner.begin() as conn:
            for user_id in (self.user_a, self.user_b):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'zzfixture artefacts user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )

    def tearDown(self) -> None:
        """Remove every fixture row."""
        with self.owner.begin() as conn:
            for table in (
                "artefacts.artefact_open_sentence",
                "artefacts.artefact_cover_letter",
                "artefacts.artefact_pitch",
            ):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE user_id IN (:a, :b)"),
                    {"a": self.user_a, "b": self.user_b},
                )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )

    def _insert(self, table: str, user_id: uuid.UUID, status: str = "approved"):
        """Insert one artefact row as a user.

        Args:
            table: `artefact_cover_letter` or `artefact_pitch`.
            user_id: The owner.
            status: The status to store.

        Returns:
            The new row's id.
        """
        with session_scope(self.app_engine, user_id=user_id) as conn:
            return conn.execute(
                text(
                    f"INSERT INTO artefacts.{table} (user_id, job_group_id, "
                    "truth_base_version, target_title, status) "
                    "VALUES (:u, :j, 1, 'Zz Title', :s) RETURNING id"
                ),
                {"u": user_id, "j": _JOB, "s": status},
            ).scalar_one()

    def test_a_user_only_sees_their_own_artefacts(self) -> None:
        for table in ("artefact_cover_letter", "artefact_pitch"):
            self._insert(table, self.user_a)
            with session_scope(self.app_engine, user_id=self.user_b) as conn:
                seen = conn.execute(
                    text(
                        f"SELECT count(*) FROM artefacts.{table} "
                        "WHERE job_group_id = :j"
                    ),
                    {"j": _JOB},
                ).scalar_one()
            self.assertEqual(seen, 0, table)

    def test_status_must_be_a_known_value(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert("artefact_cover_letter", self.user_a, status="bogus")

    def test_open_sentences_are_per_user_and_enforce_their_enums(self) -> None:
        letter_id = self._insert("artefact_cover_letter", self.user_a, "needs_review")
        insert = text(
            "INSERT INTO artefacts.artefact_open_sentence (user_id, artefact_kind, "
            "artefact_id, sentence_id, location, kind, text) "
            "VALUES (:u, :k, :a, 's1', 'evidence:1', :kind, 'x') RETURNING id"
        )
        ok = {"u": self.user_a, "k": "cover_letter", "a": letter_id, "kind": "orphan"}
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            conn.execute(insert, ok)
        for bad in ({"kind": "bogus"}, {"k": "bogus"}):
            with self.assertRaises(IntegrityError):
                with session_scope(self.app_engine, user_id=self.user_a) as conn:
                    conn.execute(insert, {**ok, **bad})
        with session_scope(self.app_engine, user_id=self.user_b) as conn:
            seen = conn.execute(
                text("SELECT count(*) FROM artefacts.artefact_open_sentence")
            ).scalar_one()
        self.assertEqual(seen, 0)

    def test_an_open_sentence_can_be_decided(self) -> None:
        letter_id = self._insert("artefact_cover_letter", self.user_a, "needs_review")
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            sentence_id = conn.execute(
                text(
                    "INSERT INTO artefacts.artefact_open_sentence (user_id, "
                    "artefact_kind, artefact_id, sentence_id, location, kind, text) "
                    "VALUES (:u, 'cover_letter', :a, 's1', 'evidence:1', 'orphan', "
                    "'x') RETURNING id"
                ),
                {"u": self.user_a, "a": letter_id},
            ).scalar_one()
            conn.execute(
                text(
                    "UPDATE artefacts.artefact_open_sentence SET status = 'linked', "
                    "evidence_ref = 'cv:abc', decided_at = now() WHERE id = :i"
                ),
                {"i": sentence_id},
            )


if __name__ == "__main__":
    unittest.main()
