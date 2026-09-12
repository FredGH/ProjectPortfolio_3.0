from __future__ import annotations

import unittest

from core.classification.rules import classify_by_rules


class TestClassifyByRules(unittest.TestCase):
    def test_none_title_returns_none(self) -> None:
        self.assertIsNone(classify_by_rules(None))

    def test_software_engineer_titles(self) -> None:
        for title in (
            "Software Engineer",
            "Senior Backend Developer",
            "Full Stack Developer",
        ):
            self.assertEqual(classify_by_rules(title), "software_engineer")

    def test_data_engineer_titles(self) -> None:
        for title in ("Data Engineer", "ETL Developer", "Data Platform Engineer"):
            self.assertEqual(classify_by_rules(title), "data_engineer")

    def test_data_scientist_titles(self) -> None:
        for title in ("Data Scientist", "Quantitative Analyst"):
            self.assertEqual(classify_by_rules(title), "data_scientist")

    def test_ai_ml_engineer_titles(self) -> None:
        for title in ("Machine Learning Engineer", "NLP Engineer", "AI Engineer"):
            self.assertEqual(classify_by_rules(title), "ai_ml_engineer")

    def test_analytics_engineer_titles(self) -> None:
        for title in ("Analytics Engineer", "Business Intelligence Engineer"):
            self.assertEqual(classify_by_rules(title), "analytics_engineer")

    def test_platform_devops_titles(self) -> None:
        for title in ("DevOps Engineer", "Site Reliability Engineer", "SRE"):
            self.assertEqual(classify_by_rules(title), "platform_devops")

    def test_unrecognised_title_returns_none(self) -> None:
        # Must cascade to the embedding/LLM stages, not guess "other".
        self.assertIsNone(classify_by_rules("Product Manager"))

    def test_data_platform_engineer_does_not_false_positive_as_software_engineer(
        self,
    ) -> None:
        # Regression: "engineer" alone must not trigger the generic
        # software_engineer pattern before the more specific data_engineer
        # pattern gets a chance — rule order matters.
        self.assertEqual(classify_by_rules("Data Platform Engineer"), "data_engineer")

    def test_case_insensitive(self) -> None:
        self.assertEqual(classify_by_rules("SENIOR DATA ENGINEER"), "data_engineer")
