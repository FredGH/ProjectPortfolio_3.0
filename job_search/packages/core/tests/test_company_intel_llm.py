"""Unit tests for the company-intel LLM task runner."""

from __future__ import annotations

import unittest

from core.company_intel.llm import LlmTaskError, run_task, total_usage
from core.llm.types import LLMResponse


class _Echo:
    """An adapter echoing a fixed reply."""

    def __init__(self, text: str, truncated: bool = False) -> None:
        """Hold the reply.

        Args:
            text: The reply text.
            truncated: Whether to flag it as cut off.
        """
        self.text, self.truncated, self.prompt = text, truncated, ""

    def complete(self, *, model: str, prompt: str, **_kwargs: object) -> LLMResponse:
        """Return the fixed reply.

        Args:
            model: The model asked for.
            prompt: The prompt (kept).
            **_kwargs: Ignored.

        Returns:
            The response.
        """
        self.prompt = prompt
        return LLMResponse(
            self.text, "anthropic", model, 1000, 500, truncated=self.truncated
        )


class TestRunTask(unittest.TestCase):
    """run_task loads the prompt, calls the task's provider and records usage."""

    def test_it_returns_text_prompt_version_and_a_priced_record(self) -> None:
        adapter = _Echo(" {} ")
        result = run_task(
            "company_resolution",
            1,
            {
                "title": "T",
                "company": "C",
                "location": "L",
                "urls": "u",
                "description": "D {with} braces",
            },
            {"anthropic": adapter},
        )
        self.assertEqual(result.text, "{}")
        self.assertEqual(result.prompt_version, "claude.v1")
        self.assertIn("D {with} braces", adapter.prompt)
        self.assertEqual(result.record["task"], "company_resolution")
        self.assertEqual(result.record["input_tokens"], 1000)
        self.assertAlmostEqual(result.record["cost_usd"], 0.007)

    def test_a_cut_off_reply_is_an_error(self) -> None:
        with self.assertRaises(LlmTaskError):
            run_task(
                "company_resolution",
                1,
                {
                    "title": "T",
                    "company": "C",
                    "location": "L",
                    "urls": "u",
                    "description": "D",
                },
                {"anthropic": _Echo("{", truncated=True)},
            )


class TestTotalUsage(unittest.TestCase):
    """total_usage sums calls and keeps an unpriced total honest."""

    def test_it_sums_tokens_and_cost(self) -> None:
        calls = [
            {"task": "a", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.5},
            {"task": "b", "input_tokens": 20, "output_tokens": 1, "cost_usd": 0.25},
        ]
        total = total_usage(calls)
        self.assertEqual(total["input_tokens"], 30)
        self.assertEqual(total["output_tokens"], 6)
        self.assertAlmostEqual(total["cost_usd"], 0.75)
        self.assertEqual(total["calls"], calls)

    def test_an_unpriced_call_makes_the_total_cost_unknown(self) -> None:
        total = total_usage(
            [{"task": "a", "input_tokens": 1, "output_tokens": 1, "cost_usd": None}]
        )
        self.assertIsNone(total["cost_usd"])


if __name__ == "__main__":
    unittest.main()
