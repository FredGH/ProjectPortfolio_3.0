"""Unit tests for core.skills.normalise."""

from __future__ import annotations

import unittest

from core.skills.normalise import normalise_skill


class TestNormaliseSkill(unittest.TestCase):
    def test_lowercases_and_collapses_whitespace(self) -> None:
        self.assertEqual(
            normalise_skill("  Google   Cloud\tPlatform "), "google cloud platform"
        )

    def test_expands_ampersand(self) -> None:
        self.assertEqual(normalise_skill("Data & Analytics"), "data and analytics")

    def test_strips_surrounding_punctuation(self) -> None:
        self.assertEqual(normalise_skill("(Terraform),"), "terraform")
        self.assertEqual(normalise_skill("Python."), "python")
        self.assertEqual(normalise_skill("(python.)"), "python")

    def test_keeps_leading_dot_plus_and_hash(self) -> None:
        self.assertEqual(normalise_skill(".NET"), ".net")
        self.assertEqual(normalise_skill("C++"), "c++")
        self.assertEqual(normalise_skill("C#"), "c#")
        self.assertEqual(normalise_skill("Node.js"), "node.js")

    def test_applies_nfkc_so_fullwidth_letters_match(self) -> None:
        self.assertEqual(normalise_skill("ＡＷＳ"), "aws")

    def test_empty_and_punctuation_only_input_returns_empty_string(self) -> None:
        self.assertEqual(normalise_skill(""), "")
        self.assertEqual(normalise_skill(" - "), "")

    def test_is_idempotent(self) -> None:
        once = normalise_skill("  Machine  Learning (ML)! ")
        self.assertEqual(normalise_skill(once), once)

    def test_does_not_merge_distinct_skills(self) -> None:
        self.assertNotEqual(normalise_skill("Java"), normalise_skill("JavaScript"))


if __name__ == "__main__":
    unittest.main()
