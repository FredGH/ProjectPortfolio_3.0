"""Unit tests for core.skills.normalise."""

from __future__ import annotations

import unittest

from core.skills.normalise import (
    MAX_SKILL_CHARS,
    MAX_SKILL_WORDS,
    candidate_forms,
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


class TestCandidateForms(unittest.TestCase):
    """`candidate_forms` is the mapper's lookup order for one raw string."""

    def test_a_plain_skill_has_only_its_whole_form(self) -> None:
        self.assertEqual(candidate_forms("Python"), ["python"])

    def test_the_whole_normalised_string_always_comes_first(self) -> None:
        # The whole form is the persisted `skill_mapping` key, so it must be
        # exactly `normalise_skill` (including the stripped trailing paren).
        forms = candidate_forms("MySQL (RDS)")
        self.assertEqual(forms[0], normalise_skill("MySQL (RDS)"))
        self.assertEqual(forms, ["mysql (rds", "mysql"])

    def test_a_parenthetical_qualifier_is_dropped_for_the_head_form(self) -> None:
        self.assertEqual(
            candidate_forms("AWS (S3, ECS/Fargate, Lambda)"),
            ["aws (s3, ecs/fargate, lambda", "aws"],
        )

    def test_a_slash_term_stays_whole_and_only_the_qualifier_is_dropped(self) -> None:
        self.assertEqual(
            candidate_forms("CI/CD (Jira+Git+Terraform)"),
            ["ci/cd (jira+git+terraform", "ci/cd"],
        )

    def test_a_balanced_qualifier_mid_string_keeps_the_words_after_it(self) -> None:
        self.assertEqual(
            candidate_forms("Python (pandas) scripting"),
            ["python (pandas) scripting", "python scripting"],
        )

    def test_nested_qualifiers_are_all_dropped(self) -> None:
        self.assertEqual(
            candidate_forms("Cloud (AWS (S3)) platforms"),
            ["cloud (aws (s3)) platforms", "cloud platforms"],
        )

    def test_a_slash_or_comma_list_is_not_split_here(self) -> None:
        # Splitting "TypeScript/React" into two skills is W1b, deliberately
        # not done: it needs a new table and a bridge change.
        self.assertEqual(candidate_forms("TypeScript/React"), ["typescript/react"])
        self.assertEqual(candidate_forms("Hive, Impala"), ["hive, impala"])

    def test_a_qualifier_only_string_has_no_separate_head(self) -> None:
        # normalise_skill already strips the surrounding parentheses.
        self.assertEqual(candidate_forms("(RDS)"), ["rds"])

    def test_an_empty_parenthesis_pair_adds_no_extra_form(self) -> None:
        self.assertEqual(candidate_forms("C++ ()"), ["c++"])

    def test_blank_and_punctuation_only_input_has_no_forms(self) -> None:
        self.assertEqual(candidate_forms(""), [])
        self.assertEqual(candidate_forms("  ( ) "), [])
        self.assertEqual(candidate_forms(" - "), [])


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
