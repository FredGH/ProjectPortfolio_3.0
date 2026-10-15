"""Drilling, answers and gaps for one user (PLAN.md Step 20)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

from core.artefacts.context import build_pack
from core.artefacts.service import NoCvError
from core.cv.store import read_truth_base
from core.interview import progress, store
from core.interview.gaps import load_alias_groups, score_question
from core.interview.personal import AnswerDraft, draft_answer
from core.interview.queue import QueueItem, order_queue
from core.interview.sm2 import Sm2State, review
from core.llm.types import LLMAdapter


class AnswerExistsError(RuntimeError):
    """The user wrote this answer themselves and did not confirm replacing it."""


class UnknownQuestionError(LookupError):
    """The question does not exist or is not approved."""


def refresh_gaps(
    engine: Engine, user_id: uuid.UUID, category: str | None = None
) -> int:
    """Score the user's CV gaps, recomputing only when the CV version changed.

    Args:
        engine: The app-role engine.
        user_id: The user.
        category: Restrict to one qa_category, or None for all.

    Returns:
        How many questions were scored this call.

    Raises:
        NoCvError: If the user has no CV.
    """
    stored = read_truth_base(engine, user_id)
    if stored is None:
        raise NoCvError("add your CV first")
    mine = progress.list_progress(engine, user_id)
    groups = load_alias_groups()
    scores: dict[uuid.UUID, float] = {}
    for question in store.list_questions(engine, category):
        if question.kind != "technical":
            continue
        have = mine.get(question.id)
        if have and have.truth_base_version_scored == stored.version:
            continue
        scores[question.id] = score_question(
            question.tags, question.difficulty, stored.truth_base, groups
        ).score
    if scores:
        progress.save_gap_scores(engine, user_id, scores, stored.version)
    return len(scores)


def build_queue(
    engine: Engine,
    user_id: uuid.UUID,
    category: str | None = None,
    limit: int | None = 30,
    now: datetime | None = None,
) -> list[store.StoredQuestion]:
    """Order the user's approved questions for drilling.

    Args:
        engine: The app-role engine.
        user_id: The user.
        category: Restrict to one qa_category, or None for all.
        limit: Keep only this many, or None for all.
        now: The current time (default: now).

    Returns:
        Questions: due first, then never-seen technical by CV gap, then
        never-seen behavioural.
    """
    mine = progress.list_progress(engine, user_id)
    questions = {q.id: q for q in store.list_questions(engine, category)}
    items = [
        QueueItem(
            question_id=str(q.id),
            kind=q.kind,
            next_review=(mine[q.id].state.next_review if q.id in mine else None),
            gap_score=(mine[q.id].cv_gap_score or 0.0) if q.id in mine else 0.0,
        )
        for q in questions.values()
    ]
    ordered = order_queue(items, now or datetime.now(UTC), limit)
    return [questions[uuid.UUID(i.question_id)] for i in ordered]


def review_question(
    engine: Engine,
    user_id: uuid.UUID,
    question_id: uuid.UUID,
    quality: int,
    now: datetime | None = None,
) -> Sm2State:
    """Record a self-rating and schedule the next review.

    Args:
        engine: The app-role engine.
        user_id: The user.
        question_id: The question.
        quality: Self-rating 0-5.
        now: The review time (default: now).

    Returns:
        The new SM-2 state.

    Raises:
        UnknownQuestionError: If the question is not an approved one.
        ValueError: If `quality` is not 0-5.
    """
    if store.get_question(engine, question_id) is None:
        raise UnknownQuestionError(str(question_id))
    now = now or datetime.now(UTC)
    have = progress.get_progress(engine, user_id, question_id)
    state = have.state if have else Sm2State(2.5, 0, 0)
    new = review(state, quality, now)
    progress.record_review(engine, user_id, question_id, new, quality, now)
    return new


def draft_personal_answer(
    engine: Engine,
    user_id: uuid.UUID,
    question_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    replace: bool = False,
    config_path: Path | None = None,
) -> AnswerDraft:
    """Draft the user's answer from their CV and store it.

    Args:
        engine: The app-role engine.
        user_id: The user.
        question_id: The question.
        adapters: LLM adapters by provider.
        replace: Allow replacing an answer the user wrote themselves.
        config_path: Task-config override (tests).

    Returns:
        The draft. A `failed` draft stores nothing.

    Raises:
        UnknownQuestionError: If the question is not an approved one.
        NoCvError: If the user has no CV.
        AnswerExistsError: If the user wrote their own answer and `replace`
            is False.
    """
    question = store.get_question(engine, question_id)
    if question is None or question.status != "approved":
        raise UnknownQuestionError(str(question_id))
    have = progress.get_progress(engine, user_id, question_id)
    if have and have.answer_status == "written" and not replace:
        raise AnswerExistsError("you wrote this answer yourself; confirm to replace it")
    stored = read_truth_base(engine, user_id)
    if stored is None:
        raise NoCvError("add your CV first")
    draft = draft_answer(
        question.question,
        question.model_answer,
        build_pack(stored.truth_base),
        adapters=adapters,
        config_path=config_path,
    )
    if draft.status != "failed":
        progress.save_draft(
            engine, user_id, question_id, draft.text, draft.status, draft.open_sentences
        )
    return draft


def decide_sentence(
    engine: Engine,
    user_id: uuid.UUID,
    open_id: uuid.UUID,
    action: str,
    evidence_ref: str | None,
) -> str:
    """Apply a link / remove decision to an untraced sentence.

    Args:
        engine: The app-role engine.
        user_id: The user.
        open_id: The open sentence's row id.
        action: `link` or `remove`.
        evidence_ref: For a link, a CV evidence id.

    Returns:
        The answer's new status.

    Raises:
        progress.StaleDecisionError: If it no longer applies.
        ValueError: For an unknown action or a link to an id outside the CV.
        NoCvError: If the user has no CV.
    """
    if action not in ("link", "remove"):
        raise ValueError(f"unknown action {action!r}")
    open_sentence = progress.read_open(engine, user_id, open_id)
    if open_sentence is None:
        raise progress.StaleDecisionError("this sentence is not available - reload")
    if action == "link":
        stored = read_truth_base(engine, user_id)
        if stored is None:
            raise NoCvError("add your CV first")
        if not evidence_ref or evidence_ref not in build_pack(stored.truth_base).items:
            raise ValueError("link the sentence to a source from your CV")
    return progress.decide_open(engine, user_id, open_sentence, action, evidence_ref)
