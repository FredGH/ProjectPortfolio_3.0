"""DB-backed tests for generating the shared question bank."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.interview_fixtures import BankAdapter, batch_json

from core.interview import service, store
from core.interview.config import BEHAVIOURAL_TOPIC, TopicsConfig, WorkItem
from core.interview.critic import CriticConfigError

_CAT = "zzfixture_category"
_CONFIG = TopicsConfig(3, 2, {_CAT: ["Topic A"]})


def _qs(prefix: str, n: int, answer: str = "A correct answer.") -> str:
    """Build a writer reply of `n` distinct questions.

    Args:
        prefix: A prefix keeping questions distinct across calls.
        n: How many questions.
        answer: The model answer of each.

    Returns:
        A writer-reply JSON string.
    """
    return batch_json([(f"{prefix} question number {i}?", answer) for i in range(n)])


class TestGenerateBank(unittest.TestCase):
    """The bank generates, is checked, and never repeats finished work."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove the fixture questions."""
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM interview.question_bank WHERE qa_category = :c"),
                {"c": _CAT},
            )

    def _work(self, **kwargs: object) -> list[WorkItem]:
        """Plan the fixture category's work.

        Args:
            **kwargs: Extra `pending_work` arguments.

        Returns:
            The work items.
        """
        work, _ = service.pending_work(
            self.engine, config=_CONFIG, categories=[_CAT], **kwargs  # type: ignore[arg-type]
        )
        return work

    def test_a_topic_generates_its_target_of_approved_questions(self) -> None:
        adapter = BankAdapter([_qs("a", 3)])
        results = service.generate_work(
            self.engine, self._work(topic="Topic A"), adapters={"anthropic": adapter}
        )
        self.assertEqual((results[0].approved, results[0].needs_review), (3, 0))
        self.assertEqual(store.approved_counts(self.engine)[(_CAT, "Topic A")], 3)

    def test_a_second_run_makes_no_llm_calls(self) -> None:
        adapter = BankAdapter([_qs("a", 3), _qs("b", 2)])
        service.generate_work(
            self.engine, self._work(), adapters={"anthropic": adapter}
        )
        self.assertEqual(self._work(), [])
        before = len(adapter.prompts)
        service.generate_work(
            self.engine, self._work(), adapters={"anthropic": adapter}
        )
        self.assertEqual(len(adapter.prompts), before)

    def test_a_wrong_answer_is_stored_for_review_never_approved(self) -> None:
        writer = batch_json(
            [
                ("good one?", "Right."),
                ("bad one?", "WRONG: SQL is a procedural language."),
            ]
        )
        adapter = BankAdapter(
            [writer, _qs("fill", 2)], reject={"WRONG": "SQL is declarative"}
        )
        service.generate_work(
            self.engine, self._work(topic="Topic A"), adapters={"anthropic": adapter}
        )
        review = store.list_questions(self.engine, _CAT, status="needs_review")
        self.assertEqual([q.question for q in review], ["bad one?"])
        self.assertIn("declarative", review[0].critic_issue or "")
        approved = [q.question for q in store.list_questions(self.engine, _CAT)]
        self.assertNotIn("bad one?", approved)
        self.assertEqual(len(approved), 3)

    def test_an_unusable_critic_reply_approves_nothing(self) -> None:
        adapter = BankAdapter([_qs("a", 3)], critic_text="not json")
        results = service.generate_work(
            self.engine, self._work(topic="Topic A"), adapters={"anthropic": adapter}
        )
        self.assertIn("unusable", results[0].error or "")
        self.assertEqual(store.list_questions(self.engine, _CAT), [])
        self.assertEqual(
            store.list_questions(self.engine, _CAT, status="needs_review"), []
        )

    def test_repeated_questions_are_not_stored_twice(self) -> None:
        same = batch_json([("same question?", "A"), ("Same question", "A")])
        adapter = BankAdapter([same, _qs("x", 3)])
        service.generate_work(
            self.engine, self._work(topic="Topic A"), adapters={"anthropic": adapter}
        )
        texts = [
            q.question.lower().rstrip("?")
            for q in store.list_questions(self.engine, _CAT)
        ]
        self.assertEqual(len(texts), len(set(texts)))

    def test_behavioural_questions_are_stored_under_their_own_topic(self) -> None:
        adapter = BankAdapter([_qs("b", 2)])
        service.generate_work(
            self.engine,
            self._work(topic=BEHAVIOURAL_TOPIC),
            adapters={"anthropic": adapter},
        )
        kinds = {(q.topic, q.kind) for q in store.list_questions(self.engine, _CAT)}
        self.assertEqual(kinds, {(BEHAVIOURAL_TOPIC, "behavioural")})

    def test_a_critic_routed_away_from_claude_is_refused_before_any_spend(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.yml"
            path.write_text(
                "tasks:\n  interview_critic:\n    provider: ollama\n    model: x\n"
                "    prompt_family: claude\n"
            )
            adapter = BankAdapter([_qs("a", 3)])
            with self.assertRaises(CriticConfigError):
                service.generate_work(
                    self.engine,
                    self._work(),
                    adapters={"anthropic": adapter},
                    config_path=path,
                )
            self.assertEqual(adapter.prompts, [])

    def test_the_cost_estimate_counts_batches(self) -> None:
        item = WorkItem(_CAT, "t", "technical", 25)
        self.assertEqual(
            service.estimate_cost([item]), round(3 * service.COST_PER_BATCH_USD, 2)
        )

    def test_forcing_a_topic_rejects_the_old_questions_without_deleting(self) -> None:
        adapter = BankAdapter([_qs("a", 3)])
        service.generate_work(
            self.engine, self._work(topic="Topic A"), adapters={"anthropic": adapter}
        )
        self.assertEqual(store.reject_topic(self.engine, _CAT, "Topic A"), 3)
        self.assertEqual(len(self._work(topic="Topic A")), 1)
        self.assertEqual(
            len(store.list_questions(self.engine, _CAT, status="rejected")), 3
        )


if __name__ == "__main__":
    unittest.main()
