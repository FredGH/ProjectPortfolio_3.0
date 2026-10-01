"""Every pipeline stage must have a dashboard explanation, so a new
stage cannot ship with an empty Explain modal (the same guard
test_pipeline_registry.py applies to CLI subcommands)."""

from __future__ import annotations

import unittest

from core.pipeline.descriptions import PHASE_DESCRIPTIONS, STAGE_DESCRIPTIONS
from core.pipeline.registry import REVIEW_STAGES, STAGES


class TestStageDescriptions(unittest.TestCase):
    def test_every_stage_has_a_description(self) -> None:
        expected = set(STAGES) | set(REVIEW_STAGES)
        self.assertEqual(expected - set(STAGE_DESCRIPTIONS), set())

    def test_no_description_for_an_unknown_stage(self) -> None:
        expected = set(STAGES) | set(REVIEW_STAGES)
        self.assertEqual(set(STAGE_DESCRIPTIONS) - expected, set())

    def test_every_description_fills_all_three_fields(self) -> None:
        for key, description in {
            **STAGE_DESCRIPTIONS,
            **PHASE_DESCRIPTIONS,
        }.items():
            for field in ("summary", "input", "output"):
                with self.subTest(stage=key, field=field):
                    self.assertTrue(getattr(description, field).strip())


if __name__ == "__main__":
    unittest.main()
