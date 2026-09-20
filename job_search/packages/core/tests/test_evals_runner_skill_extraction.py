"""skill_extraction's golden set loads and the predictor round-trips it.

Unit-level, fake adapter only (mirrors test_evals_runner_cv_extraction.py).
"""

from __future__ import annotations

import json
import unittest

from tests.skills_fakes import FakeAdapter

from core.evals.golden import load_golden_set
from core.evals.metrics import field_f1
from core.evals.runner import _PREDICTORS, MINIMUM_GOLDEN_SET_SIZE
from core.skills.normalise import normalise_skill


def _reply(*skills: tuple[str, str]) -> str:
    return json.dumps(
        {"skills": [{"skill": s, "requirement_level": lvl} for s, lvl in skills]}
    )


def _predict(case, adapter):
    return _PREDICTORS["skill_extraction"](
        case,
        provider="ollama",
        model="test-model",
        prompt_family="local",
        adapters={"ollama": adapter},
    )


class TestSkillExtractionGoldenSet(unittest.TestCase):
    def test_has_at_least_the_minimum_case_count(self) -> None:
        self.assertGreaterEqual(
            len(load_golden_set("skill_extraction")), MINIMUM_GOLDEN_SET_SIZE
        )

    def test_expected_keys_are_normalised_and_levels_are_valid(self) -> None:
        for case in load_golden_set("skill_extraction"):
            for key, level in case.expected.items():
                self.assertTrue(key.startswith("skill:"), (case.case_id, key))
                name = key[len("skill:") :]
                self.assertEqual(name, normalise_skill(name), (case.case_id, key))
                self.assertIn(level, {"must_have", "nice_to_have"}, case.case_id)

    def test_includes_cases_that_name_no_skills(self) -> None:
        empty = [c for c in load_golden_set("skill_extraction") if not c.expected]
        self.assertGreaterEqual(len(empty), 2)

    def test_case_ids_are_unique(self) -> None:
        ids = [c.case_id for c in load_golden_set("skill_extraction")]
        self.assertEqual(len(ids), len(set(ids)))


class TestSkillExtractionPredictor(unittest.TestCase):
    def test_is_registered(self) -> None:
        self.assertIn("skill_extraction", _PREDICTORS)

    def test_flattens_extraction_to_skill_level_pairs_and_reports_the_version(
        self,
    ) -> None:
        case = load_golden_set("skill_extraction")[0]
        adapter = FakeAdapter(_reply(("Python", "must_have"), ("dbt", "nice_to_have")))
        predicted, prompt_version = _predict(case, adapter)
        self.assertEqual(
            predicted, {"skill:python": "must_have", "skill:dbt": "nice_to_have"}
        )
        self.assertEqual(prompt_version, "local.v1")

    def test_a_perfect_prediction_scores_one_under_field_f1(self) -> None:
        case = next(
            c for c in load_golden_set("skill_extraction") if c.case_id == "skill_001"
        )
        adapter = FakeAdapter(
            _reply(
                ("Python", "must_have"),
                ("SQL", "must_have"),
                ("Airflow", "must_have"),
                ("dbt", "nice_to_have"),
                ("Terraform", "nice_to_have"),
            )
        )
        predicted, _version = _predict(case, adapter)
        self.assertEqual(field_f1(predicted, case.expected), 1.0)

    def test_a_malformed_response_falls_back_to_empty(self) -> None:
        case = load_golden_set("skill_extraction")[0]
        self.assertEqual(_predict(case, FakeAdapter("not json")), ({}, None))


if __name__ == "__main__":
    unittest.main()
