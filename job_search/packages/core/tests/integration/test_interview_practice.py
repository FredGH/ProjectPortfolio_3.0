"""DB-backed tests for per-user interview practice (real Postgres)."""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from tests.artefacts_fixtures import ScriptedAdapter, cv_ref, sentence_json
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.interview_fixtures import batch_json
from tests.tailoring_fixtures import make_truth_base

from core.cv.store import write_truth_base
from core.interview import practice, progress, store
from core.interview.config import BEHAVIOURAL_TOPIC
from core.interview.store import NewQuestion
from core.interview.write import question_hash

_CAT = "zzfixture_category"
_NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def _new(
    question: str,
    kind: str = "technical",
    tags: list[str] | None = None,
    difficulty: int = 3,
) -> NewQuestion:
    """Build a question to insert.

    Args:
        question: The question text.
        kind: `technical` or `behavioural`.
        tags: Its tags.
        difficulty: 1-5.

    Returns:
        A `NewQuestion` in the fixture category.
    """
    return NewQuestion(
        _CAT,
        BEHAVIOURAL_TOPIC if kind == "behavioural" else "T",
        kind,
        difficulty,
        question,
        question_hash(question),
        "Model answer.",
        tags or [],
        "approved",
        None,
        "m",
        "claude.v1",
        "m",
        "claude.v1",
    )


def _reply(*sentences: dict) -> str:
    """Build a personal-answer writer reply.

    Args:
        *sentences: Sentence dicts.

    Returns:
        A JSON string.
    """
    return json.dumps({"sentences": list(sentences)})


class TestPractice(unittest.TestCase):
    """Queue, SM-2, gaps, answers and isolation, against the real database."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def setUp(self) -> None:
        """Create two users (one with a CV) and four questions."""
        self.user, self.other = uuid.uuid4(), uuid.uuid4()
        with self.owner.begin() as conn:
            for u in (self.user, self.other):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:i, :e, 'zzfixture')"
                    ),
                    {"i": u, "e": f"zzfixture-{u}@example.com"},
                )
        write_truth_base(self.engine, self.user, "md", make_truth_base())
        store.insert_questions(
            self.engine,
            [
                _new("Explain dbt models?", tags=["dbt"]),
                _new("Explain Rust lifetimes?", tags=["Rust"]),
                _new("Explain Airflow DAGs?", tags=["Airflow"]),
                _new("Tell me about a conflict.", kind="behavioural"),
            ],
        )
        self.q = {q.question: q for q in store.list_questions(self.engine, _CAT)}

    def tearDown(self) -> None:
        """Remove every fixture row."""
        with self.owner.begin() as conn:
            for table in ("personal_answer_open_sentence", "user_question_progress"):
                conn.execute(
                    text(f"DELETE FROM interview.{table} WHERE user_id IN (:a, :b)"),
                    {"a": self.user, "b": self.other},
                )
            conn.execute(
                text("DELETE FROM interview.question_bank WHERE qa_category = :c"),
                {"c": _CAT},
            )
            for table in ("cv_truth_base_history", "cv_truth_base"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE user_id IN (:a, :b)"),
                    {"a": self.user, "b": self.other},
                )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user, "b": self.other},
            )

    def _id(self, question: str) -> uuid.UUID:
        """Look up a fixture question id.

        Args:
            question: The question text.

        Returns:
            Its id.
        """
        return self.q[question].id

    def test_the_queue_puts_the_biggest_cv_gap_first_then_behavioural(self) -> None:
        practice.refresh_gaps(self.engine, self.user, _CAT)
        order = [
            q.question
            for q in practice.build_queue(self.engine, self.user, _CAT, now=_NOW)
        ]
        self.assertEqual(order[0], "Explain Rust lifetimes?")
        self.assertEqual(order[-1], "Tell me about a conflict.")
        self.assertLess(
            order.index("Explain dbt models?"), order.index("Tell me about a conflict.")
        )

    def test_a_reviewed_question_leaves_the_queue_until_it_is_due(self) -> None:
        qid = self._id("Explain dbt models?")
        state = practice.review_question(self.engine, self.user, qid, 5, _NOW)
        self.assertEqual(state.next_review, _NOW + timedelta(days=1))
        names = lambda now: [  # noqa: E731
            q.question
            for q in practice.build_queue(self.engine, self.user, _CAT, now=now)
        ]
        self.assertNotIn("Explain dbt models?", names(_NOW))
        self.assertEqual(names(_NOW + timedelta(days=2))[0], "Explain dbt models?")

    def test_gaps_are_recomputed_only_when_the_cv_version_changes(self) -> None:
        self.assertEqual(practice.refresh_gaps(self.engine, self.user, _CAT), 3)
        self.assertEqual(practice.refresh_gaps(self.engine, self.user, _CAT), 0)
        write_truth_base(self.engine, self.user, "md", make_truth_base())
        self.assertEqual(practice.refresh_gaps(self.engine, self.user, _CAT), 3)

    def test_another_user_sees_none_of_this_users_progress(self) -> None:
        qid = self._id("Explain dbt models?")
        practice.review_question(self.engine, self.user, qid, 4, _NOW)
        progress.save_written_answer(self.engine, self.user, qid, "mine")
        self.assertEqual(progress.list_progress(self.engine, self.other), {})
        self.assertEqual(len(store.list_questions(self.engine, _CAT)), 4)

    def test_a_written_answer_is_not_replaced_without_confirmation(self) -> None:
        qid = self._id("Explain dbt models?")
        progress.save_written_answer(self.engine, self.user, qid, "My own words.")
        adapter = ScriptedAdapter([_reply(sentence_json("x", "voice"))])
        with self.assertRaises(practice.AnswerExistsError):
            practice.draft_personal_answer(
                self.engine, self.user, qid, adapters={"anthropic": adapter}
            )
        self.assertEqual(adapter.prompts, [])
        self.assertEqual(
            progress.get_progress(self.engine, self.user, qid).personal_answer,
            "My own words.",
        )

    def _draft_with_pending(self, qid: uuid.UUID):  # type: ignore[no-untyped-def]
        """Draft an answer containing one invented-number sentence.

        Args:
            qid: The question id.

        Returns:
            The `AnswerDraft`.
        """
        bad = _reply(
            sentence_json("Here is the story.", "voice"),
            sentence_json("I led 40 engineers.", sources=[cv_ref(0, 0)]),
            sentence_json("It went well.", "voice"),
        )
        return practice.draft_personal_answer(
            self.engine, self.user, qid, adapters={"anthropic": ScriptedAdapter([bad])}
        )

    def test_a_draft_with_an_invented_claim_waits_for_a_decision_and_remove_cuts_it(
        self,
    ) -> None:
        qid = self._id("Tell me about a conflict.")
        draft = self._draft_with_pending(qid)
        self.assertEqual(draft.status, "needs_review")
        mine = progress.get_progress(self.engine, self.user, qid)
        self.assertEqual(mine.answer_status, "needs_review")
        (pending,) = progress.list_open(self.engine, self.user, qid)
        status = practice.decide_sentence(
            self.engine, self.user, pending.id, "remove", None
        )
        self.assertEqual(status, "drafted")
        after = progress.get_progress(self.engine, self.user, qid)
        self.assertNotIn("40 engineers", after.personal_answer or "")
        self.assertEqual(after.answer_status, "drafted")

    def test_linking_needs_a_cv_source_and_a_decision_cannot_be_made_twice(
        self,
    ) -> None:
        qid = self._id("Tell me about a conflict.")
        self._draft_with_pending(qid)
        (pending,) = progress.list_open(self.engine, self.user, qid)
        with self.assertRaises(ValueError):
            practice.decide_sentence(
                self.engine, self.user, pending.id, "link", "cv:made-up"
            )
        practice.decide_sentence(
            self.engine, self.user, pending.id, "link", cv_ref(0, 1)
        )
        with self.assertRaises(progress.StaleDecisionError):
            practice.decide_sentence(self.engine, self.user, pending.id, "remove", None)

    def test_another_user_cannot_decide_this_users_sentence(self) -> None:
        qid = self._id("Tell me about a conflict.")
        self._draft_with_pending(qid)
        (pending,) = progress.list_open(self.engine, self.user, qid)
        with self.assertRaises(progress.StaleDecisionError):
            practice.decide_sentence(
                self.engine, self.other, pending.id, "remove", None
            )

    def test_a_failed_draft_stores_nothing(self) -> None:
        from tests.company_intel_fixtures import FakeAdapter

        qid = self._id("Explain dbt models?")
        draft = practice.draft_personal_answer(
            self.engine, self.user, qid, adapters={"anthropic": FakeAdapter(["no"])}
        )
        self.assertEqual(draft.status, "failed")
        self.assertIsNone(progress.get_progress(self.engine, self.user, qid))

    def test_a_user_without_a_cv_cannot_draft_or_score(self) -> None:
        from core.artefacts.service import NoCvError

        with self.assertRaises(NoCvError):
            practice.refresh_gaps(self.engine, self.other, _CAT)
        with self.assertRaises(NoCvError):
            practice.draft_personal_answer(
                self.engine,
                self.other,
                self._id("Explain dbt models?"),
                adapters={"anthropic": ScriptedAdapter([batch_json([])])},
            )


if __name__ == "__main__":
    unittest.main()
