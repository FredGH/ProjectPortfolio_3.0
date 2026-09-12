"""Unit tests for `core.classification.llm_classifier.classify_by_llm`.

Unit-level, fake adapter only — distinct from
`tests/integration/test_classification_llm.py`, which hits the real
Anthropic API when a key is configured.
"""

from __future__ import annotations

import unittest

from core.classification.llm_classifier import classify_by_llm
from core.llm.types import LLMResponse


class _FakeAdapter:
    """A fake `LLMAdapter` that returns a fixed response and records calls.

    Attributes:
        calls: `(model, prompt)` pairs passed to every `complete` call.
    """

    def __init__(self, response_text: str) -> None:
        """Initialise the fake adapter.

        Args:
            response_text: The text every `complete` call will return.
        """
        self._response_text = response_text
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Record the call and return the fixed fake response.

        Args:
            model: The provider-specific model identifier.
            prompt: The prompt text.
            temperature: Sampling temperature (unused by the fake).
            seed: A fixed seed (unused by the fake).

        Returns:
            The fixed `LLMResponse` configured at construction time.
        """
        self.calls.append((model, prompt))
        return LLMResponse(
            text=self._response_text,
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestClassifyByLlm(unittest.TestCase):
    """Unit tests for `classify_by_llm`'s 4-tuple return and overrides."""

    def test_returns_prompt_version_and_model_id_alongside_category(self) -> None:
        """A well-formed response yields category, confidence, prompt_version,
        and model_id together.

        Returns:
            None.
        """
        adapter = _FakeAdapter('{"category": "data_engineer", "confidence": 0.7}')
        category, confidence, prompt_version, model_id = classify_by_llm(
            "Some Title", adapters={"anthropic": adapter}
        )
        self.assertEqual(category, "data_engineer")
        self.assertEqual(confidence, 0.7)
        self.assertEqual(prompt_version, "claude.v1")
        self.assertEqual(model_id, "claude-sonnet-5")

    def test_provider_and_model_override_route_to_the_overridden_adapter(
        self,
    ) -> None:
        """`provider`/`model` overrides route the call to the named adapter
        and its model, leaving the default adapter untouched.

        Returns:
            None.
        """
        overridden_adapter = _FakeAdapter(
            '{"category": "software_engineer", "confidence": 0.6}'
        )
        default_adapter = _FakeAdapter('{"category": "other", "confidence": 0.1}')
        category, _, prompt_version, model_id = classify_by_llm(
            "Some Title",
            adapters={"anthropic": default_adapter, "ollama": overridden_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="claude",
        )
        self.assertEqual(category, "software_engineer")
        self.assertEqual(model_id, "llama3.1:8b")
        self.assertEqual(prompt_version, "claude.v1")
        self.assertEqual(default_adapter.calls, [])
        self.assertEqual(len(overridden_adapter.calls), 1)

    def test_malformed_response_falls_back_to_other_with_no_prompt_version(
        self,
    ) -> None:
        """A malformed response falls back to `("other", 0.0, None, None)`
        rather than propagating a parse error.

        Returns:
            None.
        """
        adapter = _FakeAdapter("not json")
        category, confidence, prompt_version, model_id = classify_by_llm(
            "Some Title", adapters={"anthropic": adapter}
        )
        self.assertEqual(category, "other")
        self.assertEqual(confidence, 0.0)
        self.assertIsNone(prompt_version)
        self.assertIsNone(model_id)


if __name__ == "__main__":
    unittest.main()
