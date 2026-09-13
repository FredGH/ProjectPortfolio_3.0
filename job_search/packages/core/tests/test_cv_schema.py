"""CVTruthBase round-trips through JSON without losing data."""

from __future__ import annotations

import unittest

from core.cv.schema import Bullet, CVTruthBase, Experience, Skill


class TestCVTruthBaseRoundTrip(unittest.TestCase):
    def test_round_trips_to_json_and_back(self) -> None:
        original = CVTruthBase(
            identity="Jane Doe",
            headline="Senior Data Engineer",
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
        )
        restored = CVTruthBase.model_validate_json(original.model_dump_json())
        self.assertEqual(restored, original)

    def test_optional_fields_default_to_empty(self) -> None:
        minimal = CVTruthBase(identity="Jane Doe", headline="Engineer")
        self.assertEqual(minimal.skills, [])
        self.assertEqual(minimal.experience, [])
        self.assertIsNone(minimal.work_auth)


if __name__ == "__main__":
    unittest.main()
