"""Unit tests for deterministic tailored-document assembly."""

from __future__ import annotations

import unittest

from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.tailoring.assemble import assemble, bullet_index, classify_origin, clean_text
from core.tailoring.schema import (
    TailorBullet,
    TailorExperience,
    TailorOutput,
    TailorSummary,
)

TITLE = "Lead Data Engineer"


class TestCleanText(unittest.TestCase):
    def test_strips_a_leading_bullet_glyph(self) -> None:
        self.assertEqual(clean_text("• Built dbt models"), "Built dbt models")

    def test_strips_a_leading_dash_followed_by_space(self) -> None:
        self.assertEqual(clean_text("- Built dbt models"), "Built dbt models")

    def test_keeps_a_leading_minus_sign_that_is_part_of_a_number(self) -> None:
        self.assertEqual(clean_text("-5% churn"), "-5% churn")

    def test_removes_decorative_symbols_and_emoji(self) -> None:
        self.assertEqual(clean_text("Led ★ the team ✅ 🚀"), "Led the team")

    def test_keeps_legitimate_symbols(self) -> None:
        self.assertEqual(clean_text("Cooled to 20°C"), "Cooled to 20°C")
        self.assertEqual(
            clean_text("Built Tableau® dashboards"), "Built Tableau® dashboards"
        )
        self.assertEqual(clean_text("Shipped Acme™ © 2020"), "Shipped Acme™ © 2020")

    def test_strips_the_invisible_parts_of_emoji_sequences(self) -> None:
        self.assertEqual(clean_text("✅️ Shipped"), "Shipped")
        self.assertEqual(clean_text("Coded 👩‍💻 daily"), "Coded daily")
        self.assertEqual(clean_text("Step 1⃣ done"), "Step 1 done")
        for invisible in ("️", "︀", "‍", "⃣"):
            self.assertNotIn(invisible, clean_text(f"A{invisible}B"))

    def test_strips_markdown_emphasis_markers(self) -> None:
        self.assertEqual(clean_text("**Built** x"), "Built x")
        self.assertEqual(clean_text("__Built__ x"), "Built x")
        self.assertEqual(clean_text("Built *fast* pipelines"), "Built fast pipelines")
        self.assertEqual(clean_text("Built _fast_ pipelines"), "Built fast pipelines")

    def test_keeps_a_lone_asterisk_and_snake_case(self) -> None:
        self.assertEqual(clean_text("Grew revenue 2x*"), "Grew revenue 2x*")
        self.assertEqual(clean_text("Tuned 3 * 4 grid"), "Tuned 3 * 4 grid")
        self.assertEqual(clean_text("Owned dim_job_score"), "Owned dim_job_score")

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(clean_text("  Built   dbt\n models "), "Built dbt models")


class TestClassifyOrigin(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.known = bullet_index(self.truth_base)
        self.ref = bullet_id(self.truth_base, 0, 0)

    def test_identical_text_with_one_valid_ref_is_original(self) -> None:
        origin = classify_origin(
            "Built dbt models for risk reporting", [self.ref], self.known
        )
        self.assertEqual(origin, "original")

    def test_identical_text_ignoring_case_and_spacing_is_original(self) -> None:
        origin = classify_origin(
            "built  DBT models for risk reporting", [self.ref], self.known
        )
        self.assertEqual(origin, "original")

    def test_changed_text_with_a_valid_ref_is_reworded(self) -> None:
        origin = classify_origin(
            "Built dbt models powering risk reporting", [self.ref], self.known
        )
        self.assertEqual(origin, "reworded")

    def test_no_refs_is_orphan(self) -> None:
        self.assertEqual(classify_origin("Anything", [], self.known), "orphan")

    def test_only_unknown_refs_is_orphan(self) -> None:
        self.assertEqual(classify_origin("Anything", ["nope"], self.known), "orphan")


class TestAssemble(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        self.ref1 = bullet_id(self.truth_base, 0, 1)
        self.ref_old = bullet_id(self.truth_base, 1, 0)

    def test_titles_companies_and_dates_come_from_the_truth_base(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[
                        TailorBullet(text="Built dbt models", evidence_refs=[self.ref0])
                    ],
                )
            ]
        )
        document = assemble(self.truth_base, output, target_title=TITLE)
        role = document.experience[0]
        self.assertEqual(
            (role.company, role.title, role.start, role.end),
            ("Acme Bank", "Senior Data Engineer", "2019-01", None),
        )
        self.assertEqual(document.experience[1].company, "Beta Retail")

    def test_headline_and_target_title_are_the_injected_title(self) -> None:
        document = assemble(self.truth_base, TailorOutput(), target_title=TITLE)
        self.assertEqual(document.target_title, TITLE)
        self.assertEqual(document.headline, TITLE)

    def test_bullets_are_ordered_and_classified(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[
                        TailorBullet(
                            text="Migrated nightly batch jobs to Airflow",
                            evidence_refs=[self.ref1],
                        ),
                        TailorBullet(
                            text="Built dbt models powering risk reporting",
                            evidence_refs=[self.ref0],
                        ),
                        TailorBullet(text="Led a team of 12", evidence_refs=[]),
                    ],
                )
            ]
        )
        bullets = (
            assemble(self.truth_base, output, target_title=TITLE).experience[0].bullets
        )
        self.assertEqual(
            [b.origin for b in bullets], ["original", "reworded", "orphan"]
        )
        self.assertEqual(bullets[0].text, "Migrated nightly batch jobs to Airflow")

    def test_generated_text_is_cleaned(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[
                        TailorBullet(
                            text="• Built dbt models ✅", evidence_refs=[self.ref0]
                        )
                    ],
                )
            ]
        )
        bullet = (
            assemble(self.truth_base, output, target_title=TITLE)
            .experience[0]
            .bullets[0]
        )
        self.assertEqual(bullet.text, "Built dbt models")

    def test_an_omitted_role_keeps_its_original_bullets(self) -> None:
        output = TailorOutput(experience=[])
        document = assemble(self.truth_base, output, target_title=TITLE)
        self.assertEqual(
            [b.text for b in document.experience[1].bullets],
            ["Wrote SQL reports for the finance team"],
        )
        self.assertEqual(document.experience[1].bullets[0].origin, "original")
        self.assertEqual(
            document.experience[1].bullets[0].evidence_refs, [self.ref_old]
        )

    def test_a_role_with_an_empty_bullet_list_is_kept_without_bullets(self) -> None:
        output = TailorOutput(experience=[TailorExperience(truth_index=1, bullets=[])])
        document = assemble(self.truth_base, output, target_title=TITLE)
        self.assertEqual(len(document.experience), 2)
        self.assertEqual(document.experience[1].bullets, [])

    def test_an_out_of_range_role_index_is_ignored(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=99,
                    bullets=[TailorBullet(text="Ghost", evidence_refs=[self.ref0])],
                )
            ]
        )
        document = assemble(self.truth_base, output, target_title=TITLE)
        self.assertEqual(len(document.experience), 2)
        all_text = [b.text for e in document.experience for b in e.bullets]
        self.assertNotIn("Ghost", all_text)

    def test_the_first_of_a_duplicated_role_index_wins(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[TailorBullet(text="First", evidence_refs=[self.ref0])],
                ),
                TailorExperience(
                    truth_index=0,
                    bullets=[TailorBullet(text="Second", evidence_refs=[self.ref0])],
                ),
            ]
        )
        bullets = (
            assemble(self.truth_base, output, target_title=TITLE).experience[0].bullets
        )
        self.assertEqual([b.text for b in bullets], ["First"])

    def test_blank_bullets_are_dropped(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[TailorBullet(text="  ✅ ", evidence_refs=[self.ref0])],
                )
            ]
        )
        self.assertEqual(
            assemble(self.truth_base, output, target_title=TITLE).experience[0].bullets,
            [],
        )

    def test_skills_keep_the_models_order_and_drop_unknown_names(self) -> None:
        output = TailorOutput(skills=["airflow", "Kubernetes", "dbt", "AIRFLOW"])
        skills = assemble(self.truth_base, output, target_title=TITLE).skills
        self.assertEqual([s.name for s in skills], ["Airflow", "dbt"])
        self.assertEqual(skills[0].canonical_id, "zzfixture-skill-airflow")

    def test_empty_skills_fall_back_to_all_truth_base_skills(self) -> None:
        document = assemble(self.truth_base, TailorOutput(), target_title=TITLE)
        self.assertEqual([s.name for s in document.skills], ["dbt", "Airflow", "SQL"])

    def test_a_summary_matching_the_truth_base_is_original(self) -> None:
        output = TailorOutput(
            summary=TailorSummary(
                text="Data engineer with eight years building analytics pipelines.",
                evidence_refs=[],
            )
        )
        summary = assemble(self.truth_base, output, target_title=TITLE).summary
        self.assertEqual(summary.origin, "original")

    def test_a_new_summary_with_a_valid_ref_is_reworded(self) -> None:
        output = TailorOutput(
            summary=TailorSummary(
                text="Pipeline specialist.", evidence_refs=[self.ref0]
            )
        )
        self.assertEqual(
            assemble(self.truth_base, output, target_title=TITLE).summary.origin,
            "reworded",
        )

    def test_a_new_summary_with_no_ref_is_an_orphan(self) -> None:
        output = TailorOutput(
            summary=TailorSummary(text="World-class leader.", evidence_refs=[])
        )
        self.assertEqual(
            assemble(self.truth_base, output, target_title=TITLE).summary.origin,
            "orphan",
        )

    def test_no_model_summary_falls_back_to_the_truth_base_summary(self) -> None:
        summary = assemble(self.truth_base, TailorOutput(), target_title=TITLE).summary
        self.assertEqual(summary.origin, "original")
        self.assertEqual(summary.text, self.truth_base.summary)

    def test_static_sections_are_copied_verbatim(self) -> None:
        document = assemble(self.truth_base, TailorOutput(), target_title=TITLE)
        self.assertEqual(document.education, self.truth_base.education)
        self.assertEqual(document.identity, "Zz Fixture")
        self.assertEqual(document.email, "zz@example.com")


if __name__ == "__main__":
    unittest.main()
