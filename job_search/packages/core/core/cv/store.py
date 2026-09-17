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
    """One version of a user's `cv_truth_base`/`cv_truth_base_history`
    row, deserialized.

    Attributes:
        version: This version's number.
        extracted_markdown: The markdown this version was parsed from.
        truth_base: The parsed `CVTruthBase`.
        label: The name given to this version at save time, if any.
        updated_at: When this version was written.
    """

    version: int
    extracted_markdown: str
    truth_base: CVTruthBase
    label: str | None
    updated_at: datetime


@dataclass(frozen=True)
class HistoryEntry:
    """One version's listing-page summary — no markdown/truth-base
    payload, so browsing history stays cheap regardless of CV size.

    Attributes:
        version: This version's number.
        label: The name given to this version at save time, if any.
        created_at: When this version was written.
    """

    version: int
    label: str | None
    created_at: datetime


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
                "SELECT version, extracted_markdown, truth_base, label, updated_at "
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
        label=row.label,
        updated_at=row.updated_at,
    )


def list_truth_base_history(engine: Engine, user_id: uuid.UUID) -> list[HistoryEntry]:
    """List every version of a user's CV, newest first, without payload.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: The user whose history to list.

    Returns:
        Every `HistoryEntry`, ordered by version descending.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT version, label, created_at FROM cv_truth_base_history "
                "WHERE user_id = :user_id ORDER BY version DESC"
            ),
            {"user_id": user_id},
        ).all()
    return [
        HistoryEntry(version=row.version, label=row.label, created_at=row.created_at)
        for row in rows
    ]


def read_truth_base_version(
    engine: Engine, user_id: uuid.UUID, version: int
) -> StoredTruthBase | None:
    """Read one specific historical version of a user's CV.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: The user whose history to read.
        version: The version number to read.

    Returns:
        The `StoredTruthBase` for that version, or None if no such
        version exists for this user.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                "SELECT version, extracted_markdown, truth_base, label, created_at "
                "FROM cv_truth_base_history "
                "WHERE user_id = :user_id AND version = :version"
            ),
            {"user_id": user_id, "version": version},
        ).one_or_none()
    if row is None:
        return None
    return StoredTruthBase(
        version=row.version,
        extracted_markdown=row.extracted_markdown,
        truth_base=CVTruthBase.model_validate(row.truth_base),
        label=row.label,
        updated_at=row.created_at,
    )


def write_truth_base(
    engine: Engine,
    user_id: uuid.UUID,
    extracted_markdown: str,
    truth_base: CVTruthBase,
    label: str | None = None,
) -> int:
    """Write a new version of a user's CV truth base.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: The user this CV belongs to.
        extracted_markdown: The markdown this version was parsed from
            (or the prior version's markdown, unchanged, for a
            correction-pass save that only edits structured fields).
        truth_base: The truth base to store as the new current version.
        label: An optional name for this version (e.g. "Before I added
            the AI section"), shown when browsing history.

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
                "(user_id, version, extracted_markdown, truth_base, label) "
                "VALUES (:user_id, :version, :markdown, CAST(:truth_base AS jsonb), "
                ":label)"
            ),
            {
                "user_id": user_id,
                "version": new_version,
                "markdown": extracted_markdown,
                "truth_base": truth_base_json,
                "label": label,
            },
        )

        if current:
            conn.execute(
                text(
                    "UPDATE cv_truth_base SET "
                    "version = :version, "
                    "extracted_markdown = :markdown, "
                    "truth_base = CAST(:truth_base AS jsonb), "
                    "label = :label, "
                    "updated_at = now() "
                    "WHERE user_id = :user_id"
                ),
                {
                    "user_id": user_id,
                    "version": new_version,
                    "markdown": extracted_markdown,
                    "truth_base": truth_base_json,
                    "label": label,
                },
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO cv_truth_base "
                    "(user_id, version, extracted_markdown, truth_base, label) "
                    "VALUES (:user_id, :version, :markdown, "
                    "CAST(:truth_base AS jsonb), :label)"
                ),
                {
                    "user_id": user_id,
                    "version": new_version,
                    "markdown": extracted_markdown,
                    "truth_base": truth_base_json,
                    "label": label,
                },
            )
    return new_version
