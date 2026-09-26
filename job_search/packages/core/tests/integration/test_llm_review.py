"""Integration tests: how `llm` mappings behave in the review flow."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    insert_mapping,
    live_owner_engine,
    purge_fixtures,
)

from core.skills import review
from core.skills.esco_load import load_esco
from core.skills.mapper import remap_all_auto, remap_unresolved


# Every remap call in these tests MUST be scoped to these fixture strings: an
# unscoped call deletes every auto-made mapping in the shared dev database.
_SEEDED_NORMS = ["zzfixture llm checked", "zzfixture llm unchecked"]


def _insert_llm_match(conn, raw_norm: str, note: str = "same skill") -> None:
    insert_mapping(
        conn,
        raw_norm,
        skill_id="fixture-cloud",
        method="llm",
        score=0.8,
        review_status=None,
    )
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET llm_verdict = 'match_high', "
            "llm_note = :note, llm_checked_at = now() WHERE raw_norm = :n"
        ),
        {"note": note, "n": raw_norm},
    )


class TestLlmReviewFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def test_llm_match_is_listed_for_verification_with_its_note(self) -> None:
        with self.engine.begin() as conn:
            _insert_llm_match(conn, "zzfixture llm one", note="cloud platform")
        with self.engine.connect() as conn:
            items = review.list_auto_matches(conn, query="zzfixture llm one")
        self.assertEqual(len(items), 1)
        self.assertEqual(
            (items[0].method, items[0].skill_id, items[0].llm_note),
            ("llm", "fixture-cloud", "cloud platform"),
        )

    def test_confirming_an_llm_match_creates_a_review_alias(self) -> None:
        with self.engine.begin() as conn:
            _insert_llm_match(conn, "zzfixture llm two")
        with self.engine.begin() as conn:
            review.resolve_to_skill(conn, "zzfixture llm two", "fixture-cloud")
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT a.source, m.method, m.review_status "
                    "FROM silver.skill_alias a JOIN silver.skill_mapping m "
                    "ON m.raw_norm = a.alias_norm WHERE a.alias_norm = :n"
                ),
                {"n": "zzfixture llm two"},
            ).one()
        self.assertEqual(tuple(row), ("review", "alias", "resolved"))

    def test_rejecting_an_llm_match_returns_it_to_the_unmapped_list(self) -> None:
        with self.engine.begin() as conn:
            _insert_llm_match(conn, "zzfixture llm three")
        with self.engine.begin() as conn:
            review.reject_auto_match(conn, "zzfixture llm three")
        with self.engine.connect() as conn:
            items = review.list_unmapped(conn, query="zzfixture llm three")
            checked = conn.execute(
                text(
                    "SELECT llm_checked_at IS NOT NULL FROM silver.skill_mapping "
                    "WHERE raw_norm = :n"
                ),
                {"n": "zzfixture llm three"},
            ).scalar_one()
        self.assertEqual([i.review_status for i in items], ["rejected"])
        self.assertTrue(checked)  # so the model is never asked about it again

    def test_unmapped_item_carries_the_models_note_and_custom_label(self) -> None:
        with self.engine.begin() as conn:
            insert_mapping(conn, "zzfixture llm four", review_status="open")
            conn.execute(
                text(
                    "UPDATE silver.skill_mapping SET llm_verdict = 'no_equivalent', "
                    "llm_custom_label = 'GRPO', llm_note = 'RL method', "
                    "llm_checked_at = now() WHERE raw_norm = :n"
                ),
                {"n": "zzfixture llm four"},
            )
        with self.engine.connect() as conn:
            (item,) = review.list_unmapped(conn, query="zzfixture llm four")
        self.assertEqual(
            (item.llm_verdict, item.llm_custom_label, item.llm_note),
            ("no_equivalent", "GRPO", "RL method"),
        )

    def test_remap_all_auto_keeps_llm_matches(self) -> None:
        with self.engine.begin() as conn:
            _insert_llm_match(conn, "zzfixture llm five")
        remap_all_auto(self.engine, raw_norms=["zzfixture llm five"])
        with self.engine.connect() as conn:
            method = conn.execute(
                text("SELECT method FROM silver.skill_mapping WHERE raw_norm = :n"),
                {"n": "zzfixture llm five"},
            ).scalar_one_or_none()
        self.assertEqual(method, "llm")

    def _seed_open_rows(self) -> None:
        """Insert one open row with a verdict and one without."""
        with self.engine.begin() as conn:
            insert_mapping(conn, "zzfixture llm checked", review_status="open")
            conn.execute(
                text(
                    "UPDATE silver.skill_mapping SET llm_verdict = 'no_equivalent', "
                    "llm_note = 'kept', llm_checked_at = now() WHERE raw_norm = :n"
                ),
                {"n": "zzfixture llm checked"},
            )
            insert_mapping(conn, "zzfixture llm unchecked", review_status="open")

    def _surviving(self) -> set[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT raw_norm FROM silver.skill_mapping "
                    "WHERE raw_norm LIKE 'zzfixture llm %checked'"
                )
            ).all()
        return {r.raw_norm for r in rows}

    def test_remap_unresolved_keeps_open_rows_the_model_checked(self) -> None:
        self._seed_open_rows()
        remap_unresolved(self.engine, raw_norms=_SEEDED_NORMS)
        self.assertEqual(self._surviving(), {"zzfixture llm checked"})

    def test_remap_all_auto_keeps_open_rows_the_model_checked(self) -> None:
        self._seed_open_rows()
        remap_all_auto(self.engine, raw_norms=_SEEDED_NORMS)
        self.assertEqual(self._surviving(), {"zzfixture llm checked"})


if __name__ == "__main__":
    unittest.main()
