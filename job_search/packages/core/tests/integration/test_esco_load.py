"""Integration tests for core.skills.esco_load against live Postgres."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text
from tests.integration.skills_fixtures import FIXTURE_ESCO_DIR, live_owner_engine

from core.skills.esco_load import EscoLoadError, concept_id, load_esco


def _purge_esco_fixtures(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM esco.occupation WHERE occupation_id LIKE 'fixture-%'")
        )
        conn.execute(text("DELETE FROM esco.skill WHERE skill_id LIKE 'fixture-%'"))


class TestConceptId(unittest.TestCase):
    def test_returns_trailing_uri_segment(self) -> None:
        self.assertEqual(
            concept_id("http://data.europa.eu/esco/skill/abc-123"), "abc-123"
        )

    def test_ignores_a_trailing_slash(self) -> None:
        self.assertEqual(concept_id("http://x/skill/abc/"), "abc")


class TestLoadEsco(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        _purge_esco_fixtures(self.engine)

    def tearDown(self) -> None:
        _purge_esco_fixtures(self.engine)

    def _count(self, sql: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(text(sql)).scalar_one()

    def test_loads_the_fixture_release_and_reports_counts(self) -> None:
        counts = load_esco(self.engine, FIXTURE_ESCO_DIR)
        self.assertEqual(counts.skills, 3)
        self.assertEqual(counts.skill_labels, 7)
        self.assertEqual(counts.occupations, 2)
        self.assertEqual(counts.occupation_skills, 4)
        self.assertEqual(counts.skipped_relations, 1)

    def test_labels_are_normalised_and_flag_the_preferred_one(self) -> None:
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT label_norm, is_preferred FROM esco.skill_label "
                    "WHERE skill_id = 'fixture-cloud' ORDER BY label_norm"
                )
            ).all()
        self.assertEqual(
            [(r.label_norm, r.is_preferred) for r in rows],
            [
                ("zzfixture cloud computing", False),
                ("zzfixture cloud platforms", False),
                ("zzfixture cloud technologies", True),
            ],
        )

    def test_skill_id_is_the_concept_uri_tail(self) -> None:
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.connect() as conn:
            uri = conn.execute(
                text(
                    "SELECT concept_uri FROM esco.skill WHERE skill_id = 'fixture-sql'"
                )
            ).scalar_one()
        self.assertEqual(uri, "http://data.europa.eu/esco/skill/fixture-sql")

    def test_is_idempotent(self) -> None:
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.skill WHERE skill_id LIKE 'fixture-%'"
            ),
            3,
        )
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.skill_label WHERE skill_id LIKE 'fixture-%'"
            ),
            7,
        )

    def test_reload_drops_labels_removed_from_the_release(self) -> None:
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            for source in FIXTURE_ESCO_DIR.glob("*.csv"):
                shutil.copy(source, release / source.name)
            skills = (release / "skills_en.csv").read_text()
            skills = skills.replace(
                "zzfixture cloud platforms\nzzfixture cloud computing",
                "zzfixture cloud computing",
            )
            (release / "skills_en.csv").write_text(skills)
            load_esco(self.engine, release)
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.skill_label "
                "WHERE label_norm = 'zzfixture cloud platforms'"
            ),
            0,
        )

    def test_missing_file_raises_naming_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(EscoLoadError) as ctx:
                load_esco(self.engine, Path(tmp))
        self.assertIn("skills_en.csv", str(ctx.exception))

    def test_missing_column_raises_listing_the_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            for source in FIXTURE_ESCO_DIR.glob("*.csv"):
                shutil.copy(source, release / source.name)
            (release / "skills_en.csv").write_text("conceptUri,preferredLabel\nx,y\n")
            with self.assertRaises(EscoLoadError) as ctx:
                load_esco(self.engine, release)
        self.assertIn("altLabels", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
