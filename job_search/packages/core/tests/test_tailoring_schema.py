"""Unit tests for the tailoring document and Tailor-output models."""

from __future__ import annotations

import json
import unittest

from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.tailoring.schema import (
    JobContext,
    JobSkill,
    TailorBullet,
    TailoredBullet,
    TailoredDocument,
    TailorOutputError,
    parse_tailor_output,
)


class TestParseTailorOutput(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()

    def _reply(self) -> dict:
        ref = bullet_id(self.truth_base, 0, 0)
        return {
            "summary": {"text": "Data engineer.", "evidence_refs": [ref]},
            "experience": [
                {
                    "truth_index": 0,
                    "bullets": [{"text": "Built dbt models", "evidence_refs": [ref]}],
                }
            ],
            "skills": ["dbt"],
        }

    def test_parses_a_bare_json_reply(self) -> None:
        output = parse_tailor_output(json.dumps(self._reply()))
        self.assertEqual(output.experience[0].truth_index, 0)
        self.assertEqual(output.experience[0].bullets[0].text, "Built dbt models")
        self.assertEqual(output.skills, ["dbt"])

    def test_parses_a_fenced_reply(self) -> None:
        text = "Here you go:\n```json\n" + json.dumps(self._reply()) + "\n```"
        output = parse_tailor_output(text)
        self.assertEqual(output.skills, ["dbt"])

    def test_missing_sections_default_to_empty(self) -> None:
        output = parse_tailor_output("{}")
        self.assertIsNone(output.summary)
        self.assertEqual(output.experience, [])
        self.assertEqual(output.skills, [])

    def test_unparseable_text_raises(self) -> None:
        with self.assertRaises(TailorOutputError):
            parse_tailor_output("sorry, I cannot do that")

    def test_wrong_shape_raises(self) -> None:
        with self.assertRaises(TailorOutputError):
            parse_tailor_output('{"experience": "not a list"}')

    def test_a_bullet_without_text_raises(self) -> None:
        with self.assertRaises(TailorOutputError):
            parse_tailor_output(
                '{"experience": [{"truth_index": 0, '
                '"bullets": [{"evidence_refs": []}]}]}'
            )


class TestTailorBullet(unittest.TestCase):
    def test_a_keep_item_is_valid(self) -> None:
        bullet = TailorBullet.model_validate({"keep": "b-1"})
        self.assertEqual((bullet.keep, bullet.text), ("b-1", None))

    def test_a_text_item_is_valid(self) -> None:
        bullet = TailorBullet.model_validate(
            {"text": "Built x", "evidence_refs": ["b"]}
        )
        self.assertEqual((bullet.keep, bullet.text), (None, "Built x"))

    def test_both_keep_and_text_is_an_error(self) -> None:
        with self.assertRaises(TailorOutputError):
            parse_tailor_output(
                '{"experience": [{"truth_index": 0, '
                '"bullets": [{"keep": "b", "text": "x"}]}]}'
            )

    def test_neither_keep_nor_text_is_an_error(self) -> None:
        with self.assertRaises(TailorOutputError):
            parse_tailor_output('{"experience": [{"truth_index": 0, "bullets": [{}]}]}')

    def test_empty_text_or_empty_keep_is_an_error(self) -> None:
        for bullet in ('{"text": "  "}', '{"keep": ""}'):
            with self.subTest(bullet=bullet), self.assertRaises(TailorOutputError):
                parse_tailor_output(
                    '{"experience": [{"truth_index": 0, "bullets": [' + bullet + "]}]}"
                )


class TestTailoredDocument(unittest.TestCase):
    def test_round_trips_through_json(self) -> None:
        document = TailoredDocument(
            target_title="Lead Data Engineer",
            headline="Lead Data Engineer",
            identity="Zz Fixture",
            experience=[],
            skills=[],
        )
        again = TailoredDocument.model_validate_json(document.model_dump_json())
        self.assertEqual(again, document)

    def test_an_unknown_origin_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TailoredBullet(text="x", evidence_refs=[], origin="invented")


class TestJobContext(unittest.TestCase):
    def test_holds_job_skills(self) -> None:
        context = JobContext(
            job_group_id="zzfixture-1",
            title_for_display="Lead Data Engineer",
            company="Gamma",
            description="Build things.",
            skills=[JobSkill("s1", "dbt", "must_have")],
        )
        self.assertEqual(context.skills[0].label, "dbt")


if __name__ == "__main__":
    unittest.main()
