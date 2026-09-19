"""Skill review endpoints (PLAN.md Step 14): the unmapped-skills list, the
embedding-match verify list, ESCO search, and the resolve / dismiss / reject
actions. All data is SHARED-zone taxonomy — a resolution applies to every
user (docs/tenancy.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import Connection, Engine

from core.skills import review

router = APIRouter()


class ReviewItemModel(BaseModel):
    """An unmapped skill string awaiting review (`core.skills.review.ReviewItem`)."""

    raw_norm: str
    raw_example: str
    seen_in_cv: bool
    jd_job_count: int
    sample_job_group_ids: list[str]
    candidate_skill_id: str | None
    candidate_label: str | None
    candidate_score: float | None


class MatchItemModel(BaseModel):
    """An auto-mapped embedding match to verify (see `core.skills.review.MatchItem`)."""

    raw_norm: str
    raw_example: str
    skill_id: str
    skill_label: str | None
    score: float
    jd_job_count: int


class SkillOptionModel(BaseModel):
    """One skill search result (see `core.skills.review.SkillOption`)."""

    skill_id: str
    label: str
    source: str


class ResolveRequest(BaseModel):
    """Map a string to an existing skill xor a new custom skill.

    Attributes:
        raw_norm: The normalised string being resolved.
        skill_id: An existing ESCO / custom skill id.
        custom_label: A label for a new custom skill.
    """

    raw_norm: str
    skill_id: str | None = None
    custom_label: str | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> ResolveRequest:
        """Require exactly one of `skill_id` / `custom_label`.

        Returns:
            This instance, if valid.

        Raises:
            ValueError: If neither or both are given.
        """
        if (self.skill_id is None) == (self.custom_label is None):
            raise ValueError("provide exactly one of skill_id or custom_label")
        return self


class RawNormRequest(BaseModel):
    """A request naming one normalised skill string.

    Attributes:
        raw_norm: The normalised string.
    """

    raw_norm: str


def _act(engine: Engine, action: Callable[[Connection], None]) -> dict[str, str]:
    """Run a review action in one transaction, mapping errors to HTTP codes.

    Args:
        engine: The app-role engine.
        action: The review call to run on the transaction's connection.

    Returns:
        ``{"status": "ok"}``.

    Raises:
        fastapi.HTTPException: 404 for an unknown string, 422 for an
            invalid state or skill.
    """
    try:
        with engine.begin() as conn:
            action(conn)
    except review.ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except review.ReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok"}


@router.get("/skills/review", response_model=list[ReviewItemModel])
def get_review_list(
    limit: int = Query(default=50, ge=1, le=500),
    engine: Engine = Depends(get_app_db_engine),
) -> list[ReviewItemModel]:
    """List unmapped skill strings needing a decision.

    Args:
        limit: Maximum items.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Open and rejected items, most-requested first.
    """
    with engine.connect() as conn:
        items = review.list_unmapped(conn, limit=limit)
    return [ReviewItemModel(**asdict(item)) for item in items]


@router.get("/skills/review/embedding-matches", response_model=list[MatchItemModel])
def get_embedding_matches(
    limit: int = Query(default=50, ge=1, le=500),
    engine: Engine = Depends(get_app_db_engine),
) -> list[MatchItemModel]:
    """List embedding auto-matches for verification, least confident first.

    Args:
        limit: Maximum items.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Auto-mapped items with their scores.
    """
    with engine.connect() as conn:
        items = review.list_embedding_matches(conn, limit=limit)
    return [MatchItemModel(**asdict(item)) for item in items]


@router.get("/skills/search", response_model=list[SkillOptionModel])
def search_skills(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    engine: Engine = Depends(get_app_db_engine),
) -> list[SkillOptionModel]:
    """Search ESCO and custom skills by label.

    Args:
        q: The search text.
        limit: Maximum results.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Matching skills.
    """
    with engine.connect() as conn:
        options = review.search_skills(conn, q, limit=limit)
    return [SkillOptionModel(**asdict(option)) for option in options]


@router.post("/skills/review/resolve")
def post_resolve(
    request: ResolveRequest, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, str]:
    """Map a string to a skill, or to a new custom skill.

    Args:
        request: The string and its target.
        engine: Injected via `get_app_db_engine`.

    Returns:
        ``{"status": "ok"}``.
    """
    if request.skill_id is not None:
        skill_id = request.skill_id
        return _act(
            engine,
            lambda conn: review.resolve_to_skill(conn, request.raw_norm, skill_id),
        )
    label = request.custom_label or ""
    return _act(
        engine, lambda conn: review.resolve_to_custom(conn, request.raw_norm, label)
    )


@router.post("/skills/review/dismiss")
def post_dismiss(
    request: RawNormRequest, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, str]:
    """Dismiss an unmapped string so it is never re-queued.

    Args:
        request: The string.
        engine: Injected via `get_app_db_engine`.

    Returns:
        ``{"status": "ok"}``.
    """
    return _act(engine, lambda conn: review.dismiss(conn, request.raw_norm))


@router.post("/skills/review/reject")
def post_reject(
    request: RawNormRequest, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, str]:
    """Reject a wrong embedding auto-match.

    Args:
        request: The string.
        engine: Injected via `get_app_db_engine`.

    Returns:
        ``{"status": "ok"}``.
    """
    return _act(
        engine, lambda conn: review.reject_embedding_match(conn, request.raw_norm)
    )
