"""Unit tests for core.skills.cv_map's pure helpers (no database)."""

from __future__ import annotations

import unittest

from core.skills.cv_map import carry_over_canonical_ids


def _skill(name: str | None, canonical_id: str | None = None) -> dict:
    return {
        "name": name,
        "canonical_id": canonical_id,
        "years": None,
        "last_used": None,
        "evidence_refs": [],
    }


class TestCarryOverCanonicalIds(unittest.TestCase):
    def test_an_unchanged_name_keeps_its_id(self) -> None:
        previous = [_skill("Python", "esco-python")]
        edited = [_skill("Python")]
        self.assertEqual(
            [s["canonical_id"] for s in carry_over_canonical_ids(previous, edited)],
            ["esco-python"],
        )

    def test_matching_ignores_case_and_surrounding_whitespace(self) -> None:
        previous = [_skill("  Python  ", "esco-python")]
        edited = [_skill("python")]
        self.assertEqual(
            carry_over_canonical_ids(previous, edited)[0]["canonical_id"],
            "esco-python",
        )

    def test_a_renamed_skill_gets_no_id(self) -> None:
        previous = [_skill("Python", "esco-python")]
        edited = [_skill("Python 3")]
        self.assertIsNone(carry_over_canonical_ids(previous, edited)[0]["canonical_id"])

    def test_a_new_skill_gets_no_id(self) -> None:
        previous = [_skill("Python", "esco-python")]
        edited = [_skill("Python"), _skill("Terraform")]
        self.assertEqual(
            [s["canonical_id"] for s in carry_over_canonical_ids(previous, edited)],
            ["esco-python", None],
        )

    def test_other_fields_and_row_order_are_untouched(self) -> None:
        previous = [_skill("Python", "esco-python")]
        edited = [{**_skill("SQL"), "years": 4}, {**_skill("Python"), "years": 9}]
        carried = carry_over_canonical_ids(previous, edited)
        self.assertEqual([s["name"] for s in carried], ["SQL", "Python"])
        self.assertEqual([s["years"] for s in carried], [4, 9])

    def test_the_inputs_are_not_mutated(self) -> None:
        previous = [_skill("Python", "esco-python")]
        edited = [_skill("Python")]
        carry_over_canonical_ids(previous, edited)
        self.assertIsNone(edited[0]["canonical_id"])

    def test_duplicate_names_all_take_the_first_matching_id(self) -> None:
        previous = [_skill("Python", "esco-python"), _skill("python", "manual:other")]
        edited = [_skill("Python"), _skill("PYTHON")]
        self.assertEqual(
            [s["canonical_id"] for s in carry_over_canonical_ids(previous, edited)],
            ["esco-python", "esco-python"],
        )

    def test_a_previous_skill_without_an_id_carries_nothing(self) -> None:
        previous = [_skill("Python")]
        edited = [_skill("Python")]
        self.assertIsNone(carry_over_canonical_ids(previous, edited)[0]["canonical_id"])

    def test_a_missing_name_never_matches_another_missing_name(self) -> None:
        # A blank grid cell arrives as None; two of them are not "the same
        # skill", so neither may inherit an id.
        previous = [_skill(None, "manual:1")]
        edited = [_skill(None)]
        self.assertIsNone(carry_over_canonical_ids(previous, edited)[0]["canonical_id"])

    def test_empty_lists(self) -> None:
        self.assertEqual(carry_over_canonical_ids([], []), [])
        self.assertEqual(carry_over_canonical_ids([_skill("Python", "x")], []), [])
        self.assertEqual(
            carry_over_canonical_ids([], [_skill("Python")])[0]["canonical_id"], None
        )


if __name__ == "__main__":
    unittest.main()
