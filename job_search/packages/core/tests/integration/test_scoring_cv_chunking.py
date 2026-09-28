"""Integration tests for core.scoring.cv_chunking against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.cv.schema import Bullet, CVTruthBase, Experience, Skill
from core.cv.store import write_truth_base
from core.db.session import build_engine
from core.scoring.cv_chunking import build_cv_sections, chunk_and_embed_cv
from core.settings import get_settings

_TRUTH_BASE = CVTruthBase(
    identity="zzfixture Person",
    headline="Senior Data Engineer",
    summary="A summary paragraph about zzfixture skills.",
    skills=[Skill(name="Python"), Skill(name="SQL")],
    experience=[
        Experience(
            company="zzfixture Co",
            title="Data Engineer",
            bullets=[Bullet(bullet_id="b1", text="Built a zzfixture pipeline.")],
        )
    ],
)

# Two experience bullets across two different roles — both map to the
# section name "experience", so build_cv_sections emits two separate
# ("experience", ..., ...) entries. Finding 1: chunk_index must run per
# section NAME across the whole write loop, not restart per entry.
_TRUTH_BASE_TWO_EXPERIENCE_BULLETS = CVTruthBase(
    identity="zzfixture Person Two",
    headline="Senior Data Engineer",
    summary="A summary paragraph about zzfixture skills.",
    skills=[Skill(name="Python"), Skill(name="SQL")],
    experience=[
        Experience(
            company="zzfixture Co",
            title="Data Engineer",
            bullets=[Bullet(bullet_id="b1", text="Built a zzfixture pipeline.")],
        ),
        Experience(
            company="zzfixture Other Co",
            title="Senior Data Engineer",
            bullets=[Bullet(bullet_id="b2", text="Led a zzfixture migration.")],
        ),
    ],
)


class TestBuildCvSections(unittest.TestCase):
    def test_sections_cover_summary_skills_and_experience(self) -> None:
        sections = build_cv_sections(_TRUTH_BASE)
        names = {s for s, _, _ in sections}
        self.assertEqual(names, {"summary", "skills", "experience"})

    def test_experience_source_ref_points_at_the_bullet(self) -> None:
        sections = build_cv_sections(_TRUTH_BASE)
        experience = [s for s in sections if s[0] == "experience"][0]
        self.assertIn("b1", experience[1])
        self.assertIn("zzfixture pipeline", experience[2])


class TestChunkAndEmbedCv(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture cv chunk user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
        write_truth_base(
            self.app_engine,
            self.user_id,
            "zzfixture markdown",
            _TRUTH_BASE,
            label="fixture",
        )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.cv_chunk_embedding WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _fake_embed(self, text_: str) -> list[float]:
        return [0.0] * 768

    def test_chunking_the_cv_writes_rows_for_its_sections(self) -> None:
        written = chunk_and_embed_cv(
            self.app_engine,
            self.user_id,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
        )
        self.assertGreater(written, 0)
        with self.owner.connect() as conn:
            sections = (
                conn.execute(
                    text(
                        "SELECT DISTINCT section FROM scoring.cv_chunk_embedding "
                        "WHERE user_id = :id"
                    ),
                    {"id": self.user_id},
                )
                .scalars()
                .all()
            )
        self.assertIn("experience", sections)

    def test_without_refresh_a_second_call_on_the_same_version_writes_nothing(
        self,
    ) -> None:
        chunk_and_embed_cv(
            self.app_engine,
            self.user_id,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
        )
        written_again = chunk_and_embed_cv(
            self.app_engine,
            self.user_id,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
        )
        self.assertEqual(written_again, 0)

    def test_two_experience_bullets_both_get_chunked_without_colliding(self) -> None:
        # Finding 1 regression: two experience bullets across two roles both
        # map to section "experience", so build_cv_sections emits two
        # separate entries under that name. chunk_and_embed_cv must not
        # raise on the second entry's chunk_index colliding with the
        # first's, and must persist both bullets' chunks.
        write_truth_base(
            self.app_engine,
            self.user_id,
            "zzfixture markdown two bullets",
            _TRUTH_BASE_TWO_EXPERIENCE_BULLETS,
            label="fixture",
        )
        written = chunk_and_embed_cv(
            self.app_engine,
            self.user_id,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
        )
        self.assertGreater(written, 0)
        with self.owner.connect() as conn:
            chunk_indexes = (
                conn.execute(
                    text(
                        "SELECT chunk_index FROM scoring.cv_chunk_embedding "
                        "WHERE user_id = :id AND section = 'experience' "
                        "ORDER BY chunk_index"
                    ),
                    {"id": self.user_id},
                )
                .scalars()
                .all()
            )
        self.assertEqual(chunk_indexes, [0, 1])


if __name__ == "__main__":
    unittest.main()
