"""CVTruthBase round-trips through JSON without losing data."""

from __future__ import annotations

import unittest

from core.cv.schema import (
    Bullet,
    Certification,
    CVTruthBase,
    Experience,
    Project,
    Skill,
)


class TestCVTruthBaseRoundTrip(unittest.TestCase):
    def test_round_trips_to_json_and_back(self) -> None:
        original = CVTruthBase(
            identity="Jane Doe",
            headline="Senior Data Engineer",
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
            continuous_development=[
                Certification(name="AI Engineering Track", year=2025)
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
        self.assertEqual(minimal.continuous_development, [])
        self.assertEqual(minimal.projects, [])
        self.assertEqual(minimal.activities_interests, [])


if __name__ == "__main__":
    unittest.main()
