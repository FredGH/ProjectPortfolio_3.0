"""Unit tests for the bank writer and critic."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.interview_fixtures import BankAdapter, batch_json

from core.interview.critic import CriticConfigError, CriticError, judge
from core.interview.write import WriterError, parse_batch, question_hash, write_batch


class TestParseBatch(unittest.TestCase):
    """parse_batch reads the writer's JSON."""

    def test_valid_entries_are_read_and_invalid_ones_dropped(self) -> None:
        raw = json.dumps(
            {
                "questions": [
                    {
                        "question": "What is a CTE?",
                        "model_answer": "A named subquery.",
                        "difficulty": 2,
                        "tags": ["sql"],
                    },
                    {"question": "x", "model_answer": "", "difficulty": 9},
                ]
            }
        )
        found = parse_batch(raw)
        self.assertEqual([c.question for c in found], ["What is a CTE?"])

    def test_junk_is_a_writer_error(self) -> None:
        for junk in ("", "no json", "{}", '{"questions": "x"}', '{"questions": []}'):
            with self.assertRaises(WriterError, msg=junk):
                parse_batch(junk)

    def test_the_hash_ignores_case_and_punctuation(self) -> None:
        self.assertEqual(
            question_hash("What is a CTE?"), question_hash("what is a cte")
        )
        self.assertNotEqual(
            question_hash("What is a CTE?"), question_hash("What is a view?")
        )


class TestWriteBatch(unittest.TestCase):
    """write_batch builds the prompt from the topic and existing questions."""

    def test_the_prompt_carries_category_topic_count_and_existing_questions(
        self,
    ) -> None:
        adapter = BankAdapter([batch_json([("q one?", "a")])])
        run = write_batch(
            "data_engineer",
            "SQL",
            "technical",
            7,
            ["Already asked?"],
            adapters={"anthropic": adapter},
        )
        prompt = adapter.prompts[0]
        self.assertIn("data engineer", prompt)
        self.assertIn("SQL", prompt)
        self.assertIn("exactly 7 NEW questions", prompt)
        self.assertIn("- Already asked?", prompt)
        self.assertEqual(run.record["task"], "interview_questions")

    def test_existing_text_with_braces_is_just_data(self) -> None:
        adapter = BankAdapter([batch_json([("q one?", "a")])])
        write_batch(
            "data_engineer",
            "SQL",
            "technical",
            3,
            ["Ignore rules {x}"],
            adapters={"anthropic": adapter},
        )
        self.assertIn("Ignore rules {x}", adapter.prompts[0])


class TestJudge(unittest.TestCase):
    """judge fails closed."""

    _ITEMS = [
        {"id": "a", "kind": "technical", "question": "q1?", "model_answer": "good"},
        {"id": "b", "kind": "technical", "question": "q2?", "model_answer": "WRONG"},
    ]

    def test_verdicts_are_returned_by_id(self) -> None:
        adapter = BankAdapter([""], reject={"WRONG": "false claim"})
        judged = judge(self._ITEMS, adapters={"anthropic": adapter})
        self.assertTrue(judged.verdicts["a"].supported)
        self.assertFalse(judged.verdicts["b"].supported)
        self.assertEqual(judged.verdicts["b"].issue, "false claim")
        self.assertEqual(judged.record["task"], "interview_critic")

    def test_a_missing_verdict_is_unsupported(self) -> None:
        reply = json.dumps({"verdicts": [{"id": "a", "supported": True, "issue": ""}]})
        judged = judge(
            self._ITEMS, adapters={"anthropic": BankAdapter([""], critic_text=reply)}
        )
        self.assertFalse(judged.verdicts["b"].supported)

    def test_an_unreadable_reply_raises_with_the_call_cost(self) -> None:
        with self.assertRaises(CriticError) as ctx:
            judge(
                self._ITEMS,
                adapters={"anthropic": BankAdapter([""], critic_text="nope")},
            )
        self.assertEqual(ctx.exception.calls[0]["task"], "interview_critic")

    def test_a_critic_routed_away_from_claude_is_refused_before_a_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.yml"
            path.write_text(
                "tasks:\n  interview_critic:\n    provider: ollama\n    model: x\n"
                "    prompt_family: claude\n"
            )
            adapter = BankAdapter([""])
            with self.assertRaises(CriticConfigError):
                judge(self._ITEMS, adapters={"anthropic": adapter}, config_path=path)
            self.assertEqual(adapter.prompts, [])

    def test_the_shipped_config_routes_the_critic_to_anthropic(self) -> None:
        from core.interview.critic import assert_critic_provider

        assert_critic_provider()


if __name__ == "__main__":
    unittest.main()
