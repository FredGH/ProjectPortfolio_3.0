"""Skill review endpoints (PLAN.md Step 14): the unmapped-skills list, the
embedding-match verify list, the decisions list, ESCO search, and the resolve /
dismiss / reject / reopen actions. All data is SHARED-zone taxonomy — a
resolution applies to every user (docs/tenancy.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, Engine

from core.skills import review

router = APIRouter()

MAX_INPUT_CHARS = 200
"""Longest accepted request string.

These endpoints are an unauthenticated shared-zone write surface (auth
lands in Step 22a): every string is bounded so an oversized slug or
`raw_norm` cannot reach Postgres and surface as an unhandled 500. Well
above the longest real skill label."""


def _reject_nul(*values: str | None) -> None:
    """Refuse request strings containing a NUL character.

    Postgres text values cannot hold a NUL byte, so one would come back as
    a driver error (an unhandled 500) rather than a validation failure.

    Args:
        values: The strings to check; None values are ignored.

    Raises:
        ValueError: If any value contains a NUL character.
    """
    for value in values:
        if value is not None and "\x00" in value:
            raise ValueError("must not contain a NUL character")


def _reject_nul_query(q: str | None) -> None:
    """Refuse a NUL character in a search query parameter.

    Args:
        q: The search text, or None.

    Raises:
        fastapi.HTTPException: 422 if `q` contains a NUL character.
    """
    if q is not None and "\x00" in q:
        raise HTTPException(
            status_code=422, detail="q must not contain a NUL character"
        )


class ReviewItemModel(BaseModel):
    """An unmapped skill string awaiting review (`core.skills.review.ReviewItem`)."""

    raw_norm: str
    raw_example: str
    review_status: str
    seen_in_cv: bool
    jd_job_count: int
    sample_job_group_ids: list[str]
    candidate_skill_id: str | None
    candidate_label: str | None
    candidate_score: float | None
    llm_verdict: str | None = None
    llm_custom_label: str | None = None
    llm_note: str | None = None


class MatchItemModel(BaseModel):
    """An auto-made match to verify (see `core.skills.review.MatchItem`)."""

    raw_norm: str
    raw_example: str
    skill_id: str
    skill_label: str | None
    method: str
    score: float | None
    suspicious: bool
    seen_in_cv: bool
    jd_job_count: int
    llm_note: str | None = None


class DecisionItemModel(BaseModel):
    """A resolved or dismissed string (see `core.skills.review.DecisionItem`)."""

    raw_norm: str
    raw_example: str
    review_status: str
    skill_id: str | None
    skill_label: str | None
    seen_in_cv: bool
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

    raw_norm: str = Field(max_length=MAX_INPUT_CHARS)
    skill_id: str | None = Field(default=None, max_length=MAX_INPUT_CHARS)
    custom_label: str | None = Field(default=None, max_length=MAX_INPUT_CHARS)

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

    @model_validator(mode="after")
    def _no_nul_characters(self) -> ResolveRequest:
        """Reject a NUL character in any of this request's strings.

        Returns:
            This instance, if valid.

        Raises:
            ValueError: If any string contains a NUL character.
        """
        _reject_nul(self.raw_norm, self.skill_id, self.custom_label)
        return self


class RawNormRequest(BaseModel):
    """A request naming one normalised skill string.

    Attributes:
        raw_norm: The normalised string.
    """

    raw_norm: str = Field(max_length=MAX_INPUT_CHARS)

    @model_validator(mode="after")
    def _no_nul_characters(self) -> RawNormRequest:
        """Reject a NUL character in `raw_norm`.

        Returns:
            This instance, if valid.

        Raises:
            ValueError: If `raw_norm` contains a NUL character.
        """
        _reject_nul(self.raw_norm)
        return self


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
    q: str | None = Query(default=None, max_length=MAX_INPUT_CHARS),
    limit: int = Query(default=50, ge=1, le=500),
    engine: Engine = Depends(get_app_db_engine),
) -> list[ReviewItemModel]:
    """List unmapped skill strings needing a decision.

    Args:
        q: Optional search text, matched against the string and its
            suggested skill. Lets a reviewer find one string in a backlog
            far longer than `limit`.
        limit: Maximum items.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Open and rejected items, most-requested first.

    Raises:
        fastapi.HTTPException: 422 if `q` contains a NUL character (a query
            parameter has no request model to validate it).
    """
    _reject_nul_query(q)
    with engine.connect() as conn:
        items = review.list_unmapped(conn, query=q, limit=limit)
    return [ReviewItemModel(**asdict(item)) for item in items]


@router.get("/skills/review/auto-matches", response_model=list[MatchItemModel])
def get_auto_matches(
    q: str | None = Query(default=None, max_length=MAX_INPUT_CHARS),
    limit: int = Query(default=50, ge=1, le=500),
    engine: Engine = Depends(get_app_db_engine),
) -> list[MatchItemModel]:
    """List the mapper's own matches (label and embedding) for verification.

    Args:
        q: Optional search text, matched against the string and its
            matched skill.
        limit: Maximum items.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Suspicious label matches first, then embedding matches least
        confident first, then the remaining label matches.

    Raises:
        fastapi.HTTPException: 422 if `q` contains a NUL character.
    """
    _reject_nul_query(q)
    with engine.connect() as conn:
        items = review.list_auto_matches(conn, query=q, limit=limit)
    return [MatchItemModel(**asdict(item)) for item in items]


@router.get("/skills/review/decisions", response_model=list[DecisionItemModel])
def get_decisions(
    q: str | None = Query(default=None, max_length=MAX_INPUT_CHARS),
    limit: int = Query(default=50, ge=1, le=500),
    engine: Engine = Depends(get_app_db_engine),
) -> list[DecisionItemModel]:
    """List resolved and dismissed strings, so one can be reopened.

    Args:
        q: Optional search text, matched against the string and its target.
        limit: Maximum items.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Resolved and dismissed items, most-used first.

    Raises:
        fastapi.HTTPException: 422 if `q` contains a NUL character (a query
            parameter has no request model to validate it).
    """
    if q is not None and "\x00" in q:
        raise HTTPException(
            status_code=422, detail="q must not contain a NUL character"
        )
    with engine.connect() as conn:
        items = review.list_decisions(conn, query=q, limit=limit)
    return [DecisionItemModel(**asdict(item)) for item in items]


@router.get("/skills/search", response_model=list[SkillOptionModel])
def search_skills(
    q: str = Query(min_length=1, max_length=MAX_INPUT_CHARS),
    limit: int = Query(default=20, ge=1, le=100),
    engine: Engine = Depends(get_app_db_engine),
) -> list[SkillOptionModel]:
    """Search ESCO and custom skills by label.

    Args:
        q: The search text.
        limit: Maximum results.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Matching skills, best match first.

    Raises:
        fastapi.HTTPException: 422 if `q` contains a NUL character (a query
            parameter has no request model to validate it).
    """
    if "\x00" in q:
        raise HTTPException(
            status_code=422, detail="q must not contain a NUL character"
        )
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
    """Reject a wrong auto-match (embedding or label).

    Args:
        request: The string.
        engine: Injected via `get_app_db_engine`.

    Returns:
        ``{"status": "ok"}``.
    """
    return _act(engine, lambda conn: review.reject_auto_match(conn, request.raw_norm))


@router.post("/skills/review/reopen")
def post_reopen(
    request: RawNormRequest, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, str]:
    """Withdraw a resolved or dismissed decision.

    Args:
        request: The string.
        engine: Injected via `get_app_db_engine`.

    Returns:
        ``{"status": "ok"}``.
    """
    return _act(engine, lambda conn: review.reopen(conn, request.raw_norm))
