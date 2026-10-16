"""Schema tests for the interview tables (migration 0039)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.db.session import session_scope

_CAT = "zzfixture_category"


class TestInterviewSchema(unittest.TestCase):
    """The question bank is shared; progress and open sentences are per user."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the RLS-subject app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def setUp(self) -> None:
        """Create two fixture users and one question."""
        self.a, self.b = uuid.uuid4(), uuid.uuid4()
        with self.owner.begin() as conn:
            for u in (self.a, self.b):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:i, :e, 'zzfixture')"
                    ),
                    {"i": u, "e": f"zzfixture-{u}@example.com"},
                )
        self.qid = self._question("What is a CTE?", "h1")

    def tearDown(self) -> None:
        """Remove every fixture row."""
        with self.owner.begin() as conn:
            for table in ("personal_answer_open_sentence", "user_question_progress"):
                conn.execute(
                    text(f"DELETE FROM interview.{table} WHERE user_id IN (:a, :b)"),
                    {"a": self.a, "b": self.b},
                )
            conn.execute(
                text("DELETE FROM interview.question_bank WHERE qa_category = :c"),
                {"c": _CAT},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.a, "b": self.b},
            )

    def _question(self, question: str, qhash: str, status: str = "approved"):  # type: ignore[no-untyped-def]
        """Insert one question as the app role.

        Args:
            question: The question text.
            qhash: Its dedupe hash.
            status: The status to store.

        Returns:
            The new row's id.
        """
        with session_scope(self.engine, user_id=self.a) as conn:
            return conn.execute(
                text(
                    "INSERT INTO interview.question_bank (qa_category, topic, kind, "
                    "difficulty, question, question_hash, model_answer, status) "
                    "VALUES (:c, 't', 'technical', 3, :q, :h, 'a', :s) RETURNING id"
                ),
                {"c": _CAT, "q": question, "h": qhash, "s": status},
            ).scalar_one()

    def test_the_bank_is_shared_between_users(self) -> None:
        with session_scope(self.engine, user_id=self.b) as conn:
            seen = conn.execute(
                text("SELECT count(*) FROM interview.question_bank WHERE id = :i"),
                {"i": self.qid},
            ).scalar_one()
        self.assertEqual(seen, 1)

    def test_a_duplicate_question_in_a_topic_is_refused(self) -> None:
        with self.assertRaises(IntegrityError):
            self._question("What is a CTE?", "h1")

    def test_status_kind_and_difficulty_are_enforced(self) -> None:
        with self.assertRaises(IntegrityError):
            self._question("Other?", "h2", status="bogus")
        with self.assertRaises(IntegrityError):
            with session_scope(self.engine, user_id=self.a) as conn:
                conn.execute(
                    text(
                        "INSERT INTO interview.question_bank (qa_category, topic, "
                        "kind, difficulty, question, question_hash, model_answer, "
                        "status) VALUES (:c, 't', 'technical', 9, 'q', 'h3', 'a', "
                        "'approved')"
                    ),
                    {"c": _CAT},
                )

    def test_progress_is_per_user(self) -> None:
        with session_scope(self.engine, user_id=self.a) as conn:
            conn.execute(
                text(
                    "INSERT INTO interview.user_question_progress (user_id, "
                    "question_id, personal_answer, answer_status) VALUES "
                    "(:u, :q, 'mine', 'written')"
                ),
                {"u": self.a, "q": self.qid},
            )
        with session_scope(self.engine, user_id=self.b) as conn:
            seen = conn.execute(
                text("SELECT count(*) FROM interview.user_question_progress")
            ).scalar_one()
        self.assertEqual(seen, 0)

    def test_open_sentences_are_per_user_and_enforce_their_status(self) -> None:
        insert = text(
            "INSERT INTO interview.personal_answer_open_sentence (user_id, "
            "question_id, sentence_id, text, status) VALUES (:u, :q, 's1', 'x', :s)"
        )
        with session_scope(self.engine, user_id=self.a) as conn:
            conn.execute(insert, {"u": self.a, "q": self.qid, "s": "pending"})
        with self.assertRaises(IntegrityError):
            with session_scope(self.engine, user_id=self.a) as conn:
                conn.execute(insert, {"u": self.a, "q": self.qid, "s": "bogus"})
        with session_scope(self.engine, user_id=self.b) as conn:
            seen = conn.execute(
                text("SELECT count(*) FROM interview.personal_answer_open_sentence")
            ).scalar_one()
        self.assertEqual(seen, 0)


if __name__ == "__main__":
    unittest.main()
