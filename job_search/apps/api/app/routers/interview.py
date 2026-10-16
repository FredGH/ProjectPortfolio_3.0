"""Interview-prep endpoints (PLAN.md Step 20).

The bank is shared; answers, progress and decisions are per user via
`get_current_user_id`. Generation and drafting call Claude synchronously.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Literal

from app.dependencies import get_app_db_engine, get_llm_adapters
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from core.artefacts.service import NoCvError
from core.cv.store import read_truth_base
from core.db.session import get_current_user_id
from core.interview import practice, progress, service, store
from core.interview.anki import export_anki
from core.interview.config import (
    BEHAVIOURAL_TOPIC,
    TopicsConfig,
    WorkItem,
    load_topics_config,
)
from core.llm.types import LLMAdapter

router = APIRouter()
_GENERATING: set[str] = set()
_GENERATING_LOCK = threading.Lock()


class BankRow(BaseModel):
    """Generation status of one topic."""

    qa_category: str
    topic: str
    approved: int
    needs_review: int
    regenerate_cost_usd: float


class WorkRow(BaseModel):
    """A topic that still needs questions."""

    category: str
    topic: str
    kind: str
    missing: int
    estimated_cost_usd: float


class BankView(BaseModel):
    """The bank's status and what generating the rest would cost."""

    rows: list[BankRow]
    work: list[WorkRow]
    unconfigured: list[str]
    estimated_cost_usd: float


class GenerateRequest(BaseModel):
    """Request body for bank generation (it spends money, so it must confirm).

    Attributes:
        category: The role family.
        topic: One topic of it, or None for every missing topic.
        confirm: Must be true: the caller has shown the user the cost.
        force: Regenerate `topic`: its approved questions are marked rejected
            (nothing is deleted) and the topic is generated again.
    """

    category: str
    topic: str | None = None
    confirm: Literal[True]
    force: bool = False


class TopicResultModel(BaseModel):
    """What generating one topic achieved."""

    category: str
    topic: str
    approved: int
    needs_review: int
    rounds: int
    error: str | None


class OpenSentenceModel(BaseModel):
    """A sentence of a drafted answer awaiting the user's decision."""

    id: uuid.UUID
    text: str
    issue: str | None
    status: str


class QuestionModel(BaseModel):
    """A question with the user's state."""

    id: uuid.UUID
    qa_category: str
    topic: str
    kind: str
    difficulty: int
    question: str
    model_answer: str
    tags: list[str]
    personal_answer: str | None
    answer_status: str
    next_review: datetime | None
    cv_gap_score: float | None
    open_sentences: list[OpenSentenceModel]


class ReviewRequest(BaseModel):
    """Request body for a self-rating."""

    quality: int = Field(ge=0, le=5)


class AnswerRequest(BaseModel):
    """Request body for saving the user's own answer."""

    answer: str


class DraftRequest(BaseModel):
    """Request body for drafting an answer from the CV."""

    replace: bool = False


class DecisionRequest(BaseModel):
    """Request body for a link / remove decision."""

    action: Literal["link", "remove"]
    evidence_ref: str | None = None


class EvidenceModel(BaseModel):
    """One CV item a sentence can be linked to."""

    id: str
    label: str


def _question(
    engine: Engine, user_id: uuid.UUID, q: store.StoredQuestion
) -> QuestionModel:
    """Build the API model of a question for a user.

    Args:
        engine: The app-role engine.
        user_id: The user.
        q: The stored question.

    Returns:
        The question with the user's answer, schedule and open sentences.
    """
    mine = progress.get_progress(engine, user_id, q.id)
    opens = progress.list_open(engine, user_id, q.id)
    return QuestionModel(
        id=q.id,
        qa_category=q.qa_category,
        topic=q.topic,
        kind=q.kind,
        difficulty=q.difficulty,
        question=q.question,
        model_answer=q.model_answer,
        tags=q.tags,
        personal_answer=mine.personal_answer if mine else None,
        answer_status=mine.answer_status if mine else "none",
        next_review=mine.state.next_review if mine else None,
        cv_gap_score=mine.cv_gap_score if mine else None,
        open_sentences=[
            OpenSentenceModel(id=o.id, text=o.text, issue=o.issue, status=o.status)
            for o in opens
        ],
    )


def _regenerate_cost(config: TopicsConfig, topic: str) -> float:
    """Estimate what regenerating one topic from scratch costs.

    Args:
        config: The topics config.
        topic: The topic (or `behavioural`).

    Returns:
        A rough dollar figure for generating its full target again.
    """
    behavioural = topic == BEHAVIOURAL_TOPIC
    missing = (
        config.behavioural_per_category if behavioural else config.target_per_topic
    )
    return service.estimate_cost([WorkItem("", topic, "technical", missing)])


def _approved(engine: Engine, question_id: uuid.UUID) -> store.StoredQuestion:
    """Load an approved question or answer 404.

    Args:
        engine: The app-role engine.
        question_id: The question.

    Returns:
        The question.

    Raises:
        HTTPException: 404 when it does not exist or is not approved.
    """
    q = store.get_question(engine, question_id)
    if q is None or q.status != "approved":
        raise HTTPException(status_code=404, detail="unknown question")
    return q


@router.get("/interview/bank", response_model=BankView)
def get_bank(engine: Engine = Depends(get_app_db_engine)) -> BankView:
    """Show the bank's status and what is left to generate.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        Counts per topic, the incomplete topics and a cost estimate.
    """
    work, unconfigured = service.pending_work(engine)
    config = load_topics_config()
    return BankView(
        rows=[
            BankRow(**r, regenerate_cost_usd=_regenerate_cost(config, r["topic"]))
            for r in store.bank_status(engine)
        ],
        work=[
            WorkRow(**w.__dict__, estimated_cost_usd=service.estimate_cost([w]))
            for w in work
        ],
        unconfigured=unconfigured,
        estimated_cost_usd=service.estimate_cost(work),
    )


@router.post("/interview/bank/generate", response_model=list[TopicResultModel])
def generate_bank(
    body: GenerateRequest,
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> list[TopicResultModel]:
    """Generate what is missing for a category (or one topic of it).

    Args:
        body: The category and optionally one topic.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        One result per topic generated; empty when nothing was missing.

    Raises:
        HTTPException: 409 when this role family is already being generated;
            422 when `confirm` is not true or a regeneration names no topic.
    """
    if body.force and not body.topic:
        raise HTTPException(status_code=422, detail="regenerating needs a topic")
    with _GENERATING_LOCK:
        if body.category in _GENERATING:
            raise HTTPException(
                status_code=409, detail="this role family is already being generated"
            )
        _GENERATING.add(body.category)
    try:
        work, _ = service.pending_work(
            engine, category=body.category, topic=body.topic, force=body.force
        )
        if body.force and work:
            store.reject_topic(engine, body.category, body.topic or "")
        results = service.generate_work(engine, work, adapters=adapters)
    finally:
        with _GENERATING_LOCK:
            _GENERATING.discard(body.category)
    return [
        TopicResultModel(
            category=r.category,
            topic=r.topic,
            approved=r.approved,
            needs_review=r.needs_review,
            rounds=r.rounds,
            error=r.error,
        )
        for r in results
    ]


@router.get("/interview/queue", response_model=list[QuestionModel])
def get_queue(
    category: str | None = None,
    limit: int = Query(30, ge=1, le=200),
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[QuestionModel]:
    """Questions to drill now: due first, then the biggest CV gaps.

    Args:
        category: Restrict to one qa_category.
        limit: How many to return.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The ordered questions with the user's state. Gap scores are refreshed
        first when the user has a CV.
    """
    try:
        practice.refresh_gaps(engine, user_id, category)
    except NoCvError:
        pass
    return [
        _question(engine, user_id, q)
        for q in practice.build_queue(engine, user_id, category, limit)
    ]


@router.get("/interview/gaps", response_model=list[QuestionModel])
def get_gaps(
    category: str | None = None,
    limit: int = Query(30, ge=1, le=200),
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[QuestionModel]:
    """The technical questions the user is most likely to fumble.

    Args:
        category: Restrict to one qa_category.
        limit: How many to return.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Technical questions by descending CV-gap score.

    Raises:
        HTTPException: 409 when the user has no CV.
    """
    try:
        practice.refresh_gaps(engine, user_id, category)
    except NoCvError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    mine = progress.list_progress(engine, user_id)
    technical = [
        q for q in store.list_questions(engine, category) if q.kind == "technical"
    ]
    technical.sort(
        key=lambda q: -((mine[q.id].cv_gap_score or 0.0) if q.id in mine else 0.0)
    )
    return [_question(engine, user_id, q) for q in technical[:limit]]


@router.get("/interview/evidence", response_model=list[EvidenceModel])
def get_evidence(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[EvidenceModel]:
    """List the CV items a drafted sentence can be linked to.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The user's CV evidence ids with labels.

    Raises:
        HTTPException: 409 when the user has no CV.
    """
    from core.artefacts.context import build_pack

    stored = read_truth_base(engine, user_id)
    if stored is None:
        raise HTTPException(status_code=409, detail="add your CV first")
    return [
        EvidenceModel(id=e.id, label=e.label)
        for e in build_pack(stored.truth_base).items.values()
    ]


@router.post("/interview/questions/{question_id}/review", response_model=QuestionModel)
def review_question(
    question_id: uuid.UUID,
    body: ReviewRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> QuestionModel:
    """Record a self-rating and schedule the next review.

    Args:
        question_id: The question.
        body: The 0-5 rating.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The question with its new schedule.
    """
    q = _approved(engine, question_id)
    practice.review_question(engine, user_id, question_id, body.quality)
    return _question(engine, user_id, q)


@router.put("/interview/questions/{question_id}/answer", response_model=QuestionModel)
def save_answer(
    question_id: uuid.UUID,
    body: AnswerRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> QuestionModel:
    """Save the user's own answer.

    Args:
        question_id: The question.
        body: The answer text.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The question with the saved answer.
    """
    q = _approved(engine, question_id)
    progress.save_written_answer(engine, user_id, question_id, body.answer)
    return _question(engine, user_id, q)


@router.post("/interview/questions/{question_id}/draft", response_model=QuestionModel)
def draft_answer(
    question_id: uuid.UUID,
    body: DraftRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> QuestionModel:
    """Draft the user's answer from their CV (a few Claude calls).

    Args:
        question_id: The question.
        body: Whether to replace an answer the user wrote themselves.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The question with the draft.

    Raises:
        HTTPException: 409 when there is no CV, the user's own answer would be
            replaced, or the draft failed.
    """
    q = _approved(engine, question_id)
    try:
        draft = practice.draft_personal_answer(
            engine, user_id, question_id, adapters=adapters, replace=body.replace
        )
    except (NoCvError, practice.AnswerExistsError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if draft.status == "failed":
        raise HTTPException(status_code=409, detail=draft.error or "the draft failed")
    return _question(engine, user_id, q)


@router.post(
    "/interview/open-sentences/{open_id}/decision", response_model=QuestionModel
)
def decide_sentence(
    open_id: uuid.UUID,
    body: DecisionRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> QuestionModel:
    """Link or remove an untraced sentence of a drafted answer.

    Args:
        open_id: The open sentence's row id.
        body: The action and, for a link, the CV evidence id.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The question after the decision.

    Raises:
        HTTPException: 409 when the decision is stale or refused.
    """
    opened = progress.read_open(engine, user_id, open_id)
    try:
        practice.decide_sentence(
            engine, user_id, open_id, body.action, body.evidence_ref
        )
    except (progress.StaleDecisionError, ValueError, NoCvError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    assert opened is not None
    return _question(engine, user_id, _approved(engine, opened.question_id))


@router.get("/interview/export/anki", response_class=PlainTextResponse)
def export(
    category: str | None = None,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> str:
    """Download the Anki import file.

    Args:
        category: Restrict to one qa_category.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The tab-separated Anki file.
    """
    return export_anki(
        store.list_questions(engine, category), progress.list_progress(engine, user_id)
    )
