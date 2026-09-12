from __future__ import annotations

import unittest

from core.classification.classify import classify_title


class _FakeAdapter:
    """Records whether it was called — proves the LLM stage only fires
    when both earlier stages decline."""

    def __init__(self, category: str = "other", confidence: float = 0.5) -> None:
        self.called = False
        self._category = category
        self._confidence = confidence

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ):
        from core.llm.types import LLMResponse

        self.called = True
        return LLMResponse(
            text=(
                f'{{"category": "{self._category}", '
                f'"confidence": {self._confidence}}}'
            ),
            provider="anthropic",
            model=model,
            input_tokens=0,
            output_tokens=0,
        )


class TestClassifyTitle(unittest.TestCase):
    def _qa_map(self) -> dict[str, str | None]:
        return {
            "software_engineer": "software_engineer",
            "data_engineer": "data_engineer",
            "other": None,
        }

    def test_rules_stage_wins_without_touching_embedding_or_llm(self) -> None:
        adapter = _FakeAdapter()
        result = classify_title(
            "Senior Data Engineer",
            centroids={},
            qa_category_map=self._qa_map(),
            adapters={"anthropic": adapter},
            embedding_base_url="unused",
            embedding_model="unused",
            http_client=None,  # type: ignore[arg-type]
        )
        self.assertEqual(result.category, "data_engineer")
        self.assertEqual(result.category_method, "rules")
        self.assertEqual(result.seniority_band, "senior")
        self.assertEqual(result.qa_category, "data_engineer")
        self.assertFalse(adapter.called, "LLM must not be called when rules matched")

    def test_llm_stage_only_fires_when_rules_and_embedding_both_decline(
        self,
    ) -> None:
        adapter = _FakeAdapter(category="software_engineer", confidence=0.6)
        result = classify_title(
            "Chief Vibes Officer",
            centroids={},  # empty centroids -> embedding stage can never match
            qa_category_map=self._qa_map(),
            adapters={"anthropic": adapter},
            embedding_base_url="unused",
            embedding_model="unused",
            http_client=None,  # type: ignore[arg-type]
        )
        self.assertTrue(adapter.called)
        self.assertEqual(result.category, "software_engineer")
        self.assertEqual(result.category_method, "llm")
        self.assertEqual(result.category_confidence, 0.6)

    def test_none_title_classifies_as_other_via_rules_without_calling_llm(
        self,
    ) -> None:
        adapter = _FakeAdapter()
        result = classify_title(
            None,
            centroids={},
            qa_category_map=self._qa_map(),
            adapters={"anthropic": adapter},
            embedding_base_url="unused",
            embedding_model="unused",
            http_client=None,  # type: ignore[arg-type]
        )
        self.assertEqual(result.category, "other")
        self.assertEqual(result.category_method, "rules")
        self.assertEqual(result.seniority_band, "mid")
        self.assertIsNone(result.qa_category)
        self.assertFalse(adapter.called)
