from __future__ import annotations

import unittest

from pydantic import ValidationError

from agentic_triage.core.schema import TriageResult


def _valid_payload() -> dict:
    return {
        "input_id": "C-20240501-001",
        "priority": "P2",
        "dimension_scores": {"reputational_risk": 3, "financial_impact": 4},
        "composite_score": 7.0,
        "confidence": 0.85,
        "low_confidence_reason": None,
        "triggered_keywords": ["GDPR", "FCA"],
        "retrieved_references": {"precedent": ["C-001", "C-002"], "rules": ["R-01"]},
        "reasoning": "High financial impact detected; regulatory exposure present.",
        "recommended_action": "Assign to senior support agent",
    }


class TestTriageResult(unittest.TestCase):
    def test_valid_result_constructs(self):
        result = TriageResult(**_valid_payload())
        self.assertEqual(result.input_id, "C-20240501-001")
        self.assertEqual(result.priority, "P2")
        self.assertAlmostEqual(result.composite_score, 7.0)

    def test_analyst_override_defaults_none(self):
        result = TriageResult(**_valid_payload())
        self.assertIsNone(result.analyst_override)

    def test_analyst_override_set(self):
        payload = {**_valid_payload(), "analyst_override": "P1"}
        result = TriageResult(**payload)
        self.assertEqual(result.analyst_override, "P1")

    def test_missing_required_field_raises(self):
        payload = _valid_payload()
        del payload["priority"]
        with self.assertRaises(ValidationError):
            TriageResult(**payload)

    def test_low_confidence_reason_accepts_string(self):
        payload = {
            **_valid_payload(),
            "low_confidence_reason": "low_retrieval_similarity",
        }
        result = TriageResult(**payload)
        self.assertEqual(result.low_confidence_reason, "low_retrieval_similarity")

    def test_empty_triggered_keywords(self):
        payload = {**_valid_payload(), "triggered_keywords": []}
        result = TriageResult(**payload)
        self.assertEqual(result.triggered_keywords, [])


if __name__ == "__main__":
    unittest.main()
