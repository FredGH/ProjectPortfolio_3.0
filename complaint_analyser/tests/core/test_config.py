from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from agentic_triage.core.config import (
    CollectionConfig,
    DomainConfig,
    PriorityLevel,
    ScoringDimension,
)

DOMAINS_DIR = Path(__file__).parent.parent.parent / "domains"


class TestDomainConfigFromDict(unittest.TestCase):
    def _banking_data(self) -> dict:
        with (DOMAINS_DIR / "banking_complaints" / "config.yaml").open() as fh:
            return yaml.safe_load(fh)

    def test_banking_complaints_loads(self):
        config = DomainConfig.from_dict(self._banking_data())
        self.assertEqual(config.domain_name, "banking_complaints")
        self.assertEqual(config.input_field, "complaint_text")
        self.assertEqual(config.id_prefix, "C-")

    def test_scoring_dimensions_parsed(self):
        config = DomainConfig.from_dict(self._banking_data())
        self.assertEqual(len(config.scoring_dimensions), 2)
        self.assertIsInstance(config.scoring_dimensions[0], ScoringDimension)
        self.assertEqual(config.scoring_dimensions[0].name, "reputational_risk")

    def test_priority_levels_parsed(self):
        config = DomainConfig.from_dict(self._banking_data())
        self.assertEqual(len(config.priority_levels), 4)
        self.assertIsInstance(config.priority_levels[0], PriorityLevel)
        self.assertEqual(config.priority_levels[0].label, "P1")

    def test_p1_escalation_field(self):
        config = DomainConfig.from_dict(self._banking_data())
        p1 = config.priority_levels[0]
        self.assertEqual(p1.escalate_if_any_dimension_exceeds, 4.0)

    def test_p2_escalation_field_defaults_none(self):
        config = DomainConfig.from_dict(self._banking_data())
        p2 = config.priority_levels[1]
        self.assertIsNone(p2.escalate_if_any_dimension_exceeds)

    def test_collections_parsed(self):
        config = DomainConfig.from_dict(self._banking_data())
        self.assertEqual(len(config.collections), 3)
        self.assertIsInstance(config.collections[0], CollectionConfig)
        names = [c.name for c in config.collections]
        self.assertIn("complaints_history", names)
        self.assertIn("regulatory_rules", names)
        self.assertIn("risk_taxonomy", names)

    def test_defaults_applied(self):
        minimal = {
            "domain_name": "test",
            "input_field": "text",
            "id_prefix": "T-",
            "scoring_dimensions": [{"name": "risk", "description": "test risk"}],
            "priority_levels": [
                {
                    "label": "P1",
                    "min_composite": 5.0,
                    "description": "high",
                    "response_sla": "1 hour",
                    "recommended_action": "escalate",
                }
            ],
            "collections": [{"name": "kb", "role": "rules"}],
        }
        config = DomainConfig.from_dict(minimal)
        self.assertEqual(config.confidence_threshold, 0.7)
        self.assertEqual(config.max_reretrieval_loops, 2)
        self.assertFalse(config.use_hyde)
        self.assertEqual(config.multi_query_n, 0)

    def test_missing_required_field_raises(self):
        with self.assertRaises((KeyError, TypeError)):
            DomainConfig.from_dict({"domain_name": "incomplete"})


if __name__ == "__main__":
    unittest.main()
