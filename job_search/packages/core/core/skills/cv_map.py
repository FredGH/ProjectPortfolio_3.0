"""Map a user's CV skills to ESCO / custom skill ids (PLAN.md Step 14).

Fills `Skill.canonical_id` in the CV truth base where it is still None and
the skill string has a mapping. The write goes through
`core.cv.store.write_truth_base` with a label, so it is a new, traceable,
reversible version exactly like any other edit; bullet IDs are untouched.
Shared mapping rows (`silver.skill_mapping`) are written with the owner
engine; the per-user truth base is read and written with the RLS-subject
app engine.

`carry_over_canonical_ids` is the CV Editor's side of the same contract:
a manual save rebuilds the skill rows from a grid that has no id column,
so the ids filled in here have to be carried across it by name.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine

from core.cv.store import read_truth_base, write_truth_base
from core.skills.mapper import EMBEDDING_ACCEPT_COSINE, map_strings
from core.skills.normalise import normalise_skill

CV_MAP_LABEL = "ESCO skill normalisation"


@dataclass(frozen=True)
class CvMapResult:
    """The outcome of mapping one user's CV skills.

    Attributes:
        new_version: The truth-base version written, or None if no skill
            changed (so no version was written).
        mapped: Skills that now have a `canonical_id`.
        unmapped: Skills still without one (they are in the review list).
    """

    new_version: int | None
    mapped: int
    unmapped: int


def map_cv_skills(
    *,
    app_engine: Engine,
    owner_engine: Engine,
    user_id: uuid.UUID,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    accept_threshold: float = EMBEDDING_ACCEPT_COSINE,
) -> CvMapResult:
    """Fill `canonical_id` on a user's CV skills.

    Args:
        app_engine: The app-role (RLS) engine, for the truth base.
        owner_engine: The owner-role engine, for the shared mapping table.
        user_id: Whose CV to map.
        embed: Maps a string to its embedding.
        embedding_model: The embedding model in use.
        accept_threshold: Minimum cosine similarity to accept an embedding
            match (see `core.skills.mapper.map_skill`).

    Returns:
        The `CvMapResult`. A skill that already has a `canonical_id` (e.g.
        hand-corrected in the CV Editor) is neither re-mapped nor queued for
        review, and its id is never overwritten.

    Raises:
        LookupError: If the user has no CV truth base.
        core.skills.mapper.EmbeddingModelMismatch: If the stored ESCO
            embeddings are from a different model.
    """
    stored = read_truth_base(app_engine, user_id)
    if stored is None:
        raise LookupError(f"user {user_id} has no CV truth base")

    # Only skills still without an id are mapped: an already-mapped skill
    # (e.g. hand-corrected in the CV Editor) must not be re-embedded or land
    # in the review queue.
    resolved = map_strings(
        owner_engine,
        [
            skill.name
            for skill in stored.truth_base.skills
            if skill.canonical_id is None
        ],
        embed=embed,
        embedding_model=embedding_model,
        seen_in_cv=True,
        accept_threshold=accept_threshold,
    )

    # `map_strings` makes one embedding call per new string, so minutes can
    # pass between the read above and the write below. Re-read here and fill
    # the ids into that fresh version, so a CV Editor save made in the
    # meantime is built upon rather than overwritten. A skill added in the
    # window simply has no id yet — the next run maps it.
    stored = read_truth_base(app_engine, user_id)
    if stored is None:
        raise LookupError(f"user {user_id} has no CV truth base")
    truth_base = stored.truth_base

    changed = 0
    skills = []
    for skill in truth_base.skills:
        skill_id = resolved.get(normalise_skill(skill.name))
        if skill.canonical_id is None and skill_id is not None:
            skills.append(skill.model_copy(update={"canonical_id": skill_id}))
            changed += 1
        else:
            skills.append(skill)

    new_version = None
    if changed:
        new_version = write_truth_base(
            app_engine,
            user_id,
            stored.extracted_markdown,
            truth_base.model_copy(update={"skills": skills}),
            label=CV_MAP_LABEL,
        )
    unmapped = sum(1 for skill in skills if skill.canonical_id is None)
    return CvMapResult(new_version, mapped=len(skills) - unmapped, unmapped=unmapped)


def _name_key(name: object) -> str:
    """Reduce a skill name to the key used to match it across an edit.

    Args:
        name: A skill's `name` value (may be None or a pandas NaN for a
            row the user left blank).

    Returns:
        The stripped, lowercased name; empty if there is no usable name.
    """
    if not isinstance(name, str):
        return ""
    return name.strip().lower()


def carry_over_canonical_ids(previous: list[dict], edited: list[dict]) -> list[dict]:
    """Keep each unchanged skill's `canonical_id` across a CV Editor save.

    The editor's skills grid has no `canonical_id` column, so a save
    rebuilds every skill without one and would otherwise wipe the ids
    `map-cv-skills` filled in — silently un-mapping the CV. A skill whose
    name is unchanged (ignoring case and surrounding whitespace) therefore
    keeps its id. A renamed or newly added skill gets None: the id belonged
    to the old string, and there is no UI for setting one, so an id is only
    ever carried from `previous`, never invented.

    Args:
        previous: The skills of the version being edited, each a mapping
            with `name` and `canonical_id` keys.
        edited: The skills rebuilt from the editor grid.

    Returns:
        A new list, `edited` with `canonical_id` restored wherever the name
        matched (the first id wins if `previous` repeats a name). Neither
        input list nor its dicts are modified.
    """
    known: dict[str, str] = {}
    for skill in previous:
        key = _name_key(skill.get("name"))
        canonical_id = skill.get("canonical_id")
        if key and canonical_id is not None:
            known.setdefault(key, canonical_id)
    return [
        {
            **skill,
            "canonical_id": (
                skill.get("canonical_id") or known.get(_name_key(skill.get("name")))
            ),
        }
        for skill in edited
    ]
