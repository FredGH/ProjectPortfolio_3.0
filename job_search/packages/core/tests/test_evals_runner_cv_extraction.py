"""cv_extraction's golden set loads and the predictor round-trips it.

Unit-level, fake adapter only — mirrors test_llm_classifier.py's
`_FakeAdapter` pattern rather than mocking.
"""

from __future__ import annotations

import json
import unittest

from core.evals.golden import load_golden_set
from core.evals.runner import _PREDICTORS
from core.llm.types import LLMResponse


class _FakeAdapter:
    """A fake `LLMAdapter` that returns a fixed response.

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
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestCvExtractionGoldenSet(unittest.TestCase):
    def test_golden_set_has_at_least_the_minimum_case_count(self) -> None:
        from core.evals.runner import MINIMUM_GOLDEN_SET_SIZE

        cases = load_golden_set("cv_extraction")
        self.assertGreaterEqual(len(cases), MINIMUM_GOLDEN_SET_SIZE)

    def test_cv_extraction_predictor_is_registered(self) -> None:
        self.assertIn("cv_extraction", _PREDICTORS)

    def test_predictor_flattens_extraction_result_to_expected_keys(self) -> None:
        cases = load_golden_set("cv_extraction")
        case = cases[0]
        payload = {
            "identity": "Test Person",
            "headline": "Engineer",
            "locations": [],
            "work_auth": None,
            "skills": [{"name": case.expected["first_skill"]}],
            "experience": [
                {
                    "company": case.expected["company"],
                    "title": case.expected["title"],
                    "start": case.expected["start"],
                    "end": case.expected["end"],
                    "bullets": [case.expected["first_bullet"]],
                    "tech": [],
                    "metrics": [],
                }
            ],
            "education": [],
            "certifications": [],
            "publications": [],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        predictor = _PREDICTORS["cv_extraction"]
        predicted, prompt_version = predictor(
            case,
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
            adapters={"ollama": fake_adapter},
        )
        self.assertEqual(predicted, case.expected)
        self.assertIsNotNone(prompt_version)


if __name__ == "__main__":
    unittest.main()
