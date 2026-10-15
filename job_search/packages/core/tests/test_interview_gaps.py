"""Unit tests for CV-gap scoring."""

from __future__ import annotations

import unittest

from tests.tailoring_fixtures import make_truth_base

from core.cv.schema import Skill
from core.interview.gaps import load_alias_groups, score_question

_GROUPS = [{"kubernetes", "k8s"}, {"amazon web services", "aws"}]


class TestScoreQuestion(unittest.TestCase):
    """Tags absent from the CV are the gaps."""

    def test_a_tag_evidenced_in_the_current_role_scores_lowest(self) -> None:
        result = score_question(["dbt"], 3, make_truth_base(), _GROUPS)
        self.assertAlmostEqual(result.score, 0.1 * 0.9)
        self.assertEqual(result.missing_tags, [])

    def test_a_tag_evidenced_only_in_an_older_role_scores_slightly_higher(self) -> None:
        result = score_question(["SQL"], 3, make_truth_base(), _GROUPS)
        self.assertAlmostEqual(result.score, 0.2 * 0.9)

    def test_a_listed_skill_with_no_bullet_evidence_scores_in_between(self) -> None:
        truth = make_truth_base()
        truth.skills.append(Skill(name="Terraform"))
        result = score_question(["terraform"], 3, truth, _GROUPS)
        self.assertAlmostEqual(result.score, 0.6 * 0.9)

    def test_an_absent_tag_is_a_full_gap_and_is_reported(self) -> None:
        result = score_question(["Rust"], 3, make_truth_base(), _GROUPS)
        self.assertAlmostEqual(result.score, 1.0 * 0.9)
        self.assertEqual(result.missing_tags, ["Rust"])

    def test_aliases_count_as_the_same_skill(self) -> None:
        truth = make_truth_base()
        truth.experience[0].bullets[0].text = "Ran workloads on K8s clusters"
        self.assertEqual(
            score_question(["Kubernetes"], 3, truth, _GROUPS).missing_tags, []
        )

    def test_a_tag_is_matched_as_a_word_not_a_substring(self) -> None:
        truth = make_truth_base()
        truth.experience[0].bullets[0].text = "Wrote scripts in Go-lang style"
        self.assertEqual(score_question(["R"], 3, truth, _GROUPS).missing_tags, ["R"])

    def test_difficulty_scales_the_score(self) -> None:
        easy = score_question(["Rust"], 1, make_truth_base(), _GROUPS).score
        hard = score_question(["Rust"], 5, make_truth_base(), _GROUPS).score
        self.assertLess(easy, hard)

    def test_the_score_is_the_mean_over_tags(self) -> None:
        result = score_question(["dbt", "Rust"], 3, make_truth_base(), _GROUPS)
        self.assertAlmostEqual(result.score, ((0.1 + 1.0) / 2) * 0.9)

    def test_no_tags_scores_neutral(self) -> None:
        self.assertAlmostEqual(
            score_question([], 3, make_truth_base(), _GROUPS).score, 0.5 * 0.9
        )


class TestAliasGroups(unittest.TestCase):
    """The shipped alias file loads into spelling groups."""

    def test_groups_include_labels_and_aliases_lowercased(self) -> None:
        groups = load_alias_groups()
        self.assertTrue(any({"kubernetes", "k8s"} <= g for g in groups))


if __name__ == "__main__":
    unittest.main()
