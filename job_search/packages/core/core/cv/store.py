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

from sqlalchemy import Connection, Engine, text

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
        extraction_seconds: How long this version's extraction
            pipeline took end-to-end (Docling parse through the DB
            write), or None for a version written by a correction-pass
            save rather than an extraction.
        updated_at: When this version was written.
    """

    version: int
    extracted_markdown: str
    truth_base: CVTruthBase
    label: str | None
    extraction_seconds: float | None
    updated_at: datetime


@dataclass(frozen=True)
class HistoryEntry:
    """One version's listing-page summary — no markdown/truth-base
    payload, so browsing history stays cheap regardless of CV size.

    Attributes:
        version: This version's number.
        label: The name given to this version at save time, if any.
        extraction_seconds: How long this version's extraction
            pipeline took end-to-end, or None for a correction-pass
            save.
        created_at: When this version was written.
    """

    version: int
    label: str | None
    extraction_seconds: float | None
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
                "SELECT version, extracted_markdown, truth_base, label, "
                "extraction_seconds, updated_at "
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
        extraction_seconds=row.extraction_seconds,
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
                "SELECT version, label, extraction_seconds, created_at "
                "FROM cv_truth_base_history "
                "WHERE user_id = :user_id ORDER BY version DESC"
            ),
            {"user_id": user_id},
        ).all()
    return [
        HistoryEntry(
            version=row.version,
            label=row.label,
            extraction_seconds=row.extraction_seconds,
            created_at=row.created_at,
        )
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
                "SELECT version, extracted_markdown, truth_base, label, "
                "extraction_seconds, created_at "
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
        extraction_seconds=row.extraction_seconds,
        updated_at=row.created_at,
    )


_MAX_VERSIONS_PER_LABEL = 3


def _prune_named_history(conn: Connection, user_id: uuid.UUID, label: str) -> None:
    """Keep only the newest `_MAX_VERSIONS_PER_LABEL` versions per label.

    Scoped to `label`, never to unlabeled ("Before I added the AI
    section" but not None) history — a name only means something once
    reused, so repeatedly saving under the same name (e.g. retrying an
    extraction and naming every attempt "Latest extraction") doesn't
    accumulate history forever. The version just written by this call
    is always among the newest for its own label, so it's never the
    one pruned.

    Args:
        conn: The open connection inside `write_truth_base`'s
            transaction — runs as part of the same write, never on its
            own.
        user_id: The user whose history to prune.
        label: The label whose versions to cap.
    """
    conn.execute(
        text(
            "DELETE FROM cv_truth_base_history "
            "WHERE user_id = :user_id AND label = :label "
            "AND version NOT IN ("
            "    SELECT version FROM cv_truth_base_history "
            "    WHERE user_id = :user_id AND label = :label "
            "    ORDER BY version DESC LIMIT :keep"
            ")"
        ),
        {"user_id": user_id, "label": label, "keep": _MAX_VERSIONS_PER_LABEL},
    )


def write_truth_base(
    engine: Engine,
    user_id: uuid.UUID,
    extracted_markdown: str,
    truth_base: CVTruthBase,
    label: str | None = None,
    extraction_seconds: float | None = None,
) -> int:
    """Write a new version of a user's CV truth base.

    When `label` is given, also prunes that label's own history down
    to `_MAX_VERSIONS_PER_LABEL` — see `_prune_named_history`.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: The user this CV belongs to.
        extracted_markdown: The markdown this version was parsed from
            (or the prior version's markdown, unchanged, for a
            correction-pass save that only edits structured fields).
        truth_base: The truth base to store as the new current version.
        label: An optional name for this version (e.g. "Before I added
            the AI section"), shown when browsing history.
        extraction_seconds: How long this version's extraction
            pipeline took end-to-end, if this write came from one —
            omitted (None) for a correction-pass save.

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
                "(user_id, version, extracted_markdown, truth_base, label, "
                "extraction_seconds) "
                "VALUES (:user_id, :version, :markdown, CAST(:truth_base AS jsonb), "
                ":label, :extraction_seconds)"
            ),
            {
                "user_id": user_id,
                "version": new_version,
                "markdown": extracted_markdown,
                "truth_base": truth_base_json,
                "label": label,
                "extraction_seconds": extraction_seconds,
            },
        )

        if label:
            _prune_named_history(conn, user_id, label)

        if current:
            conn.execute(
                text(
                    "UPDATE cv_truth_base SET "
                    "version = :version, "
                    "extracted_markdown = :markdown, "
                    "truth_base = CAST(:truth_base AS jsonb), "
                    "label = :label, "
                    "extraction_seconds = :extraction_seconds, "
                    "updated_at = now() "
                    "WHERE user_id = :user_id"
                ),
                {
                    "user_id": user_id,
                    "version": new_version,
                    "markdown": extracted_markdown,
                    "truth_base": truth_base_json,
                    "label": label,
                    "extraction_seconds": extraction_seconds,
                },
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO cv_truth_base "
                    "(user_id, version, extracted_markdown, truth_base, label, "
                    "extraction_seconds) "
                    "VALUES (:user_id, :version, :markdown, "
                    "CAST(:truth_base AS jsonb), :label, :extraction_seconds)"
                ),
                {
                    "user_id": user_id,
                    "version": new_version,
                    "markdown": extracted_markdown,
                    "truth_base": truth_base_json,
                    "label": label,
                    "extraction_seconds": extraction_seconds,
                },
            )
    return new_version
