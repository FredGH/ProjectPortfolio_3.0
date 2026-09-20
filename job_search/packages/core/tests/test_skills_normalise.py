"""Unit tests for core.skills.normalise."""

from __future__ import annotations

import unittest

from core.skills.normalise import (
    MAX_SKILL_CHARS,
    MAX_SKILL_WORDS,
    is_plausible_skill,
    normalise_skill,
)


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


class TestIsPlausibleSkill(unittest.TestCase):
    def test_accepts_an_ordinary_skill_name(self) -> None:
        self.assertTrue(is_plausible_skill("google cloud platform"))

    def test_rejects_an_empty_string(self) -> None:
        self.assertFalse(is_plausible_skill(""))

    def test_accepts_exactly_the_character_limit_and_rejects_one_more(self) -> None:
        self.assertTrue(is_plausible_skill("a" * MAX_SKILL_CHARS))
        self.assertFalse(is_plausible_skill("a" * (MAX_SKILL_CHARS + 1)))

    def test_accepts_exactly_the_word_limit_and_rejects_one_more(self) -> None:
        self.assertTrue(is_plausible_skill(" ".join(["ab"] * MAX_SKILL_WORDS)))
        self.assertFalse(is_plausible_skill(" ".join(["ab"] * (MAX_SKILL_WORDS + 1))))

    def test_rejects_a_sentence_an_llm_returned_as_a_skill(self) -> None:
        self.assertFalse(
            is_plausible_skill(
                "experience working with distributed systems at scale in a "
                "fast-paced agile environment"
            )
        )


if __name__ == "__main__":
    unittest.main()
