from __future__ import annotations

import unittest

import anthropic

from core.classification.llm_classifier import classify_by_llm
from core.llm.adapters.anthropic import AnthropicAdapter
from core.settings import get_settings

_settings = get_settings()


@unittest.skipUnless(_settings.anthropic_api_key, "ANTHROPIC_API_KEY not configured")
class TestClassifyByLlm(unittest.TestCase):
    def setUp(self) -> None:
        self.adapters = {
            "anthropic": AnthropicAdapter(
                api_key=_settings.anthropic_api_key,
                client=anthropic.Anthropic(api_key=_settings.anthropic_api_key),
            ),
        }

    def test_classifies_an_unambiguous_residual_title(self) -> None:
        """A title outside all 6 substantive categories resolves to
        "other" via the real Anthropic API, with a prompt_version and
        model_id stamped on the result.

        Returns:
            None.
        """
        # A title deliberately outside all 6 substantive categories —
        # the exact "declined by rules and embedding" case this stage
        # exists for.
        category, confidence, prompt_version, model_id = classify_by_llm(
            "Chief Ethics Officer", adapters=self.adapters
        )
        self.assertEqual(category, "other")
        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)
        self.assertEqual(prompt_version, "claude.v1")
        self.assertIsNotNone(model_id)

    def test_malformed_response_falls_back_to_other_rather_than_raising(self) -> None:
        """A malformed response falls back to `("other", 0.0, None,
        None)` rather than raising, with no prompt_version/model_id
        since nothing was successfully classified.

        Returns:
            None.
        """

        # Can't force a real malformed response from the live API
        # deterministically, so this test exercises the parsing
        # fallback directly against a fake adapter instead of the real
        # one — still an integration-shaped test of the parsing logic,
        # not a mock of the classification decision itself.
        class _BrokenAdapter:
            def complete(
                self,
                *,
                model: str,
                prompt: str,
                temperature: float = 0.0,
                seed: int | None = None,
            ):
                from core.llm.types import LLMResponse

                return LLMResponse(
                    text="not json at all",
                    provider="anthropic",
                    model=model,
                    input_tokens=0,
                    output_tokens=0,
                )

        category, confidence, prompt_version, model_id = classify_by_llm(
            "Some Title", adapters={"anthropic": _BrokenAdapter()}
        )
        self.assertEqual(category, "other")
        self.assertEqual(confidence, 0.0)
        self.assertIsNone(prompt_version)
        self.assertIsNone(model_id)
