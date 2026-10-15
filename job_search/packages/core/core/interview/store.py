"""Persistence of the shared question bank (PLAN.md Step 20).

The bank is shared data (no RLS, like intel): every function takes the
app-role engine and no user.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text

from core.interview.config import BEHAVIOURAL_TOPIC


@dataclass(frozen=True)
class NewQuestion:
    """A question to insert.

    Attributes:
        qa_category: The category.
        topic: The topic (`behavioural` for behavioural questions).
        kind: `technical` or `behavioural`.
        difficulty: 1-5.
        question: The question text.
        question_hash: Its dedupe hash.
        model_answer: The model answer.
        tags: Skills the question tests.
        status: `approved` or `needs_review`.
        critic_issue: The critic's objection, if any.
        writer_model: The writer model.
        writer_prompt_version: The writer prompt version.
        critic_model: The critic model.
        critic_prompt_version: The critic prompt version.
    """

    qa_category: str
    topic: str
    kind: str
    difficulty: int
    question: str
    question_hash: str
    model_answer: str
    tags: list[str]
    status: str
    critic_issue: str | None
    writer_model: str
    writer_prompt_version: str
    critic_model: str
    critic_prompt_version: str


@dataclass(frozen=True)
class StoredQuestion:
    """A stored question.

    Attributes:
        id: Row id.
        qa_category: The category.
        topic: The topic.
        kind: `technical` or `behavioural`.
        difficulty: 1-5.
        question: The question text.
        model_answer: The model answer.
        tags: Skills the question tests.
        source: `generated` (Step 20a adds `asked`).
        status: `approved`, `needs_review` or `rejected`.
        critic_issue: The critic's objection, if any.
        created_at: When it was generated.
    """

    id: uuid.UUID
    qa_category: str
    topic: str
    kind: str
    difficulty: int
    question: str
    model_answer: str
    tags: list[str]
    source: str
    status: str
    critic_issue: str | None
    created_at: datetime


_COLUMNS = (
    "id, qa_category, topic, kind, difficulty, question, model_answer, tags, "
    "source, status, critic_issue, created_at"
)


def _stored(row) -> StoredQuestion:  # type: ignore[no-untyped-def]
    """Build a `StoredQuestion` from a row.

    Args:
        row: A row selecting `_COLUMNS`.

    Returns:
        The stored question.
    """
    return StoredQuestion(
        id=row.id,
        qa_category=row.qa_category,
        topic=row.topic,
        kind=row.kind,
        difficulty=row.difficulty,
        question=row.question,
        model_answer=row.model_answer,
        tags=list(row.tags or []),
        source=row.source,
        status=row.status,
        critic_issue=row.critic_issue,
        created_at=row.created_at,
    )


def approved_counts(engine: Engine) -> dict[tuple[str, str], int]:
    """Count approved questions per category and topic.

    Args:
        engine: The app-role engine.

    Returns:
        `{(qa_category, topic): count}`; behavioural ones are under
        `BEHAVIOURAL_TOPIC`.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT qa_category, topic, count(*) AS n FROM interview.question_bank "
                "WHERE status = 'approved' GROUP BY qa_category, topic"
            )
        ).all()
    return {(r.qa_category, r.topic): r.n for r in rows}


def topic_questions(engine: Engine, category: str, topic: str) -> list[str]:
    """List a topic's existing question texts, so a batch avoids repeats.

    Args:
        engine: The app-role engine.
        category: The category.
        topic: The topic.

    Returns:
        Every stored question text (rejected ones included, so a regenerated
        topic does not pay to re-judge a question it already has), oldest first.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT question FROM interview.question_bank WHERE qa_category = :c "
                "AND topic = :t ORDER BY created_at, id"
            ),
            {"c": category, "t": topic},
        ).all()
    return [r.question for r in rows]


def insert_questions(engine: Engine, questions: list[NewQuestion]) -> list[NewQuestion]:
    """Insert questions, skipping any that repeat a stored question.

    Args:
        engine: The app-role engine.
        questions: The questions to insert.

    Returns:
        The questions actually inserted (a repeat of a stored question,
        rejected ones included, is skipped and not returned).
    """
    inserted: list[NewQuestion] = []
    with engine.begin() as conn:
        for q in questions:
            result = conn.execute(
                text(
                    "INSERT INTO interview.question_bank (qa_category, topic, kind, "
                    "difficulty, question, question_hash, model_answer, tags, status, "
                    "critic_issue, writer_model, writer_prompt_version, critic_model, "
                    "critic_prompt_version) VALUES (:c, :t, :k, :d, :q, :h, :a, :tags, "
                    ":s, :issue, :wm, :wpv, :cm, :cpv) ON CONFLICT ON CONSTRAINT "
                    "uq_question_bank_category_topic_hash DO NOTHING"
                ),
                {
                    "c": q.qa_category,
                    "t": q.topic,
                    "k": q.kind,
                    "d": q.difficulty,
                    "q": q.question,
                    "h": q.question_hash,
                    "a": q.model_answer,
                    "tags": q.tags,
                    "s": q.status,
                    "issue": q.critic_issue,
                    "wm": q.writer_model,
                    "wpv": q.writer_prompt_version,
                    "cm": q.critic_model,
                    "cpv": q.critic_prompt_version,
                },
            )
            if result.rowcount:
                inserted.append(q)
    return inserted


def reject_topic(engine: Engine, category: str, topic: str) -> int:
    """Mark a topic's approved questions `rejected` (nothing is deleted).

    Args:
        engine: The app-role engine.
        category: The category.
        topic: The topic.

    Returns:
        How many rows were rejected.
    """
    with engine.begin() as conn:
        return conn.execute(
            text(
                "UPDATE interview.question_bank SET status = 'rejected' "
                "WHERE qa_category = :c AND topic = :t AND status = 'approved'"
            ),
            {"c": category, "t": topic},
        ).rowcount


def list_questions(
    engine: Engine, category: str | None = None, status: str = "approved"
) -> list[StoredQuestion]:
    """List stored questions.

    Args:
        engine: The app-role engine.
        category: Restrict to one category, or None for all.
        status: The status to list.

    Returns:
        Matching questions, by category, topic and creation order.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM interview.question_bank WHERE status = :s "
                "AND (CAST(:c AS text) IS NULL OR qa_category = :c) "
                "ORDER BY qa_category, topic, created_at, id"
            ),
            {"s": status, "c": category},
        ).all()
    return [_stored(r) for r in rows]


def get_question(engine: Engine, question_id: uuid.UUID) -> StoredQuestion | None:
    """Read one question.

    Args:
        engine: The app-role engine.
        question_id: The question's id.

    Returns:
        The question, or None.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT {_COLUMNS} FROM interview.question_bank WHERE id = :i"),
            {"i": question_id},
        ).one_or_none()
    return _stored(row) if row else None


def bank_status(engine: Engine) -> list[dict]:
    """Summarise the bank per category and topic.

    Args:
        engine: The app-role engine.

    Returns:
        Dicts with `qa_category`, `topic`, `approved` and `needs_review`
        counts, ordered by category and topic.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT qa_category, topic, "
                "count(*) FILTER (WHERE status = 'approved') AS approved, "
                "count(*) FILTER (WHERE status = 'needs_review') AS needs_review "
                "FROM interview.question_bank GROUP BY qa_category, topic "
                "ORDER BY qa_category, topic"
            )
        ).all()
    return [
        {
            "qa_category": r.qa_category,
            "topic": r.topic,
            "approved": r.approved,
            "needs_review": r.needs_review,
        }
        for r in rows
    ]


__all__ = [
    "BEHAVIOURAL_TOPIC",
    "NewQuestion",
    "StoredQuestion",
    "approved_counts",
    "bank_status",
    "get_question",
    "insert_questions",
    "list_questions",
    "reject_topic",
    "topic_questions",
]
