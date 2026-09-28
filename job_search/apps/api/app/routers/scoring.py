"""Scoring-preferences endpoints (PLAN.md Step 15).

Follows the same `get_current_user_id` pattern as apps/api/app/routers/cv.py
— every endpoint here 501s until Step 22a's identity middleware exists,
exactly like the CV router does today (verified live: GET /cv/truth-base
returns 501 on this stack). Not a regression introduced here.
"""

from __future__ import annotations

import uuid

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import Engine, text

from core.db.session import get_current_user_id, session_scope
from core.scoring.calibration import (
    CalibrationPreview,
    JobLabel,
    LabelCandidate,
    delete_label,
    pick_labeling_candidate,
    read_labels,
    save_calibration,
    split_and_fit,
    write_label,
)
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


class LabelCandidateModel(BaseModel):
    """Response body for GET /scoring/labeling-candidate."""

    job_group_id: str
    title: str
    company: str
    location: str
    engagement_type: str
    description: str
    vector_similarity_score: float
    reranker_score: float
    skill_coverage_score: float
    llm_fit_score: float
    llm_rationale: str | None
    llm_missing_skills: list[str] | None
    llm_stretch_flag: bool | None


class JobLabelModel(BaseModel):
    """Response body for one recorded label."""

    job_group_id: str
    label: str
    labeled_at: str


class LabelBody(BaseModel):
    """Request body for PUT /scoring/labels/{job_group_id}."""

    label: str


class CalibrationPreviewModel(BaseModel):
    """Request/response body for a fit preview."""

    fit_count: int
    holdout_count: int
    weights: dict[str, float]
    holdout_agreement: float | None
    embedding_model: str


class SaveCalibrationBody(BaseModel):
    """Request body for POST /scoring/calibration-runs."""

    preview: CalibrationPreviewModel
    calibrated_by: str | None = None


class CalibrationRunModel(BaseModel):
    """Response body for one saved calibration run."""

    fit_count: int
    holdout_count: int
    weights: dict[str, float]
    holdout_agreement: float | None
    embedding_model: str
    calibrated_by: str | None
    calibrated_at: str


@router.get("/scoring/labeling-candidate", response_model=None)
def get_labeling_candidate(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> LabelCandidateModel | Response:
    """Return one eligible, unlabeled job to hand-label, or 204 if none.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The candidate, or an empty 204 response when nothing eligible
        remains unlabeled.
    """
    candidate: LabelCandidate | None = pick_labeling_candidate(engine, user_id)
    if candidate is None:
        return Response(status_code=204)
    return LabelCandidateModel(**candidate.__dict__)


@router.get("/scoring/labels", response_model=list[JobLabelModel])
def get_labels(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[JobLabelModel]:
    """Return every label this user has recorded, newest first.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Every recorded label.
    """
    labels: list[JobLabel] = read_labels(engine, user_id)
    return [
        JobLabelModel(
            job_group_id=label.job_group_id,
            label=label.label,
            labeled_at=label.labeled_at.isoformat(),
        )
        for label in labels
    ]


@router.put("/scoring/labels/{job_group_id}", response_model=JobLabelModel)
def put_label(
    job_group_id: str,
    body: LabelBody,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> JobLabelModel:
    """Create or overwrite one job's label.

    Args:
        job_group_id: The job being labeled.
        body: The label to store.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The label as stored.
    """
    write_label(engine, user_id, job_group_id, body.label)
    [saved] = [
        label
        for label in read_labels(engine, user_id)
        if label.job_group_id == job_group_id
    ]
    return JobLabelModel(
        job_group_id=saved.job_group_id,
        label=saved.label,
        labeled_at=saved.labeled_at.isoformat(),
    )


@router.delete("/scoring/labels/{job_group_id}")
def delete_label_endpoint(
    job_group_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> Response:
    """Remove one job's label.

    Args:
        job_group_id: The job to un-label.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        An empty 204 response.
    """
    delete_label(engine, user_id, job_group_id)
    return Response(status_code=204)


@router.post("/scoring/calibrate", response_model=CalibrationPreviewModel)
def calibrate(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> CalibrationPreviewModel:
    """Preview a fit run against this user's current labels. Writes nothing.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The fit preview.

    Raises:
        fastapi.HTTPException: 400, when fewer than 30 labels exist.
    """
    try:
        preview = split_and_fit(engine, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CalibrationPreviewModel(
        fit_count=preview.fit_count,
        holdout_count=preview.holdout_count,
        weights=preview.weights,
        holdout_agreement=preview.holdout_agreement,
        embedding_model=preview.embedding_model,
    )


@router.post("/scoring/calibration-runs", response_model=CalibrationRunModel)
def save_calibration_run(
    body: SaveCalibrationBody,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> CalibrationRunModel:
    """Persist a previewed calibration.

    Args:
        body: The preview to save (as returned by POST /scoring/calibrate)
            plus optional attribution.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The saved run, read back from history.
    """
    preview = CalibrationPreview(
        fit_count=body.preview.fit_count,
        holdout_count=body.preview.holdout_count,
        weights=body.preview.weights,
        holdout_agreement=body.preview.holdout_agreement,
        embedding_model=body.preview.embedding_model,
    )
    save_calibration(engine, user_id, preview, calibrated_by=body.calibrated_by)
    [latest] = _read_calibration_history(engine, user_id)[:1]
    return latest


def _read_calibration_history(
    engine: Engine, user_id: uuid.UUID
) -> list[CalibrationRunModel]:
    """Read this user's calibration-run history, newest first.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose history to read.

    Returns:
        Every saved run, newest first.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT fit_count, holdout_count, vector_similarity_weight, "
                "reranker_weight, skill_coverage_weight, llm_fit_weight, "
                "holdout_agreement, embedding_model, calibrated_by, calibrated_at "
                "FROM scoring.calibration_run WHERE user_id = :user_id "
                "ORDER BY calibrated_at DESC"
            ),
            {"user_id": user_id},
        ).all()
    return [
        CalibrationRunModel(
            fit_count=row.fit_count,
            holdout_count=row.holdout_count,
            weights={
                "vector_similarity": float(row.vector_similarity_weight),
                "reranker": float(row.reranker_weight),
                "skill_coverage": float(row.skill_coverage_weight),
                "llm_fit": float(row.llm_fit_weight),
            },
            holdout_agreement=(
                float(row.holdout_agreement)
                if row.holdout_agreement is not None
                else None
            ),
            embedding_model=row.embedding_model,
            calibrated_by=row.calibrated_by,
            calibrated_at=row.calibrated_at.isoformat(),
        )
        for row in rows
    ]


@router.get("/scoring/calibration-runs", response_model=list[CalibrationRunModel])
def get_calibration_runs(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[CalibrationRunModel]:
    """Return this user's calibration-run history, newest first.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Every saved run, newest first.
    """
    return _read_calibration_history(engine, user_id)
