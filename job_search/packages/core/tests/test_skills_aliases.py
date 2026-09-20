"""Unit tests for loading config/skill_aliases.yml."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.skills.aliases import load_seed_aliases
from core.skills.normalise import normalise_skill


def _write(text: str) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
    handle.write(text)
    handle.close()
    return Path(handle.name)


class TestCommittedSeedFile(unittest.TestCase):
    def test_every_gcp_spelling_points_at_one_custom_skill(self) -> None:
        seeds = {s.skill_id: s for s in load_seed_aliases()}
        gcp = seeds["custom:google-cloud-platform"]
        spellings = {normalise_skill(a) for a in (gcp.label or "", *gcp.aliases)}
        self.assertTrue({"gcp", "google cloud", "google cloud platform"} <= spellings)

    def test_no_alias_is_claimed_by_two_entries(self) -> None:
        seeds = load_seed_aliases()  # raises ValueError on a conflict
        self.assertTrue(seeds)
        owners: dict[str, set[str]] = {}
        for seed in seeds:
            for spelling in (*seed.aliases, seed.label or ""):
                norm = normalise_skill(spelling)
                if norm:
                    owners.setdefault(norm, set()).add(seed.skill_id)
        shared = {norm: ids for norm, ids in owners.items() if len(ids) > 1}
        self.assertEqual(shared, {})


class TestLoadSeedAliases(unittest.TestCase):
    def test_custom_id_without_a_label_is_rejected(self) -> None:
        path = _write("skills:\n  - skill_id: 'custom:x'\n    aliases: ['x']\n")
        with self.assertRaises(ValueError):
            load_seed_aliases(path)

    def test_alias_claimed_by_two_entries_is_rejected(self) -> None:
        path = _write(
            "skills:\n"
            "  - {skill_id: 'custom:a', label: 'A', aliases: ['shared']}\n"
            "  - {skill_id: 'custom:b', label: 'B', aliases: ['Shared']}\n"
        )
        with self.assertRaises(ValueError):
            load_seed_aliases(path)

    def test_esco_id_entry_needs_no_label(self) -> None:
        path = _write("skills:\n  - {skill_id: 'abc-123', aliases: ['thing']}\n")
        seeds = load_seed_aliases(path)
        self.assertEqual(seeds[0].skill_id, "abc-123")
        self.assertIsNone(seeds[0].label)

    def test_non_list_aliases_are_rejected(self) -> None:
        path = _write("skills:\n  - {skill_id: 'abc', aliases: 'nope'}\n")
        with self.assertRaises(ValueError):
            load_seed_aliases(path)


if __name__ == "__main__":
    unittest.main()
