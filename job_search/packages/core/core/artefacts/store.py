"""Persistence of letters, pitches and their open sentences (PLAN.md Step 19).

Per user via RLS. One row per generation; decisions update a row with a
compare-and-swap on its content so two browser tabs cannot overwrite each other.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Engine, text

from core.artefacts.schema import Kind
from core.db.session import session_scope

TABLES = {
    "cover_letter": "artefacts.artefact_cover_letter",
    "pitch": "artefacts.artefact_pitch",
}


class StaleDecisionError(RuntimeError):
    """A decision no longer applies (changed, already decided, or not visible)."""


@dataclass(frozen=True)
class OpenDraft:
    """A sentence to record as awaiting the user's decision.

    Attributes:
        sentence_id: The sentence's stable id.
        location: A human location, e.g. `evidence:2`.
        kind: `orphan` (no valid source) or `unsupported`.
        text: The sentence.
        claimed_sources: The ids it cited.
        issue: Why it was rejected.
    """

    sentence_id: str
    location: str
    kind: str
    text: str
    claimed_sources: list[str]
    issue: str


@dataclass(frozen=True)
class StoredOpen:
    """A stored open sentence.

    Attributes:
        id: Row id.
        artefact_kind: `cover_letter` or `pitch`.
        artefact_id: The artefact it belongs to.
        sentence_id: The sentence's stable id.
        location: A human location.
        kind: `orphan` or `unsupported`.
        text: The sentence.
        claimed_sources: The ids it cited.
        issue: Why it was rejected.
        status: `pending`, `linked` or `removed`.
        evidence_ref: The source a link attached.
    """

    id: uuid.UUID
    artefact_kind: str
    artefact_id: uuid.UUID
    sentence_id: str
    location: str
    kind: str
    text: str
    claimed_sources: list[str]
    issue: str | None
    status: str
    evidence_ref: str | None


@dataclass(frozen=True)
class StoredArtefact:
    """A stored letter or pitch.

    Attributes:
        id: Row id.
        kind: `cover_letter` or `pitch`.
        job_group_id: The job.
        truth_base_version: The CV version used.
        intel_version: The saved intel version used, or None.
        target_title: The exact title.
        status: `needs_review`, `approved` or `failed`.
        content: The document as JSON, or None for a failed run.
        attempts: Writer attempts made.
        writer_model: The writer model.
        writer_prompt_version: The writer prompt version.
        critic_model: The critic model.
        critic_prompt_version: The critic prompt version.
        usage: Token use and cost.
        error_message: Why it failed.
        created_at: When it was generated.
        open_sentences: Its open sentences, in creation order.
    """

    id: uuid.UUID
    kind: str
    job_group_id: str
    truth_base_version: int
    intel_version: int | None
    target_title: str
    status: str
    content: dict | None
    attempts: int
    writer_model: str | None
    writer_prompt_version: str | None
    critic_model: str | None
    critic_prompt_version: str | None
    usage: dict | None
    error_message: str | None
    created_at: datetime
    open_sentences: list[StoredOpen]


_COLUMNS = (
    "id, job_group_id, truth_base_version, intel_version, target_title, status, "
    "content, attempts, writer_model, writer_prompt_version, critic_model, "
    "critic_prompt_version, usage, error_message, created_at"
)
_OPEN_COLUMNS = (
    "id, artefact_kind, artefact_id, sentence_id, location, kind, text, "
    "claimed_sources, issue, status, evidence_ref"
)


def _open(row) -> StoredOpen:  # type: ignore[no-untyped-def]
    """Build a `StoredOpen` from a row.

    Args:
        row: A row selecting `_OPEN_COLUMNS`.

    Returns:
        The stored open sentence.
    """
    return StoredOpen(
        id=row.id,
        artefact_kind=row.artefact_kind,
        artefact_id=row.artefact_id,
        sentence_id=row.sentence_id,
        location=row.location,
        kind=row.kind,
        text=row.text,
        claimed_sources=list(row.claimed_sources or []),
        issue=row.issue,
        status=row.status,
        evidence_ref=row.evidence_ref,
    )


def _artefact(kind: str, row, opens: list[StoredOpen]) -> StoredArtefact:  # type: ignore[no-untyped-def]
    """Build a `StoredArtefact` from a row.

    Args:
        kind: `cover_letter` or `pitch`.
        row: A row selecting `_COLUMNS`.
        opens: Its open sentences.

    Returns:
        The stored artefact.
    """
    return StoredArtefact(
        id=row.id,
        kind=kind,
        job_group_id=row.job_group_id,
        truth_base_version=row.truth_base_version,
        intel_version=row.intel_version,
        target_title=row.target_title,
        status=row.status,
        content=row.content,
        attempts=row.attempts,
        writer_model=row.writer_model,
        writer_prompt_version=row.writer_prompt_version,
        critic_model=row.critic_model,
        critic_prompt_version=row.critic_prompt_version,
        usage=row.usage,
        error_message=row.error_message,
        created_at=row.created_at,
        open_sentences=opens,
    )


def save_artefact(
    engine: Engine,
    user_id: uuid.UUID,
    kind: Kind,
    *,
    job_group_id: str,
    truth_base_version: int,
    intel_version: int | None,
    target_title: str,
    status: str,
    document: BaseModel | None,
    attempts: int,
    writer_model: str | None,
    writer_prompt_version: str | None,
    critic_model: str | None,
    critic_prompt_version: str | None,
    usage: dict | None,
    error_message: str | None,
    open_sentences: list[OpenDraft],
) -> uuid.UUID:
    """Save one generation and its open sentences atomically.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        kind: `cover_letter` or `pitch`.
        job_group_id: The job.
        truth_base_version: The CV version used.
        intel_version: The saved intel version used, or None.
        target_title: The exact title.
        status: `needs_review`, `approved` or `failed`.
        document: The document, or None for a failed run.
        attempts: Writer attempts made.
        writer_model: The writer model.
        writer_prompt_version: The writer prompt version.
        critic_model: The critic model.
        critic_prompt_version: The critic prompt version.
        usage: Token use and cost.
        error_message: Why it failed.
        open_sentences: Sentences awaiting a decision.

    Returns:
        The new artefact's id.
    """
    with session_scope(engine, user_id=user_id) as conn:
        artefact_id = conn.execute(
            text(
                f"INSERT INTO {TABLES[kind]} (user_id, job_group_id, "
                "truth_base_version, intel_version, target_title, status, content, "
                "attempts, writer_model, writer_prompt_version, critic_model, "
                "critic_prompt_version, usage, error_message) VALUES (:u, :j, :tb, "
                ":iv, :title, :status, CAST(:content AS jsonb), :attempts, :wm, :wpv, "
                ":cm, :cpv, CAST(:usage AS jsonb), :err) RETURNING id"
            ),
            {
                "u": user_id,
                "j": job_group_id,
                "tb": truth_base_version,
                "iv": intel_version,
                "title": target_title,
                "status": status,
                "content": document.model_dump_json() if document else None,
                "attempts": attempts,
                "wm": writer_model,
                "wpv": writer_prompt_version,
                "cm": critic_model,
                "cpv": critic_prompt_version,
                "usage": json.dumps(usage) if usage is not None else None,
                "err": error_message,
            },
        ).scalar_one()
        for draft in open_sentences:
            conn.execute(
                text(
                    "INSERT INTO artefacts.artefact_open_sentence (user_id, "
                    "artefact_kind, artefact_id, sentence_id, location, kind, text, "
                    "claimed_sources, issue) VALUES (:u, :k, :a, :sid, :loc, :kind, "
                    ":text, :src, :issue)"
                ),
                {
                    "u": user_id,
                    "k": kind,
                    "a": artefact_id,
                    "sid": draft.sentence_id,
                    "loc": draft.location,
                    "kind": draft.kind,
                    "text": draft.text,
                    "src": draft.claimed_sources,
                    "issue": draft.issue,
                },
            )
    return artefact_id


def _opens(conn, artefact_id: uuid.UUID) -> list[StoredOpen]:  # type: ignore[no-untyped-def]
    """Read an artefact's open sentences.

    Args:
        conn: An open connection.
        artefact_id: The artefact.

    Returns:
        Its open sentences, in creation order.
    """
    rows = conn.execute(
        text(
            f"SELECT {_OPEN_COLUMNS} FROM artefacts.artefact_open_sentence "
            "WHERE artefact_id = :a ORDER BY created_at, id"
        ),
        {"a": artefact_id},
    ).all()
    return [_open(r) for r in rows]


def read_latest(
    engine: Engine, user_id: uuid.UUID, kind: Kind, job_group_id: str
) -> StoredArtefact | None:
    """Read the user's newest artefact of a kind for a job.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        kind: `cover_letter` or `pitch`.
        job_group_id: The job.

    Returns:
        The newest row with its open sentences, or None.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM {TABLES[kind]} WHERE job_group_id = :j "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"j": job_group_id},
        ).one_or_none()
        return _artefact(kind, row, _opens(conn, row.id)) if row else None


def read_artefact(
    engine: Engine, user_id: uuid.UUID, kind: Kind, artefact_id: uuid.UUID
) -> StoredArtefact | None:
    """Read one artefact by id.

    Args:
        engine: The app-role engine.
        user_id: The owner (RLS hides other users' rows).
        kind: `cover_letter` or `pitch`.
        artefact_id: The artefact.

    Returns:
        The row with its open sentences, or None.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(f"SELECT {_COLUMNS} FROM {TABLES[kind]} WHERE id = :i"),
            {"i": artefact_id},
        ).one_or_none()
        return _artefact(kind, row, _opens(conn, row.id)) if row else None


def read_open_sentence(
    engine: Engine, user_id: uuid.UUID, open_id: uuid.UUID
) -> StoredOpen | None:
    """Read one open sentence.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        open_id: The open sentence's row id.

    Returns:
        The open sentence, or None.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                f"SELECT {_OPEN_COLUMNS} FROM artefacts.artefact_open_sentence "
                "WHERE id = :i"
            ),
            {"i": open_id},
        ).one_or_none()
    return _open(row) if row else None


def save_decision(
    engine: Engine,
    user_id: uuid.UUID,
    *,
    open_sentence: StoredOpen,
    status: str,
    evidence_ref: str | None,
    document: BaseModel,
    base_document: BaseModel,
) -> str:
    """Persist one decision and the updated document atomically.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        open_sentence: The open sentence being decided.
        status: `linked` or `removed`.
        evidence_ref: The attached source, for a link.
        document: The document after the decision.
        base_document: The document the decision was computed from; the
            stored content must still equal it (compare-and-swap).

    Returns:
        The artefact's new status: `approved` when nothing is left pending,
        else `needs_review`.

    Raises:
        StaleDecisionError: If the artefact is not visible, its content
            changed, or the sentence is no longer pending.
    """
    table = TABLES[open_sentence.artefact_kind]
    with session_scope(engine, user_id=user_id) as conn:
        locked = conn.execute(
            text(f"SELECT content FROM {table} WHERE id = :a FOR UPDATE"),
            {"a": open_sentence.artefact_id},
        ).one_or_none()
        if locked is None or locked.content is None:
            raise StaleDecisionError("this document is not available - reload")
        if locked.content != json.loads(base_document.model_dump_json()):
            raise StaleDecisionError(
                "the document changed since you opened it - reload"
            )
        updated = conn.execute(
            text(
                "UPDATE artefacts.artefact_open_sentence SET status = :s, "
                "evidence_ref = :ref, decided_at = now() WHERE id = :i AND "
                "artefact_id = :a AND status = 'pending'"
            ),
            {
                "s": status,
                "ref": evidence_ref,
                "i": open_sentence.id,
                "a": open_sentence.artefact_id,
            },
        )
        if updated.rowcount == 0:
            raise StaleDecisionError("this sentence was already decided - reload")
        pending = conn.execute(
            text(
                "SELECT count(*) FROM artefacts.artefact_open_sentence "
                "WHERE artefact_id = :a AND status = 'pending'"
            ),
            {"a": open_sentence.artefact_id},
        ).scalar_one()
        new_status = "needs_review" if pending else "approved"
        conn.execute(
            text(
                f"UPDATE {table} SET content = CAST(:c AS jsonb), status = :s, "
                "updated_at = now() WHERE id = :a"
            ),
            {
                "c": document.model_dump_json(),
                "s": new_status,
                "a": open_sentence.artefact_id,
            },
        )
    return new_status
