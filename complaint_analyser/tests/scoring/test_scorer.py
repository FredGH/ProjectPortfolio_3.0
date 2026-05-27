from __future__ import annotations

import unittest

from agentic_triage.core.config import (
    CollectionConfig,
    DomainConfig,
    PriorityLevel,
    ScoringDimension,
)
from agentic_triage.scoring.scorer import compute_priority


def _make_config(escalate_p1: float | None = 4.0) -> DomainConfig:
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
                weight=2.0,
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
                min_composite=3.0,
                description="Critical",
                response_sla="4h",
                recommended_action="escalate",
                escalate_if_any_dimension_exceeds=escalate_p1,
            ),
            PriorityLevel(
                label="P2",
                min_composite=2.0,
                description="High",
                response_sla="24h",
                recommended_action="review",
            ),
            PriorityLevel(
                label="P3",
                min_composite=1.0,
                description="Medium",
                response_sla="72h",
                recommended_action="queue",
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


class TestComputePriority(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _make_config()

    def test_p4_when_all_scores_zero(self) -> None:
        label, composite = compute_priority(
            {"fraud_risk": 0, "reg_breach": 0}, self.config
        )
        self.assertEqual(label, "P4")
        self.assertAlmostEqual(composite, 0.0)

    def test_p3_when_composite_in_range(self) -> None:
        # fraud_risk=1 * weight=2, reg_breach=1 * weight=1 → composite = 3/3 = 1.0 → P3
        label, composite = compute_priority(
            {"fraud_risk": 1, "reg_breach": 1}, self.config
        )
        self.assertEqual(label, "P3")
        self.assertAlmostEqual(composite, 1.0)

    def test_p2_when_composite_in_range(self) -> None:
        # fraud_risk=2 * 2 + reg_breach=2 * 1 = 6 / 3 = 2.0 → P2
        label, composite = compute_priority(
            {"fraud_risk": 2, "reg_breach": 2}, self.config
        )
        self.assertEqual(label, "P2")
        self.assertAlmostEqual(composite, 2.0)

    def test_p1_when_composite_above_threshold(self) -> None:
        # fraud_risk=4 * 2 + reg_breach=4 * 1 = 12 / 3 = 4.0 → would be P1 by composite
        # but first check escalation: 4 > 4.0 is False; 4.0 > 4.0 is False → no escalation
        # composite 4.0 >= 3.0 → P1
        label, composite = compute_priority(
            {"fraud_risk": 4, "reg_breach": 4}, self.config
        )
        self.assertEqual(label, "P1")

    def test_escalation_override_bypasses_composite(self) -> None:
        # reg_breach=5 > 4.0 threshold → P1 regardless of composite
        label, composite = compute_priority(
            {"fraud_risk": 0, "reg_breach": 5}, self.config
        )
        self.assertEqual(label, "P1")
        # composite = 0*2 + 5*1 = 5 / 3 ≈ 1.67 — well below P1 composite threshold
        self.assertAlmostEqual(composite, 5.0 / 3.0, places=5)

    def test_escalation_exactly_at_threshold_does_not_trigger(self) -> None:
        # dimension score == escalate threshold → not > threshold, so no escalation
        label, _ = compute_priority({"fraud_risk": 4, "reg_breach": 0}, self.config)
        # composite = 4*2 + 0*1 = 8/3 ≈ 2.67 → P2
        self.assertEqual(label, "P2")

    def test_no_escalation_when_escalate_threshold_is_none(self) -> None:
        config = _make_config(escalate_p1=None)
        # All scores 0 → P4 (no escalation field set on P1)
        label, _ = compute_priority({"fraud_risk": 0, "reg_breach": 0}, config)
        self.assertEqual(label, "P4")

    def test_composite_zero_when_no_dimensions(self) -> None:
        config = DomainConfig(
            domain_name="empty",
            input_field="text",
            id_prefix="E-",
            scoring_dimensions=[],
            priority_levels=[
                PriorityLevel(
                    label="P4",
                    min_composite=0.0,
                    description="Low",
                    response_sla="5d",
                    recommended_action="batch",
                )
            ],
            collections=[],
        )
        label, composite = compute_priority({}, config)
        self.assertEqual(label, "P4")
        self.assertAlmostEqual(composite, 0.0)

    def test_returns_lowest_level_when_no_threshold_matches(self) -> None:
        config = DomainConfig(
            domain_name="tight",
            input_field="text",
            id_prefix="T-",
            scoring_dimensions=[
                ScoringDimension(
                    name="risk",
                    description="risk",
                    min_score=0,
                    max_score=5,
                    weight=1.0,
                )
            ],
            priority_levels=[
                PriorityLevel(
                    label="P1",
                    min_composite=10.0,
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
            collections=[],
        )
        label, _ = compute_priority({"risk": 5}, config)
        # composite = 5.0 < 10.0 → falls through to P4
        self.assertEqual(label, "P4")

    def test_weighted_composite_calculation(self) -> None:
        # fraud_risk=3 * weight=2, reg_breach=0 * weight=1 → 6/3 = 2.0
        _, composite = compute_priority({"fraud_risk": 3, "reg_breach": 0}, self.config)
        self.assertAlmostEqual(composite, 2.0)

    def test_missing_dimension_treated_as_zero(self) -> None:
        # Only provide one dimension; missing one defaults to 0
        label, composite = compute_priority({"fraud_risk": 0}, self.config)
        self.assertEqual(label, "P4")
        self.assertAlmostEqual(composite, 0.0)


if __name__ == "__main__":
    unittest.main()
