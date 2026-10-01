"""Unit tests for the code-only tailoring checks."""

from __future__ import annotations

import unittest

from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.cv.schema import Skill
from core.tailoring.assemble import assemble
from core.tailoring.checks import (
    STRUCTURAL_CODES,
    check_evidence_refs,
    check_experience_unchanged,
    check_headline,
    compute_keyword_coverage,
)
from core.tailoring.schema import JobSkill, TailorBullet, TailorExperience, TailorOutput

TITLE = "Lead Data Engineer"


def _document(truth_base, bullets_by_role: dict[int, list[TailorBullet]], skills=None):
    output = TailorOutput(
        experience=[
            TailorExperience(truth_index=i, bullets=b)
            for i, b in bullets_by_role.items()
        ],
        skills=skills or [],
    )
    return assemble(truth_base, output, target_title=TITLE)


class TestEvidenceChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        self.ref_old = bullet_id(self.truth_base, 1, 0)

    def test_a_valid_same_role_reference_has_no_problem(self) -> None:
        document = _document(
            self.truth_base,
            {0: [TailorBullet(text="Built it", evidence_refs=[self.ref0])]},
        )
        self.assertEqual(check_evidence_refs(document, self.truth_base), [])

    def test_an_unknown_reference_is_flagged_with_its_location(self) -> None:
        document = _document(
            self.truth_base,
            {0: [TailorBullet(text="Built it", evidence_refs=[self.ref0, "nope"])]},
        )
        problems = check_evidence_refs(document, self.truth_base)
        self.assertEqual([p.code for p in problems], ["invalid_evidence_ref"])
        self.assertEqual(problems[0].location, "e0b0")

    def test_a_bullet_citing_another_role_is_flagged(self) -> None:
        # Review Focus 1: moving an achievement to a different employer.
        document = _document(
            self.truth_base,
            {0: [TailorBullet(text="Wrote reports", evidence_refs=[self.ref_old])]},
        )
        problems = check_evidence_refs(document, self.truth_base)
        self.assertEqual([p.code for p in problems], ["cross_role_evidence"])
        self.assertEqual(problems[0].location, "e0b0")

    def test_an_orphan_with_no_refs_is_not_an_evidence_problem(self) -> None:
        # Orphans are surfaced through their `origin`, not as a Problem.
        document = _document(
            self.truth_base, {0: [TailorBullet(text="Invented", evidence_refs=[])]}
        )
        self.assertEqual(check_evidence_refs(document, self.truth_base), [])

    def test_an_unknown_summary_reference_is_flagged(self) -> None:
        from core.tailoring.schema import TailorSummary

        output = TailorOutput(
            summary=TailorSummary(text="New summary", evidence_refs=["nope"])
        )
        document = assemble(self.truth_base, output, target_title=TITLE)
        problems = check_evidence_refs(document, self.truth_base)
        self.assertEqual(problems[0].location, "summary")


class TestExperienceUnchanged(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.document = _document(self.truth_base, {})

    def test_an_assembled_document_has_no_problem(self) -> None:
        self.assertEqual(check_experience_unchanged(self.document, self.truth_base), [])

    def test_a_changed_title_is_flagged(self) -> None:
        self.document.experience[0].title = "Head of Data"
        problems = check_experience_unchanged(self.document, self.truth_base)
        self.assertEqual([p.code for p in problems], ["experience_changed"])
        self.assertEqual(problems[0].location, "e0")

    def test_a_changed_date_is_flagged(self) -> None:
        self.document.experience[1].end = "2020-01"
        problems = check_experience_unchanged(self.document, self.truth_base)
        self.assertEqual(problems[0].location, "e1")

    def test_a_missing_role_is_flagged(self) -> None:
        self.document.experience.pop()
        problems = check_experience_unchanged(self.document, self.truth_base)
        self.assertEqual([p.code for p in problems], ["experience_changed"])

    def test_structural_codes_cover_what_cannot_become_an_orphan(self) -> None:
        self.assertEqual(
            STRUCTURAL_CODES,
            frozenset(
                {"experience_changed", "headline_mismatch", "missing_target_title"}
            ),
        )


class TestHeadline(unittest.TestCase):
    def setUp(self) -> None:
        self.document = _document(make_truth_base(), {})

    def test_the_exact_target_title_passes(self) -> None:
        self.assertEqual(check_headline(self.document), [])

    def test_a_different_headline_is_flagged(self) -> None:
        self.document.headline = "Senior Data Engineer"
        self.assertEqual(
            [p.code for p in check_headline(self.document)], ["headline_mismatch"]
        )

    def test_a_case_difference_is_flagged(self) -> None:
        self.document.headline = TITLE.upper()
        self.assertEqual(
            [p.code for p in check_headline(self.document)], ["headline_mismatch"]
        )

    def test_a_blank_target_title_is_flagged(self) -> None:
        self.document.target_title = "  "
        self.document.headline = "  "
        self.assertEqual(
            [p.code for p in check_headline(self.document)], ["missing_target_title"]
        )


class TestKeywordCoverage(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.job_skills = [
            JobSkill("zzfixture-skill-dbt", "dbt", "must_have"),
            JobSkill("zzfixture-skill-sql", "SQL", "nice_to_have"),
            JobSkill("zzfixture-skill-k8s", "Kubernetes", "must_have"),
        ]

    def test_a_skill_shown_in_the_document_is_covered(self) -> None:
        document = _document(self.truth_base, {}, skills=["dbt"])
        coverage = compute_keyword_coverage(document, self.truth_base, self.job_skills)
        self.assertIn("dbt", coverage.covered)

    def test_a_skill_the_cv_has_but_the_document_omits_is_missing_evidenced(
        self,
    ) -> None:
        # Both roles are emptied: an omitted role keeps its original bullets,
        # and role 1's original bullet mentions SQL, which would cover it.
        document = _document(self.truth_base, {0: [], 1: []}, skills=["dbt"])
        coverage = compute_keyword_coverage(document, self.truth_base, self.job_skills)
        self.assertEqual(coverage.missing_evidenced, ["SQL"])

    def test_a_skill_the_cv_never_evidences_is_missing_unevidenced(self) -> None:
        document = _document(self.truth_base, {}, skills=["dbt", "SQL"])
        coverage = compute_keyword_coverage(document, self.truth_base, self.job_skills)
        self.assertEqual(coverage.missing_unevidenced, ["Kubernetes"])
        self.assertNotIn("Kubernetes", coverage.missing_evidenced)

    def test_a_label_mentioned_in_bullet_text_counts_as_covered(self) -> None:
        document = _document(self.truth_base, {})
        coverage = compute_keyword_coverage(
            document,
            self.truth_base,
            [JobSkill("x", "Airflow", "must_have")],
        )
        self.assertEqual(coverage.covered, ["Airflow"])

    def test_matching_respects_word_boundaries(self) -> None:
        # "R" must not match inside "Airflow" or "dbt models for risk reporting".
        document = _document(self.truth_base, {})
        coverage = compute_keyword_coverage(
            document, self.truth_base, [JobSkill("zzfixture-r", "R", "nice_to_have")]
        )
        self.assertEqual(coverage.covered, [])
        self.assertEqual(coverage.missing_unevidenced, ["R"])

    def test_canonical_id_match_covers_a_differently_named_skill(self) -> None:
        self.truth_base.skills.append(
            Skill(name="Data build tool", canonical_id="zz-id")
        )
        document = _document(self.truth_base, {}, skills=["Data build tool"])
        coverage = compute_keyword_coverage(
            document, self.truth_base, [JobSkill("zz-id", "dbt core", "must_have")]
        )
        self.assertEqual(coverage.covered, ["dbt core"])


if __name__ == "__main__":
    unittest.main()
