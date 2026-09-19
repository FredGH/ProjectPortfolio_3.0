"""Integration tests for core.skills.aliases.sync_seed_aliases."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine, purge_fixtures

from core.skills.aliases import sync_seed_aliases


class TestSyncSeedAliases(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        sfx = uuid.uuid4().hex[:8]
        self.skill_id = f"custom:fixture-{sfx}"
        handle = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        handle.write(
            f"skills:\n  - skill_id: '{self.skill_id}'\n"
            "    label: 'zzfixture Label'\n"
            "    aliases: ['zzfixture alias one', 'zzfixture alias two']\n"
        )
        handle.close()
        self.path = Path(handle.name)

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _aliases(self) -> dict[str, tuple[str, str]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT alias_norm, skill_id, source FROM silver.skill_alias "
                    "WHERE alias_norm LIKE 'zzfixture%'"
                )
            ).all()
        return {r.alias_norm: (r.skill_id, r.source) for r in rows}

    def test_writes_the_custom_skill_and_every_spelling_including_the_label(
        self,
    ) -> None:
        attempted = sync_seed_aliases(self.engine, self.path)
        self.assertEqual(attempted, 3)
        self.assertEqual(
            self._aliases(),
            {
                "zzfixture alias one": (self.skill_id, "seed"),
                "zzfixture alias two": (self.skill_id, "seed"),
                "zzfixture label": (self.skill_id, "seed"),
            },
        )
        with self.engine.connect() as conn:
            label = conn.execute(
                text(
                    "SELECT canonical_label FROM silver.custom_skill "
                    "WHERE skill_id = :s"
                ),
                {"s": self.skill_id},
            ).scalar_one()
        self.assertEqual(label, "zzfixture Label")

    def test_is_idempotent(self) -> None:
        sync_seed_aliases(self.engine, self.path)
        sync_seed_aliases(self.engine, self.path)
        self.assertEqual(len(self._aliases()), 3)

    def test_never_overwrites_a_review_sourced_alias(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
                    "VALUES ('zzfixture alias one', 'custom:fixture-other', 'review')"
                )
            )
        sync_seed_aliases(self.engine, self.path)
        self.assertEqual(
            self._aliases()["zzfixture alias one"], ("custom:fixture-other", "review")
        )


if __name__ == "__main__":
    unittest.main()
