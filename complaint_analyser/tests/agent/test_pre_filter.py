from __future__ import annotations

import unittest

from agentic_triage.agent.pre_filter import apply_auto_p4, is_auto_p4
from agentic_triage.core.config import (
    CollectionConfig,
    DomainConfig,
    PriorityLevel,
    ScoringDimension,
)


def _make_config() -> DomainConfig:
    return DomainConfig(
        domain_name="test",
        input_field="text",
        id_prefix="T-",
        scoring_dimensions=[
            ScoringDimension(
                name="fraud_risk",
                description="Fraud risk",
                min_score=0,
                max_score=5,
                weight=1.0,
            ),
            ScoringDimension(
                name="reg_breach",
                description="Regulatory breach",
                min_score=0,
                max_score=5,
                weight=1.0,
            ),
        ],
        priority_levels=[
            PriorityLevel(
                label="P1",
                min_composite=4.0,
                description="Critical",
                response_sla="4h",
                recommended_action="escalate",
            ),
            PriorityLevel(
                label="P4",
                min_composite=0.0,
                description="Low",
                response_sla="5d",
                recommended_action="batch",
            ),
        ],
        collections=[CollectionConfig(name="kb", role="rules")],
    )


def _make_state(**overrides) -> dict:
    base: dict = {
        "input_id": "T-001",
        "batch_id": "B-001",
        "raw_text": "some complaint text",
        "sanitized_text": "some complaint text",
        "cleaned_text": "some complaint text",
        "entities": {},
        "triggered_keywords": [],
        "retrieved_context": {},
        "retrieval_scores": {"kb": 0.2},
        "dimension_scores": {},
        "precedent_scores": {},
        "composite_score": 0.0,
        "is_auto_p4": False,
        "priority": "",
        "confidence": 0.0,
        "low_confidence_reason": None,
        "loop_count": 0,
        "reasoning": "",
        "recommended_action": "",
        "analyst_override": None,
        "hyde_text": None,
        "retrieval_queries": [],
        "retrieved_references": {},
    }
    base.update(overrides)
    return base


class TestIsAutoP4(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _make_config()

    def test_returns_true_when_no_signal(self) -> None:
        state = _make_state()
        self.assertTrue(is_auto_p4(state, self.config))

    def test_returns_false_when_keywords_present(self) -> None:
        state = _make_state(triggered_keywords=["fraud alert"])
        self.assertFalse(is_auto_p4(state, self.config))

    def test_returns_false_when_multiple_keywords(self) -> None:
        state = _make_state(triggered_keywords=["fraud", "unauthorised"])
        self.assertFalse(is_auto_p4(state, self.config))

    def test_returns_false_when_money_entity_with_values(self) -> None:
        state = _make_state(entities={"MONEY": ["£5,000"]})
        self.assertFalse(is_auto_p4(state, self.config))

    def test_returns_false_when_org_entity_with_values(self) -> None:
        state = _make_state(entities={"ORG": ["HSBC"]})
        self.assertFalse(is_auto_p4(state, self.config))

    def test_returns_false_when_person_entity_with_values(self) -> None:
        state = _make_state(entities={"PERSON": ["John Smith"]})
        self.assertFalse(is_auto_p4(state, self.config))

    def test_returns_true_when_high_value_label_present_but_empty(self) -> None:
        state = _make_state(entities={"MONEY": [], "ORG": []})
        self.assertTrue(is_auto_p4(state, self.config))

    def test_returns_false_when_retrieval_score_at_threshold(self) -> None:
        state = _make_state(retrieval_scores={"kb": 0.45})
        self.assertFalse(is_auto_p4(state, self.config))

    def test_returns_false_when_retrieval_score_above_threshold(self) -> None:
        state = _make_state(retrieval_scores={"kb": 0.90})
        self.assertFalse(is_auto_p4(state, self.config))

    def test_returns_true_when_retrieval_score_just_below_threshold(self) -> None:
        state = _make_state(retrieval_scores={"kb": 0.44})
        self.assertTrue(is_auto_p4(state, self.config))

    def test_returns_true_when_retrieval_scores_empty(self) -> None:
        state = _make_state(retrieval_scores={})
        self.assertTrue(is_auto_p4(state, self.config))

    def test_any_collection_above_threshold_blocks_auto_p4(self) -> None:
        state = _make_state(retrieval_scores={"kb": 0.1, "rules": 0.80})
        self.assertFalse(is_auto_p4(state, self.config))

    def test_non_high_value_entity_label_does_not_block(self) -> None:
        state = _make_state(entities={"GPE": ["London"], "PRODUCT": ["Widget"]})
        self.assertTrue(is_auto_p4(state, self.config))


class TestApplyAutoP4(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _make_config()

    def test_sets_is_auto_p4_true(self) -> None:
        state = _make_state()
        result = apply_auto_p4(state, self.config)
        self.assertTrue(result["is_auto_p4"])

    def test_priority_is_lowest_level(self) -> None:
        state = _make_state()
        result = apply_auto_p4(state, self.config)
        self.assertEqual(result["priority"], "P4")

    def test_dimension_scores_all_zero(self) -> None:
        state = _make_state()
        result = apply_auto_p4(state, self.config)
        self.assertEqual(result["dimension_scores"], {"fraud_risk": 0, "reg_breach": 0})

    def test_composite_score_is_zero(self) -> None:
        state = _make_state()
        result = apply_auto_p4(state, self.config)
        self.assertEqual(result["composite_score"], 0.0)

    def test_confidence_is_high(self) -> None:
        state = _make_state()
        result = apply_auto_p4(state, self.config)
        self.assertAlmostEqual(result["confidence"], 0.95)

    def test_low_confidence_reason_is_none(self) -> None:
        state = _make_state()
        result = apply_auto_p4(state, self.config)
        self.assertIsNone(result["low_confidence_reason"])

    def test_recommended_action_from_lowest_level(self) -> None:
        state = _make_state()
        result = apply_auto_p4(state, self.config)
        self.assertEqual(result["recommended_action"], "batch")

    def test_original_state_fields_preserved(self) -> None:
        state = _make_state(input_id="T-999", batch_id="B-007")
        result = apply_auto_p4(state, self.config)
        self.assertEqual(result["input_id"], "T-999")
        self.assertEqual(result["batch_id"], "B-007")


if __name__ == "__main__":
    unittest.main()
