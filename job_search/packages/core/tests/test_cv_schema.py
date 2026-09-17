"""CVTruthBase round-trips through JSON without losing data."""

from __future__ import annotations

import unittest

from core.cv.schema import (
    Bullet,
    Certification,
    CVTruthBase,
    Experience,
    Project,
    Publication,
    Skill,
)


class TestCVTruthBaseRoundTrip(unittest.TestCase):
    def test_round_trips_to_json_and_back(self) -> None:
        original = CVTruthBase(
            identity="Jane Doe",
            headline="Senior Data Engineer",
            email="jane.doe@example.com",
            phone="+44 7700 900000",
            linkedin_url="https://linkedin.com/in/janedoe",
            nationality="British",
            summary="Data engineer with 10 years of experience.",
            locations=["London, UK"],
            work_auth="UK citizen",
            skills=[Skill(name="Python", years=10.0)],
            experience=[
                Experience(
                    company="Acme Corp",
                    title="Data Engineer",
                    start="2020-01",
                    end=None,
                    bullets=[Bullet(bullet_id="abc123", text="Built a pipeline.")],
                    tech=["Python", "Postgres"],
                    metrics=["50% faster"],
                )
            ],
            qualifications=[Certification(name="AI Engineering Track", year=2025)],
            publications=[
                Publication(
                    citation="On XLE Index Construction, Springer",
                    authors=["Smith J.", "Doe A."],
                    year=2019,
                )
            ],
            projects=[
                Project(
                    name="CV Intelligence Agent",
                    description="LLM-powered chatbot answering career questions.",
                    tech=["Python", "LangChain"],
                    url="https://example.com/cv-agent",
                )
            ],
            activities_interests=["Mentor at MyJobGlasses"],
        )
        restored = CVTruthBase.model_validate_json(original.model_dump_json())
        self.assertEqual(restored, original)

    def test_optional_fields_default_to_empty(self) -> None:
        minimal = CVTruthBase(identity="Jane Doe", headline="Engineer")
        self.assertEqual(minimal.skills, [])
        self.assertEqual(minimal.experience, [])
        self.assertIsNone(minimal.work_auth)
        self.assertIsNone(minimal.summary)
        self.assertIsNone(minimal.email)
        self.assertIsNone(minimal.phone)
        self.assertIsNone(minimal.linkedin_url)
        self.assertIsNone(minimal.nationality)
        self.assertEqual(minimal.qualifications, [])
        self.assertEqual(minimal.projects, [])
        self.assertEqual(minimal.activities_interests, [])


if __name__ == "__main__":
    unittest.main()
