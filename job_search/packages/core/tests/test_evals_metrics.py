from __future__ import annotations

import unittest

from core.evals.metrics import JudgeResult, exact_match, field_f1, llm_judge
from core.llm.types import LLMResponse


class TestExactMatch(unittest.TestCase):
    def test_identical_dicts_score_one(self) -> None:
        self.assertEqual(
            exact_match({"category": "data_engineer"}, {"category": "data_engineer"}),
            1.0,
        )

    def test_differing_dicts_score_zero(self) -> None:
        self.assertEqual(
            exact_match({"category": "data_engineer"}, {"category": "other"}), 0.0
        )

    def test_extra_predicted_keys_are_ignored(self) -> None:
        # Only the fields present in `expected` are compared — a
        # predictor returning extra metadata fields must not be
        # penalised for it.
        predicted = {"category": "data_engineer", "confidence": 0.9}
        expected = {"category": "data_engineer"}
        self.assertEqual(exact_match(predicted, expected), 1.0)

    def test_missing_expected_key_scores_zero(self) -> None:
        self.assertEqual(exact_match({}, {"category": "data_engineer"}), 0.0)


class TestFieldF1(unittest.TestCase):
    def test_all_fields_match_scores_one(self) -> None:
        predicted = {"skill": "python", "years": 5}
        expected = {"skill": "python", "years": 5}
        self.assertEqual(field_f1(predicted, expected), 1.0)

    def test_no_fields_match_scores_zero(self) -> None:
        predicted = {"skill": "java", "years": 2}
        expected = {"skill": "python", "years": 5}
        self.assertEqual(field_f1(predicted, expected), 0.0)

    def test_partial_match_scores_between_zero_and_one(self) -> None:
        # 1 of 2 fields correct, no extra predicted fields:
        # precision = 1/2, recall = 1/2, f1 = 0.5
        predicted = {"skill": "python", "years": 2}
        expected = {"skill": "python", "years": 5}
        self.assertAlmostEqual(field_f1(predicted, expected), 0.5)

    def test_extra_predicted_fields_reduce_precision(self) -> None:
        # 1 correct field out of 2 predicted, 1 expected field total:
        # precision = 1/2, recall = 1/1, f1 = 2*(0.5*1)/(0.5+1) = 0.667
        predicted = {"skill": "python", "spurious": "x"}
        expected = {"skill": "python"}
        self.assertAlmostEqual(field_f1(predicted, expected), 0.6666666666666666)

    def test_both_empty_scores_one(self) -> None:
        # No fields expected, none predicted — trivially correct rather
        # than a division-by-zero.
        self.assertEqual(field_f1({}, {}), 1.0)


class _FakeJudgeAdapter:
    """Mock LLM adapter for testing judge responses.

    Attributes:
        calls: List of prompts sent to the adapter.
    """

    def __init__(self, response_text: str) -> None:
        """Initialize the fake adapter with a fixed response text.

        Args:
            response_text: The text to return from all calls to complete().
        """
        self._response_text = response_text
        self.calls: list[str] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Return a fake LLM response and record the prompt.

        Args:
            model: The model name (ignored).
            prompt: The prompt sent to the LLM.
            temperature: Temperature parameter (ignored).
            seed: Random seed (ignored).

        Returns:
            A fake LLMResponse with the configured response text.
        """
        self.calls.append(prompt)
        return LLMResponse(
            text=self._response_text,
            provider="anthropic",
            model=model,
            input_tokens=10,
            output_tokens=10,
        )


class TestLlmJudge(unittest.TestCase):
    def test_parses_a_well_formed_judge_response(self) -> None:
        """Verify that llm_judge parses valid JSON responses correctly."""
        adapter = _FakeJudgeAdapter(
            '{"score": 0.8, "rationale": "mostly accurate, minor omission"}'
        )
        result = llm_judge(
            "The candidate has 5 years of Python.",
            "Score 0-1 on factual grounding against the source CV.",
            adapters={"anthropic": adapter},
        )
        self.assertIsInstance(result, JudgeResult)
        self.assertEqual(result.score, 0.8)
        self.assertEqual(result.rationale, "mostly accurate, minor omission")
        self.assertEqual(len(adapter.calls), 1)
        self.assertIn("Score 0-1 on factual grounding", adapter.calls[0])
        self.assertIn("The candidate has 5 years of Python.", adapter.calls[0])

    def test_malformed_response_returns_zero_score_not_a_raised_exception(
        self,
    ) -> None:
        """Verify that malformed JSON responses return zero score without raising."""
        adapter = _FakeJudgeAdapter("not json at all")
        result = llm_judge("output", "rubric", adapters={"anthropic": adapter})
        self.assertEqual(result.score, 0.0)
        self.assertIn("could not parse", result.rationale.lower())


if __name__ == "__main__":
    unittest.main()
