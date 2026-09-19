"""Integration tests for core.skills.cv_map against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
    sparse_vector,
)

from core.cv.schema import Bullet, CVTruthBase, Experience, Skill
from core.cv.store import read_truth_base, write_truth_base
from core.db.session import session_scope
from core.settings import get_settings
from core.skills.cv_map import CV_MAP_LABEL, map_cv_skills
from core.skills.esco_load import load_esco

_MODEL = get_settings().embedding_model


def _far_embed(_text: str) -> list[float]:
    return sparse_vector({767: 1.0})


class TestMapCvSkills(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)
        load_esco(self.owner, FIXTURE_ESCO_DIR)
        self.user_id = uuid.uuid4()
        with session_scope(self.owner) as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'Test User')"
                ),
                {"id": self.user_id, "email": f"{self.user_id}@example.com"},
            )
        write_truth_base(
            self.app,
            self.user_id,
            "# markdown",
            CVTruthBase(
                identity="Jane Doe",
                headline="Engineer",
                skills=[
                    Skill(name="ZZFixture Cloud Platforms"),
                    Skill(name="zzfixture unheard of thing"),
                    Skill(name="zzfixture kept", canonical_id="manual:1"),
                ],
                experience=[
                    Experience(
                        company="Fixture Corp",
                        title="Engineer",
                        bullets=[Bullet(bullet_id="fixture01", text="Did things.")],
                    )
                ],
            ),
        )

    def tearDown(self) -> None:
        purge_fixtures(self.owner)
        with session_scope(self.owner) as conn:
            for table in ("cv_truth_base_history", "cv_truth_base"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE user_id = :id"),
                    {"id": self.user_id},
                )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _run(self):
        return map_cv_skills(
            app_engine=self.app,
            owner_engine=self.owner,
            user_id=self.user_id,
            embed=_far_embed,
            embedding_model=_MODEL,
        )

    def test_fills_canonical_ids_and_writes_a_labelled_new_version(self) -> None:
        result = self._run()
        self.assertEqual(
            (result.new_version, result.mapped, result.unmapped), (2, 2, 1)
        )
        stored = read_truth_base(self.app, self.user_id)
        ids = {s.name: s.canonical_id for s in stored.truth_base.skills}
        self.assertEqual(
            ids,
            {
                "ZZFixture Cloud Platforms": "fixture-cloud",
                "zzfixture unheard of thing": None,
                "zzfixture kept": "manual:1",
            },
        )
        self.assertEqual(stored.label, CV_MAP_LABEL)

    def test_bullet_ids_are_untouched(self) -> None:
        self._run()
        stored = read_truth_base(self.app, self.user_id)
        self.assertEqual(
            stored.truth_base.experience[0].bullets[0].bullet_id, "fixture01"
        )

    def test_a_second_run_writes_no_new_version(self) -> None:
        self._run()
        second = self._run()
        self.assertIsNone(second.new_version)
        self.assertEqual(read_truth_base(self.app, self.user_id).version, 2)

    def test_flags_the_mapping_rows_as_seen_in_a_cv_and_queues_unmapped_ones(
        self,
    ) -> None:
        self._run()
        with self.owner.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT raw_norm, seen_in_cv, review_status "
                    "FROM silver.skill_mapping WHERE raw_norm LIKE 'zzfixture%' "
                    "ORDER BY raw_norm"
                )
            ).all()
        # The already-mapped "zzfixture kept" must have no row at all: it is
        # neither re-mapped nor queued for review.
        self.assertEqual(
            [(r.raw_norm, r.seen_in_cv, r.review_status) for r in rows],
            [
                ("zzfixture cloud platforms", True, None),
                ("zzfixture unheard of thing", True, "open"),
            ],
        )

    def test_never_overwrites_an_existing_canonical_id_even_when_a_mapping_exists(
        self,
    ) -> None:
        # "zzfixture cloud platforms" label-matches fixture-cloud, so only the
        # `canonical_id is None` guard keeps the hand-set id.
        write_truth_base(
            self.app,
            self.user_id,
            "# markdown",
            CVTruthBase(
                identity="Jane Doe",
                headline="Engineer",
                skills=[
                    Skill(name="zzfixture cloud platforms", canonical_id="manual:2"),
                    Skill(name="zzfixture python programming"),
                ],
                experience=[],
            ),
        )
        result = self._run()
        self.assertEqual(
            (result.new_version, result.mapped, result.unmapped), (3, 2, 0)
        )
        stored = read_truth_base(self.app, self.user_id)
        ids = {s.name: s.canonical_id for s in stored.truth_base.skills}
        self.assertEqual(
            ids,
            {
                "zzfixture cloud platforms": "manual:2",
                "zzfixture python programming": "fixture-python",
            },
        )

    def test_a_user_without_a_cv_raises_lookup_error(self) -> None:
        with self.assertRaises(LookupError):
            map_cv_skills(
                app_engine=self.app,
                owner_engine=self.owner,
                user_id=uuid.uuid4(),
                embed=_far_embed,
                embedding_model=_MODEL,
            )


if __name__ == "__main__":
    unittest.main()
