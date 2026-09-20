"""Seed skill aliases: config/skill_aliases.yml -> silver.skill_alias.

The seed file is the committed starting vocabulary for tools ESCO lacks
(and corrections to ESCO). `sync_seed_aliases` upserts it idempotently and
never overwrites an alias a human created on the Skill Review page.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import Engine, text

from core.skills.normalise import normalise_skill

_DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "skill_aliases.yml"

_UPSERT_CUSTOM = text(
    "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
    "VALUES (:skill_id, :label) "
    "ON CONFLICT (skill_id) DO UPDATE SET canonical_label = EXCLUDED.canonical_label"
)
_UPSERT_ALIAS = text(
    "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
    "VALUES (:alias_norm, :skill_id, 'seed') "
    "ON CONFLICT (alias_norm) DO UPDATE SET skill_id = EXCLUDED.skill_id "
    "WHERE silver.skill_alias.source = 'seed'"
)


@dataclass(frozen=True)
class SeedSkill:
    """One entry of the seed file.

    Attributes:
        skill_id: An ESCO skill id or a `custom:<slug>` id.
        label: Canonical label; required for `custom:` ids.
        aliases: Alternative spellings (the label is implicitly one too).
    """

    skill_id: str
    label: str | None
    aliases: tuple[str, ...]


def load_seed_aliases(path: Path | None = None) -> list[SeedSkill]:
    """Load and validate the seed alias file.

    Args:
        path: The YAML file. Defaults to `config/skill_aliases.yml`.

    Returns:
        The entries, in file order.

    Raises:
        ValueError: If an entry is malformed, a `custom:` id has no label,
            or a normalised alias is claimed by two entries.
    """
    data = yaml.safe_load((path or _DEFAULT_PATH).read_text()) or {}
    entries = data.get("skills")
    if not isinstance(entries, list):
        raise ValueError("skill alias file must contain a top-level 'skills' list")

    seeds: list[SeedSkill] = []
    claimed: dict[str, str] = {}
    for entry in entries:
        skill_id = entry.get("skill_id") if isinstance(entry, dict) else None
        if not isinstance(skill_id, str) or not skill_id:
            raise ValueError(f"alias entry needs a string skill_id: {entry!r}")
        label = entry.get("label")
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list) or not all(
            isinstance(a, str) for a in aliases
        ):
            raise ValueError(f"aliases for {skill_id} must be a list of strings")
        if skill_id.startswith("custom:") and not label:
            raise ValueError(f"custom skill {skill_id} needs a label")
        seed = SeedSkill(skill_id, label, tuple(aliases))
        for spelling in _spellings(seed):
            owner = claimed.setdefault(spelling, skill_id)
            if owner != skill_id:
                raise ValueError(
                    f"alias {spelling!r} is claimed by both {owner} and {skill_id}"
                )
        seeds.append(seed)
    return seeds


def _spellings(seed: SeedSkill) -> list[str]:
    """List a seed entry's normalised, non-empty spellings.

    Args:
        seed: The entry.

    Returns:
        The de-duplicated normalised aliases, including the label.
    """
    raw = list(seed.aliases) + ([seed.label] if seed.label else [])
    return sorted({norm for norm in map(normalise_skill, raw) if norm})


def sync_seed_aliases(engine: Engine, path: Path | None = None) -> int:
    """Upsert the seed file into `silver.custom_skill` and `silver.skill_alias`.

    Args:
        engine: The owner-role engine.
        path: The YAML file. Defaults to `config/skill_aliases.yml`.

    Returns:
        The number of alias rows attempted (a `review`-sourced alias with
        the same key is left unchanged but still counted).

    Raises:
        ValueError: If the file is invalid (see `load_seed_aliases`).
    """
    seeds = load_seed_aliases(path)
    attempted = 0
    with engine.begin() as conn:
        for seed in seeds:
            if seed.skill_id.startswith("custom:"):
                conn.execute(
                    _UPSERT_CUSTOM, {"skill_id": seed.skill_id, "label": seed.label}
                )
            for spelling in _spellings(seed):
                conn.execute(
                    _UPSERT_ALIAS, {"alias_norm": spelling, "skill_id": seed.skill_id}
                )
                attempted += 1
    return attempted
