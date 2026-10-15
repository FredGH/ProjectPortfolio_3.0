"""Per-user interview progress (PLAN.md Step 20): answers, SM-2 and open sentences.

Every function is scoped to one user by RLS via `session_scope`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.interview.personal import OpenDraft
from core.interview.sm2 import Sm2State


class WrittenAnswerError(RuntimeError):
    """A draft would replace an answer the user wrote themselves."""


class StaleDecisionError(RuntimeError):
    """A decision no longer applies (already decided, or the text changed)."""


@dataclass(frozen=True)
class Progress:
    """One user's state for one question.

    Attributes:
        question_id: The question.
        personal_answer: The user's answer text, or None.
        answer_status: `none`, `drafted`, `needs_review` or `written`.
        state: The SM-2 state.
        last_quality: The last self-rating, or None.
        cv_gap_score: The CV-gap score, or None when not scored.
        truth_base_version_scored: The CV version the score came from.
    """

    question_id: uuid.UUID
    personal_answer: str | None
    answer_status: str
    state: Sm2State
    last_quality: int | None
    cv_gap_score: float | None
    truth_base_version_scored: int | None


@dataclass(frozen=True)
class StoredOpenAnswer:
    """A stored untraced sentence of a drafted answer.

    Attributes:
        id: Row id.
        question_id: The question.
        sentence_id: The sentence's stable id.
        text: The sentence.
        claimed_sources: The ids it cited.
        issue: Why it was rejected.
        status: `pending`, `linked` or `removed`.
        evidence_ref: The source a link attached.
    """

    id: uuid.UUID
    question_id: uuid.UUID
    sentence_id: str
    text: str
    claimed_sources: list[str]
    issue: str | None
    status: str
    evidence_ref: str | None


_COLUMNS = (
    "question_id, personal_answer, answer_status, ease_factor, interval_days, "
    "repetitions, next_review, last_quality, cv_gap_score, truth_base_version_scored"
)
_OPEN_COLUMNS = (
    "id, question_id, sentence_id, text, claimed_sources, issue, status, evidence_ref"
)


def _progress(row) -> Progress:  # type: ignore[no-untyped-def]
    """Build a `Progress` from a row.

    Args:
        row: A row selecting `_COLUMNS`.

    Returns:
        The progress.
    """
    return Progress(
        question_id=row.question_id,
        personal_answer=row.personal_answer,
        answer_status=row.answer_status,
        state=Sm2State(
            row.ease_factor, row.interval_days, row.repetitions, row.next_review
        ),
        last_quality=row.last_quality,
        cv_gap_score=row.cv_gap_score,
        truth_base_version_scored=row.truth_base_version_scored,
    )


def _open(row) -> StoredOpenAnswer:  # type: ignore[no-untyped-def]
    """Build a `StoredOpenAnswer` from a row.

    Args:
        row: A row selecting `_OPEN_COLUMNS`.

    Returns:
        The stored open sentence.
    """
    return StoredOpenAnswer(
        id=row.id,
        question_id=row.question_id,
        sentence_id=row.sentence_id,
        text=row.text,
        claimed_sources=list(row.claimed_sources or []),
        issue=row.issue,
        status=row.status,
        evidence_ref=row.evidence_ref,
    )


def list_progress(engine: Engine, user_id: uuid.UUID) -> dict[uuid.UUID, Progress]:
    """Read all of a user's progress rows.

    Args:
        engine: The app-role engine.
        user_id: The user.

    Returns:
        Progress by question id.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            text(f"SELECT {_COLUMNS} FROM interview.user_question_progress")
        ).all()
    return {r.question_id: _progress(r) for r in rows}


def get_progress(
    engine: Engine, user_id: uuid.UUID, question_id: uuid.UUID
) -> Progress | None:
    """Read a user's progress on one question.

    Args:
        engine: The app-role engine.
        user_id: The user.
        question_id: The question.

    Returns:
        The progress, or None when the user has not touched the question.
    """
    return list_progress(engine, user_id).get(question_id)


def save_gap_scores(
    engine: Engine,
    user_id: uuid.UUID,
    scores: dict[uuid.UUID, float],
    truth_base_version: int,
) -> None:
    """Store gap scores, creating progress rows as needed.

    Args:
        engine: The app-role engine.
        user_id: The user.
        scores: Gap score by question id.
        truth_base_version: The CV version the scores came from.
    """
    with session_scope(engine, user_id=user_id) as conn:
        for question_id, score in scores.items():
            conn.execute(
                text(
                    "INSERT INTO interview.user_question_progress (user_id, "
                    "question_id, cv_gap_score, truth_base_version_scored) "
                    "VALUES (:u, :q, :s, :v) ON CONFLICT (user_id, question_id) "
                    "DO UPDATE SET cv_gap_score = :s, truth_base_version_scored = :v"
                ),
                {"u": user_id, "q": question_id, "s": score, "v": truth_base_version},
            )


def record_review(
    engine: Engine,
    user_id: uuid.UUID,
    question_id: uuid.UUID,
    state: Sm2State,
    quality: int,
    now: datetime,
) -> None:
    """Store the result of one review.

    Args:
        engine: The app-role engine.
        user_id: The user.
        question_id: The question.
        state: The new SM-2 state.
        quality: The self-rating 0-5.
        now: The review time.
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "INSERT INTO interview.user_question_progress (user_id, question_id, "
                "ease_factor, interval_days, repetitions, next_review, last_quality, "
                "last_reviewed_at) VALUES (:u, :q, :e, :i, :r, :n, :ql, :now) "
                "ON CONFLICT (user_id, question_id) DO UPDATE SET ease_factor = :e, "
                "interval_days = :i, repetitions = :r, next_review = :n, "
                "last_quality = :ql, last_reviewed_at = :now"
            ),
            {
                "u": user_id,
                "q": question_id,
                "e": state.ease_factor,
                "i": state.interval_days,
                "r": state.repetitions,
                "n": state.next_review,
                "ql": quality,
                "now": now,
            },
        )


def save_written_answer(
    engine: Engine, user_id: uuid.UUID, question_id: uuid.UUID, answer: str
) -> None:
    """Store the user's own answer; pending open sentences are superseded.

    Args:
        engine: The app-role engine.
        user_id: The user.
        question_id: The question.
        answer: The answer text (an empty string clears it).
    """
    status = "written" if answer.strip() else "none"
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "INSERT INTO interview.user_question_progress (user_id, question_id, "
                "personal_answer, answer_status) VALUES (:u, :q, :a, :s) "
                "ON CONFLICT (user_id, question_id) DO UPDATE SET "
                "personal_answer = :a, answer_status = :s"
            ),
            {"u": user_id, "q": question_id, "a": answer.strip() or None, "s": status},
        )
        conn.execute(
            text(
                "UPDATE interview.personal_answer_open_sentence "
                "SET status = 'removed', decided_at = now() "
                "WHERE question_id = :q AND status = 'pending'"
            ),
            {"q": question_id},
        )


def save_draft(
    engine: Engine,
    user_id: uuid.UUID,
    question_id: uuid.UUID,
    answer: str,
    status: str,
    open_sentences: list[OpenDraft],
    *,
    replace_written: bool = False,
) -> None:
    """Store a drafted answer and its open sentences.

    Args:
        engine: The app-role engine.
        user_id: The user.
        question_id: The question.
        answer: The drafted text.
        status: `drafted` or `needs_review`.
        open_sentences: Sentences awaiting the user's decision.
        replace_written: Allow replacing an answer the user wrote themselves.

    Raises:
        WrittenAnswerError: If the user wrote their own answer meanwhile (the
            draft took seconds) and `replace_written` is False; nothing is
            stored.
    """
    with session_scope(engine, user_id=user_id) as conn:
        saved = conn.execute(
            text(
                "INSERT INTO interview.user_question_progress (user_id, question_id, "
                "personal_answer, answer_status) VALUES (:u, :q, :a, :s) "
                "ON CONFLICT (user_id, question_id) DO UPDATE SET "
                "personal_answer = :a, answer_status = :s "
                "WHERE interview.user_question_progress.answer_status <> 'written' "
                "OR CAST(:replace AS boolean)"
            ),
            {
                "u": user_id,
                "q": question_id,
                "a": answer,
                "s": status,
                "replace": replace_written,
            },
        )
        if saved.rowcount == 0:
            raise WrittenAnswerError("you wrote this answer yourself meanwhile")
        conn.execute(
            text(
                "UPDATE interview.personal_answer_open_sentence "
                "SET status = 'removed', decided_at = now() "
                "WHERE question_id = :q AND status = 'pending'"
            ),
            {"q": question_id},
        )
        for draft in open_sentences:
            conn.execute(
                text(
                    "INSERT INTO interview.personal_answer_open_sentence (user_id, "
                    "question_id, sentence_id, text, claimed_sources, issue) "
                    "VALUES (:u, :q, :sid, :t, :src, :issue)"
                ),
                {
                    "u": user_id,
                    "q": question_id,
                    "sid": draft.sentence_id,
                    "t": draft.text,
                    "src": draft.claimed_sources,
                    "issue": draft.issue,
                },
            )


def list_open(
    engine: Engine, user_id: uuid.UUID, question_id: uuid.UUID
) -> list[StoredOpenAnswer]:
    """List a question's open sentences.

    Args:
        engine: The app-role engine.
        user_id: The user.
        question_id: The question.

    Returns:
        Its open sentences, in creation order.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            text(
                f"SELECT {_OPEN_COLUMNS} FROM interview.personal_answer_open_sentence "
                "WHERE question_id = :q ORDER BY created_at, id"
            ),
            {"q": question_id},
        ).all()
    return [_open(r) for r in rows]


def read_open(
    engine: Engine, user_id: uuid.UUID, open_id: uuid.UUID
) -> StoredOpenAnswer | None:
    """Read one open sentence.

    Args:
        engine: The app-role engine.
        user_id: The user.
        open_id: The open sentence's row id.

    Returns:
        The open sentence, or None.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                f"SELECT {_OPEN_COLUMNS} FROM interview.personal_answer_open_sentence "
                "WHERE id = :i"
            ),
            {"i": open_id},
        ).one_or_none()
    return _open(row) if row else None


def decide_open(
    engine: Engine,
    user_id: uuid.UUID,
    open_sentence: StoredOpenAnswer,
    action: str,
    evidence_ref: str | None,
) -> str:
    """Apply a link / remove decision atomically.

    Args:
        engine: The app-role engine.
        user_id: The user.
        open_sentence: The open sentence being decided.
        action: `link` (the user vouches for it) or `remove` (cut it from the
            answer text).
        evidence_ref: For a link, the source the user attached.

    Returns:
        The answer's new status: `drafted` when nothing is left pending,
        else `needs_review`.

    Raises:
        StaleDecisionError: If the sentence was already decided, is not
            visible to this user, or a removal's text is no longer in the
            answer.
    """
    with session_scope(engine, user_id=user_id) as conn:
        progress = conn.execute(
            text(
                "SELECT personal_answer FROM interview.user_question_progress "
                "WHERE question_id = :q FOR UPDATE"
            ),
            {"q": open_sentence.question_id},
        ).one_or_none()
        if progress is None:
            raise StaleDecisionError("this answer is not available - reload")
        answer = progress.personal_answer or ""
        if action == "remove":
            if open_sentence.text not in answer:
                raise StaleDecisionError("the answer changed - reload")
            answer = " ".join(answer.replace(open_sentence.text, "", 1).split())
        updated = conn.execute(
            text(
                "UPDATE interview.personal_answer_open_sentence SET status = :s, "
                "evidence_ref = :ref, decided_at = now() WHERE id = :i AND "
                "status = 'pending'"
            ),
            {
                "s": "linked" if action == "link" else "removed",
                "ref": evidence_ref,
                "i": open_sentence.id,
            },
        )
        if updated.rowcount == 0:
            raise StaleDecisionError("this sentence was already decided - reload")
        pending = conn.execute(
            text(
                "SELECT count(*) FROM interview.personal_answer_open_sentence "
                "WHERE question_id = :q AND status = 'pending'"
            ),
            {"q": open_sentence.question_id},
        ).scalar_one()
        status = "needs_review" if pending else "drafted"
        conn.execute(
            text(
                "UPDATE interview.user_question_progress SET personal_answer = :a, "
                "answer_status = :s WHERE question_id = :q"
            ),
            {"a": answer, "s": status, "q": open_sentence.question_id},
        )
    return status
