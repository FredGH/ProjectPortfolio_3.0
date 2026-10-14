"""Unit tests for the CV directory layout and the job spec file."""

from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from core.render.layout import cv_directory, job_ui_link, render_job_spec
from core.tailoring.schema import JobContext, JobSkill, JobSource


def _job(**overrides: object) -> JobContext:
    base = {
        "job_group_id": "abc123",
        "title_for_display": "Data Engineer",
        "company": "Sanderson ",
        "description": "Build pipelines.\nUse dbt.",
        "skills": [
            JobSkill("s1", "dbt", "must_have"),
            JobSkill("s2", "Airflow", "nice_to_have"),
            JobSkill("s3", "SQL", None),
        ],
    }
    return JobContext(**{**base, **overrides})  # type: ignore[arg-type]


class TestCvDirectory(unittest.TestCase):
    def test_path_is_person_month_title_company(self) -> None:
        path = cv_directory(
            Path("output"),
            "Frederic Marechal",
            datetime(2026, 10, 13),
            "Data Engineer",
            "Sanderson ",
        )
        self.assertEqual(
            path, Path("output/frederic_marechal/2026-10/data_engineer/sanderson")
        )

    def test_unsafe_characters_never_reach_the_path(self) -> None:
        path = cv_directory(
            Path("out"), "Zoë O'Neil", datetime(2026, 1, 2), "Lead/Staff ../Eng", "A&B Ltd."
        )
        self.assertEqual(
            path, Path("out/zoe_o_neil/2026-01/lead_staff_eng/a_b_ltd")
        )

    def test_missing_parts_get_a_placeholder_not_an_empty_folder(self) -> None:
        path = cv_directory(Path("out"), "", datetime(2026, 3, 4), "  ", None)
        self.assertEqual(
            path, Path("out/unknown_candidate/2026-03/untitled/unknown_company")
        )


class TestJobSpec(unittest.TestCase):
    def test_it_carries_the_url_the_ui_link_skills_and_description(self) -> None:
        source = JobSource(
            apply_url="https://www.reed.co.uk/jobs/x/1",
            source_urls=("https://www.reed.co.uk/jobs/x/1", "https://other/2"),
            source_names=("reed",),
            location="Cardiff",
            posted_at="2026-09-03",
        )
        link = job_ui_link("http://localhost:8501/", "abc123")
        text = render_job_spec(_job(), source, link)
        self.assertEqual(link, "http://localhost:8501/Tailored_CV_Review?job=abc123")
        self.assertIn("# Data Engineer — Sanderson\n", text)
        self.assertIn("**Job posting:** https://www.reed.co.uk/jobs/x/1", text)
        self.assertIn("**Also seen at:** https://other/2", text)
        self.assertIn(f"**In the UI:** {link}", text)
        self.assertIn("**Location:** Cardiff", text)
        self.assertIn("## Skills — Must have\n\ndbt", text)
        self.assertIn("## Skills — Nice to have\n\nAirflow", text)
        self.assertIn("## Skills — Other\n\nSQL", text)
        self.assertIn("Build pipelines.\nUse dbt.", text)

    def test_a_job_with_no_recorded_url_says_so(self) -> None:
        text = render_job_spec(_job(skills=[], description=""), JobSource(), "http://u")
        self.assertIn("**Job posting:** not recorded", text)
        self.assertIn("(none recorded)", text)
        self.assertNotIn("## Skills", text)

    def test_the_apply_url_leads_when_the_sources_do_not_list_it(self) -> None:
        source = JobSource(apply_url="https://apply/1", source_urls=("https://seen/2",))
        text = render_job_spec(_job(), source, "http://u")
        self.assertIn("**Job posting:** https://apply/1", text)
        self.assertIn("**Also seen at:** https://seen/2", text)


if __name__ == "__main__":
    unittest.main()
