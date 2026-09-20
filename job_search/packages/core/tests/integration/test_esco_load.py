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

    def test_reload_drops_relations_removed_from_the_release(self) -> None:
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            for source in FIXTURE_ESCO_DIR.glob("*.csv"):
                shutil.copy(source, release / source.name)
            relations_path = release / "occupationSkillRelations_en.csv"
            kept = [
                line
                for line in relations_path.read_text().splitlines(keepends=True)
                if "fixture-occ-data-engineer" not in line
                or not line.rstrip().endswith("fixture-sql")
            ]
            # Header + 5 relation rows, minus the one dropped from the release.
            self.assertEqual(len(kept), 5)
            relations_path.write_text("".join(kept))
            counts = load_esco(self.engine, release)
        self.assertEqual(counts.occupation_skills, 3)
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.occupation_skill "
                "WHERE occupation_id = 'fixture-occ-data-engineer' "
                "AND skill_id = 'fixture-sql'"
            ),
            0,
        )
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.occupation_skill "
                "WHERE occupation_id LIKE 'fixture-%'"
            ),
            3,
        )

    def test_reload_leaves_skills_and_occupations_outside_the_release_alone(
        self,
    ) -> None:
        other_skill_uri = "http://data.europa.eu/esco/skill/fixture-other-skill"
        other_occ_uri = "http://data.europa.eu/esco/occupation/fixture-other-occ"
        # The release must already be loaded so `fixture-python` exists for the
        # cross-release relation below.
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill (skill_id, concept_uri, preferred_label) "
                    "VALUES ('fixture-other-skill', :uri, 'zzfixture other skill')"
                ),
                {"uri": other_skill_uri},
            )
            conn.execute(
                text(
                    "INSERT INTO esco.skill_label "
                    "(skill_id, label, label_norm, is_preferred) VALUES "
                    "('fixture-other-skill', 'zzfixture other skill', "
                    "'zzfixture other skill', TRUE)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO esco.occupation "
                    "(occupation_id, concept_uri, preferred_label) "
                    "VALUES ('fixture-other-occ', :uri, 'zzfixture other occupation')"
                ),
                {"uri": other_occ_uri},
            )
            # One relation to a skill outside the release, one to a skill inside
            # it: neither may be deleted, because the occupation is not in it.
            conn.execute(
                text(
                    "INSERT INTO esco.occupation_skill "
                    "(occupation_id, skill_id, relation_type) VALUES "
                    "('fixture-other-occ', 'fixture-other-skill', 'essential'), "
                    "('fixture-other-occ', 'fixture-python', 'optional')"
                )
            )
        # A second load must leave the unrelated rows exactly as they were.
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.skill WHERE skill_id = 'fixture-other-skill'"
            ),
            1,
        )
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.skill_label "
                "WHERE skill_id = 'fixture-other-skill' "
                "AND label_norm = 'zzfixture other skill'"
            ),
            1,
        )
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.occupation "
                "WHERE occupation_id = 'fixture-other-occ'"
            ),
            1,
        )
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.occupation_skill "
                "WHERE occupation_id = 'fixture-other-occ'"
            ),
            2,
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
