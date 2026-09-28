"""Scoring-preferences endpoints (PLAN.md Step 15).

Follows the same `get_current_user_id` pattern as apps/api/app/routers/cv.py
— every endpoint here 501s until Step 22a's identity middleware exists,
exactly like the CV router does today (verified live: GET /cv/truth-base
returns 501 on this stack). Not a regression introduced here.
"""

from __future__ import annotations

import uuid

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from core.db.session import get_current_user_id
from core.scoring.preferences import UserPreference, read_preference, write_preference

router = APIRouter()


class UserPreferenceModel(BaseModel):
    """Request/response body for scoring preferences."""

    preferred_locations: list[str] = Field(default_factory=list)
    remote_ok: str = "no_preference"
    contract_types: list[str] = Field(default_factory=list)
    excluded_ir35_statuses: list[str] = Field(default_factory=list)
    min_seniority_band: str | None = None
    max_seniority_band: str | None = None
    min_salary_annual: float | None = None
    min_rate_daily: float | None = None
    max_posting_age_days: int | None = None


@router.get("/scoring/preferences", response_model=UserPreferenceModel)
def get_preferences(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> UserPreferenceModel:
    """Read the caller's scoring preferences.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The stored preferences, or all defaults if never saved.
    """
    pref = read_preference(engine, user_id)
    return UserPreferenceModel(**pref.__dict__)


@router.put("/scoring/preferences", response_model=UserPreferenceModel)
def put_preferences(
    body: UserPreferenceModel,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> UserPreferenceModel:
    """Replace the caller's scoring preferences.

    Args:
        body: The full preference set to store.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The preferences as stored.
    """
    pref = UserPreference(**body.model_dump())
    write_preference(engine, user_id, pref)
    return body
