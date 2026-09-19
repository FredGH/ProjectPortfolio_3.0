"""Integration tests for core.skills.mapper.map_skill against live Postgres."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    axis_vector,
    live_owner_engine,
    purge_fixtures,
    sparse_vector,
)

from core.settings import get_settings
from core.skills.aliases import sync_seed_aliases
from core.skills.esco_load import load_esco
from core.skills.mapper import EMBEDDING_ACCEPT_COSINE, map_skill
from core.skills.vector import to_pgvector

_MODEL = get_settings().embedding_model


def _no_embed(_text: str) -> list[float]:
    raise AssertionError("the embedding stage must not run for this input")


class TestMapSkill(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.begin() as conn:
            for skill_id, axis in (("fixture-cloud", 0), ("fixture-python", 1)):
                conn.execute(
                    text(
                        "INSERT INTO esco.skill_embedding "
                        "(skill_id, embedding_model, embedding) "
                        "VALUES (:id, :model, CAST(:v AS vector))"
                    ),
                    {
                        "id": skill_id,
                        "model": _MODEL,
                        "v": to_pgvector(axis_vector(axis)),
                    },
                )

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _map(self, raw, embed=_no_embed):
        with self.engine.connect() as conn:
            return map_skill(conn, raw, embed=embed, embedding_model=_MODEL)

    def test_esco_label_match_is_case_and_spacing_insensitive(self) -> None:
        match = self._map("  ZZFixture   Cloud Platforms ")
        self.assertEqual((match.skill_id, match.method), ("fixture-cloud", "label"))

    def test_a_preferred_label_match_wins_a_label_tie(self) -> None:
        # "zzfixture cloud computing" is a non-preferred alt label of
        # fixture-cloud; make it the *preferred* label of fixture-python.
        # fixture-cloud sorts first by id, so only is_preferred can make
        # fixture-python win.
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_label (skill_id, label, label_norm, "
                    "is_preferred) VALUES ('fixture-python', 'zzfixture cloud "
                    "computing', 'zzfixture cloud computing', true)"
                )
            )
        self.assertEqual(
            self._map("zzfixture cloud computing").skill_id, "fixture-python"
        )

    def test_equally_preferred_label_ties_pick_the_smallest_skill_id(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_label (skill_id, label, label_norm, "
                    "is_preferred) VALUES ('fixture-python', 'zzfixture cloud "
                    "computing', 'zzfixture cloud computing', false)"
                )
            )
        self.assertEqual(
            self._map("zzfixture cloud computing").skill_id, "fixture-cloud"
        )

    def test_alias_outranks_an_esco_label(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
                    "VALUES ('custom:fixture-alias', 'A')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
                    "VALUES ('zzfixture cloud platforms', "
                    "'custom:fixture-alias', 'seed')"
                )
            )
        match = self._map("ZZFixture Cloud Platforms")
        self.assertEqual(
            (match.skill_id, match.method), ("custom:fixture-alias", "alias")
        )

    def test_embedding_match_at_or_above_threshold_is_accepted(self) -> None:
        match = self._map("zzfixture novel phrase", embed=lambda _t: axis_vector(0))
        self.assertEqual((match.skill_id, match.method), ("fixture-cloud", "embedding"))
        self.assertAlmostEqual(match.score, 1.0, places=5)

    def test_embedding_match_below_threshold_is_unmapped_with_a_candidate(self) -> None:
        # cosine to fixture-cloud's axis-0 vector is exactly 0.6
        vector = sparse_vector({0: 0.6, 767: 0.8})
        match = self._map("zzfixture other phrase", embed=lambda _t: vector)
        self.assertLess(0.6, EMBEDDING_ACCEPT_COSINE)
        self.assertEqual((match.skill_id, match.method), (None, "none"))
        self.assertEqual(match.candidate_skill_id, "fixture-cloud")
        self.assertAlmostEqual(match.candidate_score, 0.6, places=5)

    def test_blank_input_is_unmapped_without_embedding(self) -> None:
        match = self._map(" - ")
        self.assertEqual((match.skill_id, match.method), (None, "none"))


class TestGcpAcceptance(unittest.TestCase):
    """PLAN.md Step 14 "Done when": all three spellings resolve to one ID."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()
        sync_seed_aliases(cls.engine)  # idempotent; real seed data by design

    def test_gcp_google_cloud_and_google_cloud_platform_resolve_to_one_id(self) -> None:
        ids = set()
        with self.engine.connect() as conn:
            for raw in ("GCP", "Google Cloud", "Google Cloud Platform"):
                match = map_skill(conn, raw, embed=_no_embed, embedding_model=_MODEL)
                self.assertEqual(match.method, "alias", raw)
                ids.add(match.skill_id)
        self.assertEqual(ids, {"custom:google-cloud-platform"})


if __name__ == "__main__":
    unittest.main()
