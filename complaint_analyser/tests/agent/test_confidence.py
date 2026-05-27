from __future__ import annotations

import unittest

from agentic_triage.agent.confidence import compute_confidence
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
        "retrieval_scores": {"kb": 0.8},
        "dimension_scores": {"fraud_risk": 2, "reg_breach": 1},
        "precedent_scores": {},
    }
    base.update(overrides)
    return base


class TestComputeConfidence(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _make_config()

    def test_full_confidence_when_no_penalties(self) -> None:
        state = _make_state()
        confidence, reason = compute_confidence(state, self.config)
        self.assertAlmostEqual(confidence, 1.0)
        self.assertIsNone(reason)

    def test_low_retrieval_penalty(self) -> None:
        state = _make_state(retrieval_scores={"kb": 0.4})
        confidence, reason = compute_confidence(state, self.config)
        self.assertAlmostEqual(confidence, 0.5)
        self.assertEqual(reason, "low_retrieval_similarity")

    def test_retrieval_score_exactly_at_threshold_is_not_penalised(self) -> None:
        state = _make_state(retrieval_scores={"kb": 0.6})
        confidence, reason = compute_confidence(state, self.config)
        self.assertAlmostEqual(confidence, 1.0)
        self.assertIsNone(reason)

    def test_divergence_penalty_when_precedent_available(self) -> None:
        state = _make_state(
            retrieval_scores={"kb": 0.9},
            dimension_scores={"fraud_risk": 5, "reg_breach": 1},
            precedent_scores={"fraud_risk": 1.0, "reg_breach": 1.0},
        )
        confidence, reason = compute_confidence(state, self.config)
        self.assertAlmostEqual(confidence, 0.5)
        self.assertEqual(reason, "high_score_divergence")

    def test_both_penalties_floor_at_zero(self) -> None:
        state = _make_state(
            retrieval_scores={"kb": 0.1},
            dimension_scores={"fraud_risk": 5, "reg_breach": 1},
            precedent_scores={"fraud_risk": 1.0, "reg_breach": 1.0},
        )
        confidence, reason = compute_confidence(state, self.config)
        self.assertAlmostEqual(confidence, 0.0)
        self.assertEqual(reason, "low_retrieval_similarity")

    def test_no_divergence_penalty_when_precedent_within_threshold(self) -> None:
        state = _make_state(
            retrieval_scores={"kb": 0.8},
            dimension_scores={"fraud_risk": 3, "reg_breach": 2},
            precedent_scores={"fraud_risk": 2.0, "reg_breach": 1.5},
        )
        confidence, reason = compute_confidence(state, self.config)
        self.assertAlmostEqual(confidence, 1.0)
        self.assertIsNone(reason)

    def test_divergence_skipped_when_no_precedent_scores(self) -> None:
        state = _make_state(
            retrieval_scores={"kb": 0.9},
            dimension_scores={"fraud_risk": 5},
            precedent_scores={},
        )
        confidence, reason = compute_confidence(state, self.config)
        self.assertAlmostEqual(confidence, 1.0)
        self.assertIsNone(reason)

    def test_any_low_retrieval_score_triggers_penalty(self) -> None:
        state = _make_state(
            retrieval_scores={"kb": 0.9, "rules": 0.3},
        )
        confidence, reason = compute_confidence(state, self.config)
        self.assertAlmostEqual(confidence, 0.5)
        self.assertEqual(reason, "low_retrieval_similarity")

    def test_low_retrieval_reason_takes_precedence_over_divergence(self) -> None:
        state = _make_state(
            retrieval_scores={"kb": 0.2},
            dimension_scores={"fraud_risk": 5, "reg_breach": 1},
            precedent_scores={"fraud_risk": 1.0},
        )
        confidence, reason = compute_confidence(state, self.config)
        self.assertAlmostEqual(confidence, 0.0)
        self.assertEqual(reason, "low_retrieval_similarity")


if __name__ == "__main__":
    unittest.main()
