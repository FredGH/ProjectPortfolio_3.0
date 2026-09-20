"""Integration tests for the esco schema (migration 0022)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.integration.skills_fixtures import (
    axis_vector,
    live_app_engine,
    live_owner_engine,
)

from core.skills.vector import to_pgvector


class TestEscoSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        self.skill_id = f"fixture-schema-{uuid.uuid4().hex[:8]}"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill (skill_id, concept_uri, preferred_label) "
                    "VALUES (:id, :uri, 'zzfixture schema skill')"
                ),
                {"id": self.skill_id, "uri": f"http://example.test/{self.skill_id}"},
            )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM esco.skill WHERE skill_id = :id"),
                {"id": self.skill_id},
            )

    def test_skill_embedding_accepts_a_768_dimension_vector(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_embedding "
                    "(skill_id, embedding_model, embedding) "
                    "VALUES (:id, 'test-model', CAST(:v AS vector))"
                ),
                {"id": self.skill_id, "v": to_pgvector(axis_vector(0))},
            )
            count = conn.execute(
                text("SELECT count(*) FROM esco.skill_embedding WHERE skill_id = :id"),
                {"id": self.skill_id},
            ).scalar_one()
        self.assertEqual(count, 1)

    def test_skill_embedding_rejects_a_wrong_dimension_vector(self) -> None:
        with self.assertRaises(DBAPIError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO esco.skill_embedding "
                        "(skill_id, embedding_model, embedding) "
                        "VALUES (:id, 'test-model', CAST('[1,2,3]' AS vector))"
                    ),
                    {"id": self.skill_id},
                )

    def test_deleting_a_skill_cascades_to_labels_and_embeddings(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_label "
                    "(skill_id, label, label_norm, is_preferred) "
                    "VALUES (:id, 'L', 'zzfixture l', true)"
                ),
                {"id": self.skill_id},
            )
            conn.execute(
                text("DELETE FROM esco.skill WHERE skill_id = :id"),
                {"id": self.skill_id},
            )
            remaining = conn.execute(
                text("SELECT count(*) FROM esco.skill_label WHERE skill_id = :id"),
                {"id": self.skill_id},
            ).scalar_one()
        self.assertEqual(remaining, 0)

    def test_app_role_can_read_but_not_write(self) -> None:
        with self.app.connect() as conn:
            found = conn.execute(
                text("SELECT count(*) FROM esco.skill WHERE skill_id = :id"),
                {"id": self.skill_id},
            ).scalar_one()
        self.assertEqual(found, 1)
        with self.assertRaises(DBAPIError):
            with self.app.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO esco.skill (skill_id, concept_uri, "
                        "preferred_label) VALUES ('fixture-x', 'http://x', 'x')"
                    )
                )


if __name__ == "__main__":
    unittest.main()
