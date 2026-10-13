"""Unit tests for building the render model from a tailored document."""

from __future__ import annotations

import unittest

from tests.render_fixtures import make_tailored_document

from core.cv.schema import (
    Activity,
    Certification,
    Education,
    Project,
    Publication,
    Skill,
)
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
            [
                "PROFESSIONAL SUMMARY",
                "CORE TECHNICAL SKILLS",
                "WORK EXPERIENCE",
                "EDUCATION",
                "PROFESSIONAL QUALIFICATIONS & CONTINUOUS PERSONAL DEVELOPMENT",
            ],
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

    def test_summary_highlights_get_bold_spans(self) -> None:
        base = make_tailored_document()
        summary = base.summary.model_copy(update={"highlights": ["pipelines"]})
        doc = build_render_doc(make_tailored_document(summary=summary))
        block = next(b for b in doc.blocks if b.kind == "paragraph" and "pipelines" in b.text)
        start, end = block.bold_spans[0]
        self.assertEqual(block.text[start:end], "pipelines")

    def test_a_bold_phrase_survives_an_acronym_expansion_inside_it(self) -> None:
        summary = make_tailored_document().summary.model_copy(
            update={
                "text": "Built the ETL pipelines and the dbt models.",
                "highlights": ["ETL pipelines", "dbt models"],
            }
        )
        doc = build_render_doc(make_tailored_document(summary=summary))
        block = next(b for b in doc.blocks if "dbt models" in b.text)
        bold = [block.text[s:e] for s, e in block.bold_spans]
        self.assertEqual(bold, ["ETL (Extract, Transform, Load) pipelines", "dbt models"])

    def test_highlighted_skills_get_bold_spans(self) -> None:
        doc = build_render_doc(make_tailored_document(highlighted_skills=["SQL", "dbt"]))
        block = next(b for b in doc.blocks if b.text == "dbt, Airflow, SQL")
        self.assertEqual(block.bold_spans, ((0, 3), (14, 17)))

    def test_education_and_certification_lines(self) -> None:
        doc = build_render_doc(make_tailored_document())
        paragraphs = _texts(doc, "paragraph")
        self.assertIn("BSc - Zz University (2011–2014)", paragraphs)
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
        self.assertEqual(doc.headings(), ["WORK EXPERIENCE"])

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
        self.assertIn("BSc - Zz University (2011)", _texts(doc, "paragraph"))

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
                "PROFESSIONAL SUMMARY",
                "CORE TECHNICAL SKILLS",
                "WORK EXPERIENCE",
                "PERSONAL PROJECTS",
                "PUBLICATIONS",
                "EDUCATION",
                "PROFESSIONAL QUALIFICATIONS & CONTINUOUS PERSONAL DEVELOPMENT",
            ],
        )
        paragraphs = _texts(doc, "paragraph")
        self.assertIn("Fixture Pipeline", _texts(doc, "project_title"))
        self.assertIn("Open ETL", _texts(doc, "bullet"))
        self.assertIn("Fixture, Z. (2020). A paper.", paragraphs)

    def test_the_contact_line_leaves_out_the_location(self) -> None:
        doc = build_render_doc(make_tailored_document(locations=["London, UK"]))
        self.assertNotIn("London", doc.blocks[2].text)
        self.assertIn("Fixtureland", doc.blocks[2].text)

    def test_spacing_before_punctuation_is_tidied_in_body_text(self) -> None:
        experience = [
            TailoredExperience(
                truth_index=0,
                company="Acme",
                title="Engineer",
                bullets=[
                    TailoredBullet(
                        text="Used ( ClaudeAI ) tooling , quickly .", origin="original"
                    )
                ],
            )
        ]
        doc = build_render_doc(make_tailored_document(experience=experience))
        self.assertEqual(_texts(doc, "bullet"), ["Used (ClaudeAI) tooling, quickly."])

    def test_the_title_keeps_its_own_spacing(self) -> None:
        doc = build_render_doc(
            make_tailored_document(
                target_title="Lead ( Data ) Engineer", headline="Lead ( Data ) Engineer"
            )
        )
        self.assertEqual(doc.blocks[1].text, "Lead ( Data ) Engineer")

    def test_a_role_line_marks_its_dates_as_muted(self) -> None:
        doc = build_render_doc(make_tailored_document())
        meta = [b for b in doc.blocks if b.kind == "role_meta"]
        self.assertEqual(meta[0].muted, "01/2019 – Present")
        self.assertTrue(meta[0].text.endswith(meta[0].muted))
        self.assertEqual(meta[1].muted, "06/2015 – 12/2018")

    def test_qualifications_are_one_comma_separated_line(self) -> None:
        doc = build_render_doc(
            make_tailored_document(
                qualifications=[
                    Certification(name="Fixture Cert", year=2020),
                    Certification(name="Other Cert", year=2021),
                    Certification(name="No Year Cert"),
                ]
            )
        )
        self.assertIn(
            "Fixture Cert (2020), Other Cert (2021), No Year Cert",
            _texts(doc, "paragraph"),
        )

    def test_activities_are_one_comma_separated_line_with_bracketed_years(
        self,
    ) -> None:
        doc = build_render_doc(
            make_tailored_document(
                activities=[
                    Activity(
                        name="Board Advisor",
                        organisation="ValueTechFactor",
                        start="2023",
                        end="2024",
                    ),
                    Activity(
                        name="Mentor",
                        organisation="MyJobGlasses",
                        start="2020",
                        end="Present",
                    ),
                    Activity(name="Speaker", start="2018"),
                ]
            )
        )
        self.assertIn(
            "Board Advisor at ValueTechFactor (2023–2024), "
            "Mentor at MyJobGlasses (2020–present), Speaker (2018)",
            _texts(doc, "paragraph"),
        )

    def test_education_is_one_comma_separated_line_like_the_template(self) -> None:
        doc = build_render_doc(
            make_tailored_document(
                education=[
                    Education(
                        institution="University of London",
                        qualification="MSc in Data Science (Distinction)",
                        start="2016",
                        end="2017",
                        grade="Distinction",
                    ),
                    Education(
                        institution="The Open University",
                        qualification="MSc in Software Development",
                        start="2002",
                        end="2008",
                        grade="First",
                    ),
                ]
            )
        )
        self.assertIn(
            "MSc in Data Science (Distinction) - University of London (2016–2017), "
            "MSc in Software Development (First) - The Open University (2002–2008)",
            _texts(doc, "paragraph"),
        )

    def test_each_publication_is_a_title_line_then_an_authors_and_venue_line(
        self,
    ) -> None:
        doc = build_render_doc(
            make_tailored_document(
                publications=[
                    Publication(
                        citation="A Title",
                        authors=["Olaniyan R.", "Stamate D.", "… & Fixture Z."],
                        conference="10th Int. Conf. on Fixtures, Springer.",
                    ),
                    Publication(
                        citation="Second Title",
                        authors=["Fixture Z. et al."],
                        conference="Journal of Fixtures",
                        year=2021,
                    ),
                ]
            )
        )
        paragraphs = _texts(doc, "paragraph")
        start = paragraphs.index("A Title")
        self.assertEqual(
            paragraphs[start : start + 4],
            [
                "A Title",
                "Olaniyan R., Stamate D., … & Fixture Z. — "
                "10th Int. Conf. on Fixtures, Springer.",
                "Second Title",
                "Fixture Z. et al. — Journal of Fixtures, 2021",
            ],
        )

    def test_a_publication_with_only_authors_or_only_a_venue_has_no_dangling_dash(
        self,
    ) -> None:
        doc = build_render_doc(
            make_tailored_document(
                publications=[
                    Publication(citation="Only Authors", authors=["Fixture Z."]),
                    Publication(citation="Only Venue", conference="A Venue"),
                    Publication(citation="Neither"),
                ]
            )
        )
        paragraphs = _texts(doc, "paragraph")
        start = paragraphs.index("Only Authors")
        self.assertEqual(
            paragraphs[start : start + 5],
            ["Only Authors", "Fixture Z.", "Only Venue", "A Venue", "Neither"],
        )

    def test_a_project_is_a_title_line_with_its_link_then_one_bullet_per_line(
        self,
    ) -> None:
        doc = build_render_doc(
            make_tailored_document(
                projects=[
                    Project(
                        name="AI Projects Portfolio",
                        url="huggingface.co/spaces/Fixture/ai-projects-v1",
                        description="First item — does a thing.\nSecond item — more.",
                    )
                ]
            )
        )
        titles = [b for b in doc.blocks if b.kind == "project_title"]
        self.assertEqual(
            [b.text for b in titles],
            ["AI Projects Portfolio: huggingface.co/spaces/Fixture/ai-projects-v1"],
        )
        self.assertEqual(titles[0].link, "huggingface.co/spaces/Fixture/ai-projects-v1")
        index = doc.blocks.index(titles[0])
        self.assertEqual(
            [(b.kind, b.text) for b in doc.blocks[index + 1 : index + 3]],
            [
                ("bullet", "First item — does a thing."),
                ("bullet", "Second item — more."),
            ],
        )

    def test_a_project_without_a_link_or_a_description_has_no_empty_parts(
        self,
    ) -> None:
        doc = build_render_doc(
            make_tailored_document(
                projects=[
                    Project(name="No Link", description="Only item."),
                    Project(name="No Description", url="example.com/x"),
                ]
            )
        )
        titles = [b for b in doc.blocks if b.kind == "project_title"]
        self.assertEqual(
            [b.text for b in titles], ["No Link", "No Description: example.com/x"]
        )
        self.assertEqual(titles[0].link, "")
        self.assertEqual(_texts(doc, "bullet").count("Only item."), 1)


if __name__ == "__main__":
    unittest.main()
