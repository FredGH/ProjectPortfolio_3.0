"""Integration tests for core.skills.esco_embed against live Postgres."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    axis_vector,
    live_owner_engine,
    purge_fixtures,
)

from core.skills.esco_embed import embed_esco_skills, embedding_coverage
from core.skills.esco_load import load_esco

_IDS = ["fixture-cloud", "fixture-python", "fixture-sql"]
_VECTORS = {
    "zzfixture cloud technologies": axis_vector(0),
    "zzfixture python": axis_vector(1),
    "zzfixture sql": axis_vector(2),
}


class TestEmbedEscoSkills(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        self.calls: list[str] = []

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _embed(self, text_: str) -> list[float]:
        self.calls.append(text_)
        return _VECTORS[text_]

    def _run(self, model: str) -> int:
        return embed_esco_skills(
            self.engine, embed=self._embed, model=model, skill_ids=_IDS
        )

    def _stored(self) -> dict[str, str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT skill_id, embedding_model FROM esco.skill_embedding "
                    "WHERE skill_id LIKE 'fixture-%'"
                )
            ).all()
        return {r.skill_id: r.embedding_model for r in rows}

    def test_embeds_each_skills_preferred_label(self) -> None:
        written = self._run("zzfixture-model-a")
        self.assertEqual(written, 3)
        self.assertEqual(sorted(self.calls), sorted(_VECTORS))
        self.assertEqual(set(self._stored().values()), {"zzfixture-model-a"})

    def test_second_run_with_the_same_model_does_nothing(self) -> None:
        self._run("zzfixture-model-a")
        self.calls.clear()
        self.assertEqual(self._run("zzfixture-model-a"), 0)
        self.assertEqual(self.calls, [])

    def test_a_different_model_re_embeds_everything(self) -> None:
        self._run("zzfixture-model-a")
        self.assertEqual(self._run("zzfixture-model-b"), 3)
        self.assertEqual(set(self._stored().values()), {"zzfixture-model-b"})

    def test_wrong_dimension_raises_and_writes_nothing(self) -> None:
        with self.assertRaises(ValueError):
            embed_esco_skills(
                self.engine,
                embed=lambda _text: [1.0, 2.0],
                model="zzfixture-model-a",
                skill_ids=_IDS,
            )
        self.assertEqual(self._stored(), {})


class TestEmbeddingCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _coverage(self) -> tuple[int, int]:
        with self.engine.connect() as conn:
            return embedding_coverage(conn, "zzfixture-model")

    def test_counts_every_skill_but_only_embeddings_from_the_given_model(
        self,
    ) -> None:
        total, embedded = self._coverage()
        self.assertGreaterEqual(total, 3)
        self.assertEqual(embedded, 0)
        embed_esco_skills(
            self.engine,
            embed=lambda label: _VECTORS[label],
            model="zzfixture-model",
            skill_ids=_IDS,
        )
        total_after, embedded_after = self._coverage()
        self.assertEqual(total_after, total)
        self.assertEqual(embedded_after, 3)


if __name__ == "__main__":
    unittest.main()
