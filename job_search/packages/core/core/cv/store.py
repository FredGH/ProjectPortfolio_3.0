"""The CV truth base's history-then-replace write path (PLAN.md Step 13).

A write is always one transaction: insert the new content into
`cv_truth_base_history` at `version = current + 1` (or `1` for a first
write), then make `cv_truth_base` mirror it — never a second live row
in `cv_truth_base`, per the DB's own `user_id UNIQUE` constraint (0017).
Both a fresh extraction and a correction-pass save go through this same
path, so both produce a new version and both are traceable in history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text

from core.cv.schema import CVTruthBase
from core.db.session import session_scope


@dataclass(frozen=True)
class StoredTruthBase:
    """One user's current `cv_truth_base` row, deserialized.

    Attributes:
        version: The current version number.
        extracted_markdown: The markdown this version was parsed from.
        truth_base: The parsed `CVTruthBase`.
        updated_at: When this version was written.
    """

    version: int
    extracted_markdown: str
    truth_base: CVTruthBase
    updated_at: datetime


def read_truth_base(engine: Engine, user_id: uuid.UUID) -> StoredTruthBase | None:
    """Read a user's current CV truth base.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: The user to read.

    Returns:
        The `StoredTruthBase`, or None if this user has no CV yet.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                "SELECT version, extracted_markdown, truth_base, updated_at "
                "FROM cv_truth_base WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        ).one_or_none()
    if row is None:
        return None
    return StoredTruthBase(
        version=row.version,
        extracted_markdown=row.extracted_markdown,
        truth_base=CVTruthBase.model_validate(row.truth_base),
        updated_at=row.updated_at,
    )


def write_truth_base(
    engine: Engine,
    user_id: uuid.UUID,
    extracted_markdown: str,
    truth_base: CVTruthBase,
) -> int:
    """Write a new version of a user's CV truth base.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: The user this CV belongs to.
        extracted_markdown: The markdown this version was parsed from
            (or the prior version's markdown, unchanged, for a
            correction-pass save that only edits structured fields).
        truth_base: The truth base to store as the new current version.

    Returns:
        The new version number (1 for a user's first CV).
    """
    truth_base_json = truth_base.model_dump_json()
    with session_scope(engine, user_id=user_id) as conn:
        current = conn.execute(
            text("SELECT version FROM cv_truth_base WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).one_or_none()
        new_version = (current.version + 1) if current else 1

        conn.execute(
            text(
                "INSERT INTO cv_truth_base_history "
                "(user_id, version, extracted_markdown, truth_base) "
                "VALUES (:user_id, :version, :markdown, CAST(:truth_base AS jsonb))"
            ),
            {
                "user_id": user_id,
                "version": new_version,
                "markdown": extracted_markdown,
                "truth_base": truth_base_json,
            },
        )

        if current:
            conn.execute(
                text(
                    "UPDATE cv_truth_base SET "
                    "version = :version, "
                    "extracted_markdown = :markdown, "
                    "truth_base = CAST(:truth_base AS jsonb), "
                    "updated_at = now() "
                    "WHERE user_id = :user_id"
                ),
                {
                    "user_id": user_id,
                    "version": new_version,
                    "markdown": extracted_markdown,
                    "truth_base": truth_base_json,
                },
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO cv_truth_base "
                    "(user_id, version, extracted_markdown, truth_base) "
                    "VALUES (:user_id, :version, :markdown, CAST(:truth_base AS jsonb))"
                ),
                {
                    "user_id": user_id,
                    "version": new_version,
                    "markdown": extracted_markdown,
                    "truth_base": truth_base_json,
                },
            )
    return new_version
