from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.ingestion.extraction import (
    ExtractedJobFields,
    apply_user_overrides,
    extract_job_fields,
)
from core.llm.types import LLMResponse

_SAMPLE_YAML = """
tasks:
  manual_entry_parse:
    provider: fake
    model: fake-model-v1
    prompt_family: local
"""


class _FakeAdapter:
    """A test double standing in for a real LLM adapter."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Return the pre-baked response, ignoring the prompt content."""
        return LLMResponse(
            text=self._response_text,
            provider="fake",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class _RaisingAdapter:
    """A test double that always fails, to prove callers degrade gracefully."""

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Always raise, simulating an unreachable LLM provider."""
        raise RuntimeError("provider unreachable")


class TestExtractJobFields(unittest.TestCase):
    """Tests for extract_job_fields's structured-output parsing."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        tmp.write(_SAMPLE_YAML)
        tmp.close()
        self.config_path = Path(tmp.name)

    def tearDown(self) -> None:
        self.config_path.unlink(missing_ok=True)

    def test_parses_a_well_formed_structured_response(self) -> None:
        """A valid JSON completion parses into ExtractedJobFields."""
        payload = json.dumps(
            {
                "title": "Senior Data Engineer",
                "company": "Acme Ltd",
                "location": "London, UK",
                "contract": "permanent",
                "salary": "£90,000",
                "seniority": "senior",
            }
        )
        adapters = {"fake": _FakeAdapter(payload)}
        result = extract_job_fields(
            "some raw JD text", adapters=adapters, config_path=self.config_path
        )
        self.assertEqual(result.title, "Senior Data Engineer")
        self.assertEqual(result.company, "Acme Ltd")

    def test_partial_response_leaves_missing_fields_none(self) -> None:
        """Fields the model omits default to None rather than erroring."""
        payload = json.dumps({"title": "Data Engineer"})
        adapters = {"fake": _FakeAdapter(payload)}
        result = extract_job_fields(
            "some raw JD text", adapters=adapters, config_path=self.config_path
        )
        self.assertEqual(result.title, "Data Engineer")
        self.assertIsNone(result.company)


class TestApplyUserOverrides(unittest.TestCase):
    """Tests for apply_user_overrides's merge and field-source tagging."""

    def test_user_value_wins_and_is_tagged(self) -> None:
        """An override present in the user's input replaces the parsed value."""
        extracted = ExtractedJobFields(title="Data Engineer", company="Parsed Co")
        merged, field_source = apply_user_overrides(
            extracted, {"company": "User Co", "title": None, "location": None}
        )
        self.assertEqual(merged.company, "User Co")
        self.assertEqual(merged.title, "Data Engineer")
        self.assertEqual(field_source, {"company": "user"})

    def test_no_overrides_leaves_extraction_untouched_and_field_source_empty(
        self,
    ) -> None:
        """With nothing overridden, the parsed values and empty map survive."""
        extracted = ExtractedJobFields(title="Data Engineer")
        merged, field_source = apply_user_overrides(
            extracted, {"company": None, "title": None, "location": None}
        )
        self.assertEqual(merged.title, "Data Engineer")
        self.assertEqual(field_source, {})


class TestExtractionResilience(unittest.TestCase):
    """Tests proving a broken LLM call is the caller's problem, not silent
    corruption — extract_job_fields itself still raises; graceful
    degradation is Task 6's orchestration's job, tested there."""

    def test_adapter_failure_propagates(self) -> None:
        """A raising adapter's exception is not swallowed at this layer."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml") as tmp:
            tmp.write(_SAMPLE_YAML)
            tmp.flush()
            adapters = {"fake": _RaisingAdapter()}
            with self.assertRaises(RuntimeError):
                extract_job_fields(
                    "text", adapters=adapters, config_path=Path(tmp.name)
                )


if __name__ == "__main__":
    unittest.main()
