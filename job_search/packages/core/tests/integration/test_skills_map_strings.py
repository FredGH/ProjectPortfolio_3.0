"""Integration tests for map_strings, map_pending and remap_unresolved."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    axis_vector,
    insert_job_skills,
    insert_mapping,
    live_owner_engine,
    purge_fixtures,
    sparse_vector,
)

from core.settings import get_settings
from core.skills.esco_load import load_esco
from core.skills.mapper import (
    EmbeddingModelMismatch,
    map_pending,
    map_strings,
    remap_unresolved,
)
from core.skills.vector import to_pgvector

_MODEL = get_settings().embedding_model


def _far_embed(_text: str) -> list[float]:
    """An embedding on an axis no fixture uses — never accepted."""
    return sparse_vector({767: 1.0})


class TestMapStrings(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_embedding "
                    "(skill_id, embedding_model, embedding) "
                    "VALUES ('fixture-cloud', :m, CAST(:v AS vector))"
                ),
                {"m": _MODEL, "v": to_pgvector(axis_vector(0))},
            )

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _row(self, raw_norm: str):
        with self.engine.connect() as conn:
            return conn.execute(
                text("SELECT * FROM silver.skill_mapping WHERE raw_norm = :n"),
                {"n": raw_norm},
            ).one_or_none()

    def test_writes_one_row_per_distinct_string_and_returns_the_skill_ids(self) -> None:
        result = map_strings(
            self.engine,
            [
                "ZZFixture Cloud Platforms",
                "zzfixture  cloud platforms",
                "zzfixture nope",
            ],
            embed=_far_embed,
            embedding_model=_MODEL,
        )
        self.assertEqual(
            result,
            {"zzfixture cloud platforms": "fixture-cloud", "zzfixture nope": None},
        )
        mapped = self._row("zzfixture cloud platforms")
        self.assertEqual((mapped.method, mapped.review_status), ("label", None))
        unmapped = self._row("zzfixture nope")
        self.assertEqual((unmapped.method, unmapped.review_status), ("none", "open"))

    def test_never_overwrites_an_existing_mapping(self) -> None:
        with self.engine.begin() as conn:
            insert_mapping(
                conn,
                "zzfixture resolved",
                skill_id="custom:fixture-x",
                method="alias",
                review_status="resolved",
            )
        result = map_strings(
            self.engine,
            ["zzfixture resolved"],
            embed=_far_embed,
            embedding_model=_MODEL,
        )
        self.assertEqual(result, {"zzfixture resolved": "custom:fixture-x"})
        self.assertEqual(self._row("zzfixture resolved").review_status, "resolved")

    def test_flags_an_existing_row_as_seen_in_cv(self) -> None:
        with self.engine.begin() as conn:
            insert_mapping(conn, "zzfixture cvskill")
        map_strings(
            self.engine,
            ["zzfixture cvskill"],
            embed=_far_embed,
            embedding_model=_MODEL,
            seen_in_cv=True,
        )
        self.assertTrue(self._row("zzfixture cvskill").seen_in_cv)

    def test_strings_that_normalise_to_nothing_are_skipped(self) -> None:
        self.assertEqual(
            map_strings(
                self.engine, ["  - "], embed=_far_embed, embedding_model=_MODEL
            ),
            {},
        )

    def test_an_implausibly_long_string_gets_no_row_and_no_embed_call(self) -> None:
        # An 8B model sometimes answers with a sentence instead of a skill.
        # It must not reach the review list, cost an embedding call, or (past
        # ~2.7 KB) overflow the job_skill_raw primary-key index.
        sentence = "zzfixture " + "long " * 60
        calls: list[str] = []

        def counting_embed(text_: str) -> list[float]:
            calls.append(text_)
            return _far_embed(text_)

        result = map_strings(
            self.engine,
            [sentence, "ZZFixture Cloud Platforms"],
            embed=counting_embed,
            embedding_model=_MODEL,
        )
        self.assertEqual(result, {"zzfixture cloud platforms": "fixture-cloud"})
        self.assertEqual(calls, [])
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT count(*) FROM silver.skill_mapping "
                    "WHERE raw_norm LIKE 'zzfixture long%'"
                )
            ).scalar_one()
        self.assertEqual(rows, 0)

    def test_refuses_when_stored_embeddings_use_a_different_model(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE esco.skill_embedding SET embedding_model = 'zzfixture-old' "
                    "WHERE skill_id = 'fixture-cloud'"
                )
            )
        with self.assertRaises(EmbeddingModelMismatch):
            map_strings(
                self.engine, ["zzfixture x"], embed=_far_embed, embedding_model=_MODEL
            )

    def test_a_failing_embed_keeps_the_chunks_already_committed(self) -> None:
        calls: list[str] = []

        def embed_then_fail(text_: str) -> list[float]:
            calls.append(text_)
            if len(calls) == 2:
                raise RuntimeError("ollama went away")
            return _far_embed(text_)

        with self.assertRaises(RuntimeError):
            map_strings(
                self.engine,
                ["zzfixture first novel", "zzfixture second novel"],
                embed=embed_then_fail,
                embedding_model=_MODEL,
                chunk_size=1,
            )
        self.assertEqual(len(calls), 2)
        first = self._row("zzfixture first novel")
        self.assertIsNotNone(first)
        self.assertEqual((first.method, first.review_status), ("none", "open"))
        self.assertIsNone(self._row("zzfixture second novel"))


class TestMapPendingAndRemap(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def test_map_pending_maps_only_scoped_unmapped_job_skills(self) -> None:
        with self.engine.begin() as conn:
            insert_job_skills(
                conn,
                self.job,
                "local.v1",
                [
                    (
                        "ZZFixture Cloud Platforms",
                        "zzfixture cloud platforms",
                        "must_have",
                    ),
                    ("zzfixture mystery", "zzfixture mystery", "nice_to_have"),
                ],
            )
        summary = map_pending(
            self.engine,
            embed=_far_embed,
            embedding_model=_MODEL,
            raw_norms=["zzfixture cloud platforms", "zzfixture mystery"],
        )
        self.assertEqual((summary.mapped, summary.unmapped), (1, 1))
        again = map_pending(
            self.engine,
            embed=_far_embed,
            embedding_model=_MODEL,
            raw_norms=["zzfixture cloud platforms", "zzfixture mystery"],
        )
        self.assertEqual((again.mapped, again.unmapped), (0, 0))

    def test_remap_unresolved_keeps_human_decisions(self) -> None:
        norms = {
            "embedding": "zzfixture r embedding",
            "open": "zzfixture r open",
            "rejected": "zzfixture r rejected",
            "resolved": "zzfixture r resolved",
            "dismissed": "zzfixture r dismissed",
        }
        with self.engine.begin() as conn:
            insert_mapping(
                conn,
                norms["embedding"],
                skill_id="fixture-cloud",
                method="embedding",
                score=0.9,
                review_status=None,
            )
            insert_mapping(conn, norms["open"])
            insert_mapping(conn, norms["rejected"], review_status="rejected")
            insert_mapping(
                conn,
                norms["resolved"],
                skill_id="fixture-cloud",
                method="alias",
                review_status="resolved",
            )
            insert_mapping(conn, norms["dismissed"], review_status="dismissed")
        deleted = remap_unresolved(self.engine, raw_norms=list(norms.values()))
        self.assertEqual(deleted, 2)
        with self.engine.connect() as conn:
            left = {
                r.raw_norm
                for r in conn.execute(
                    text(
                        "SELECT raw_norm FROM silver.skill_mapping "
                        "WHERE raw_norm LIKE 'zzfixture r %'"
                    )
                )
            }
        self.assertEqual(
            left,
            {norms["rejected"], norms["resolved"], norms["dismissed"]},
        )


if __name__ == "__main__":
    unittest.main()
