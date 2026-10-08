"""Unit tests for building the render model from a tailored document."""

from __future__ import annotations

import unittest

from tests.render_fixtures import make_tailored_document

from core.cv.schema import Education, Project, Publication, Skill
from core.render.model import build_render_doc
from core.tailoring.schema import TailoredBullet, TailoredExperience, TailoredSummary


def _texts(doc, kind):  # type: ignore[no-untyped-def]
    return [b.text for b in doc.blocks if b.kind == kind]


class TestBuildRenderDoc(unittest.TestCase):
    def test_header_blocks_come_first_in_order(self) -> None:
        doc = build_render_doc(make_tailored_document())
        self.assertEqual(
            [b.kind for b in doc.blocks[:3]], ["name", "headline", "contact"]
        )
        self.assertEqual(doc.blocks[0].text, "Zz Fixture")
        self.assertEqual(doc.blocks[1].text, "Lead Data Engineer")
        self.assertEqual(
            doc.blocks[2].text,
            "zz@example.com | +00 000 000 000 | linkedin.com/in/zzfixture"
            " | Fixtureland",
        )

    def test_sections_use_the_standard_headings_in_order(self) -> None:
        doc = build_render_doc(make_tailored_document())
        self.assertEqual(
            doc.headings(),
            ["Summary", "Skills", "Experience", "Education", "Certifications"],
        )

    def test_roles_render_title_then_company_and_dates(self) -> None:
        doc = build_render_doc(make_tailored_document())
        self.assertEqual(
            _texts(doc, "role_title"), ["Senior Data Engineer", "Data Analyst"]
        )
        self.assertEqual(
            _texts(doc, "role_meta"),
            ["Acme Bank, 01/2019 – Present", "Beta Retail, 06/2015 – 12/2018"],
        )

    def test_skills_are_one_comma_separated_paragraph(self) -> None:
        doc = build_render_doc(make_tailored_document())
        self.assertIn("dbt, Airflow, SQL", _texts(doc, "paragraph"))

    def test_education_and_certification_lines(self) -> None:
        doc = build_render_doc(make_tailored_document())
        paragraphs = _texts(doc, "paragraph")
        self.assertIn("BSc, Zz University, 2011 – 2014", paragraphs)
        self.assertIn("Fixture Cert (2020)", paragraphs)

    def test_acronyms_expand_on_first_use_only_across_sections(self) -> None:
        doc = build_render_doc(make_tailored_document())
        summary = _texts(doc, "paragraph")[0]
        self.assertEqual(
            summary,
            "Engineer building ELT (Extract, Load, Transform) pipelines "
            "on GCP (Google Cloud Platform).",
        )
        self.assertIn(
            "Ran the ELT platform and wrote an ETL (Extract, Transform, Load) guide",
            _texts(doc, "bullet"),
        )

    def test_empty_optional_sections_have_no_heading(self) -> None:
        doc = build_render_doc(
            make_tailored_document(
                summary=None, education=[], qualifications=[], skills=[]
            )
        )
        self.assertEqual(doc.headings(), ["Experience"])

    def test_blank_target_title_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_render_doc(make_tailored_document(target_title="  "))

    def test_whitespace_in_text_is_collapsed(self) -> None:
        experience = [
            TailoredExperience(
                truth_index=0,
                company="Acme",
                title="Engineer",
                bullets=[
                    TailoredBullet(text="Built   dbt\nmodels\t", origin="original")
                ],
            )
        ]
        doc = build_render_doc(make_tailored_document(experience=experience))
        self.assertEqual(_texts(doc, "bullet"), ["Built dbt models"])

    def test_title_company_and_contact_are_never_expanded(self) -> None:
        experience = [
            TailoredExperience(
                truth_index=0, company="GCP Ltd", title="GCP Engineer", bullets=[]
            )
        ]
        doc = build_render_doc(
            make_tailored_document(
                target_title="ELT Engineer & Co",
                headline="ELT Engineer & Co",
                summary=TailoredSummary(text="Plain.", origin="original"),
                experience=experience,
            )
        )
        self.assertEqual(doc.blocks[1].text, "ELT Engineer & Co")
        self.assertEqual(_texts(doc, "role_title"), ["GCP Engineer"])
        self.assertEqual(_texts(doc, "role_meta"), ["GCP Ltd"])

    def test_skills_are_not_acronym_expanded_so_the_list_stays_parseable(
        self,
    ) -> None:
        doc = build_render_doc(
            make_tailored_document(skills=[Skill(name="ELT"), Skill(name="GCP")])
        )
        self.assertIn("ELT, GCP", _texts(doc, "paragraph"))
        self.assertTrue(
            _texts(doc, "paragraph")[0].startswith(
                "Engineer building ELT (Extract, Load, Transform)"
            )
        )

    def test_a_degree_with_no_end_date_does_not_claim_present(self) -> None:
        doc = build_render_doc(
            make_tailored_document(
                education=[
                    Education(
                        institution="Zz University", qualification="BSc", start="2011"
                    )
                ]
            )
        )
        self.assertIn("BSc, Zz University, 2011", _texts(doc, "paragraph"))

    def test_control_characters_are_removed(self) -> None:
        experience = [
            TailoredExperience(
                truth_index=0,
                company="Acme",
                title="Engineer",
                bullets=[TailoredBullet(text="a\x00b\x01c", origin="original")],
            )
        ]
        doc = build_render_doc(
            make_tailored_document(
                experience=experience,
                target_title="Lead\x01 Engineer",
                headline="Lead\x01 Engineer",
            )
        )
        self.assertEqual(_texts(doc, "bullet"), ["abc"])
        self.assertEqual(doc.title, "Lead Engineer")

    def test_a_title_with_extra_whitespace_is_normalised_in_one_place(self) -> None:
        doc = build_render_doc(
            make_tailored_document(target_title=" Lead  Data\tEngineer ", headline="x")
        )
        self.assertEqual(doc.title, "Lead Data Engineer")
        self.assertEqual(doc.blocks[1].text, doc.title)

    def test_projects_and_publications_follow_experience_before_education(
        self,
    ) -> None:
        doc = build_render_doc(
            make_tailored_document(
                projects=[Project(name="Fixture Pipeline", description="Open ETL")],
                publications=[Publication(citation="Fixture, Z. (2020). A paper.")],
            )
        )
        self.assertEqual(
            doc.headings(),
            [
                "Summary",
                "Skills",
                "Experience",
                "Projects",
                "Publications",
                "Education",
                "Certifications",
            ],
        )
        paragraphs = _texts(doc, "paragraph")
        self.assertIn("Fixture Pipeline: Open ETL", paragraphs)
        self.assertIn("Fixture, Z. (2020). A paper.", paragraphs)


if __name__ == "__main__":
    unittest.main()
