"""Map a user's CV skills to ESCO / custom skill ids (PLAN.md Step 14).

Fills `Skill.canonical_id` in the CV truth base where it is still None and
the skill string has a mapping. The write goes through
`core.cv.store.write_truth_base` with a label, so it is a new, traceable,
reversible version exactly like any other edit; bullet IDs are untouched.
Shared mapping rows (`silver.skill_mapping`) are written with the owner
engine; the per-user truth base is read and written with the RLS-subject
app engine.
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
    truth_base = stored.truth_base

    # Only skills still without an id are mapped: an already-mapped skill
    # (e.g. hand-corrected in the CV Editor) must not be re-embedded or land
    # in the review queue.
    resolved = map_strings(
        owner_engine,
        [skill.name for skill in truth_base.skills if skill.canonical_id is None],
        embed=embed,
        embedding_model=embedding_model,
        seen_in_cv=True,
        accept_threshold=accept_threshold,
    )
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
