"""Cover-letter and pitch endpoints (PLAN.md Step 19).

Per user via `get_current_user_id`, like the tailoring and scoring routers.
Generation is synchronous (a few model calls); the UI waits behind a spinner.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from app.dependencies import get_app_db_engine, get_llm_adapters
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Engine

from core.artefacts import store
from core.artefacts.context import load_pack
from core.artefacts.decisions import DecisionError
from core.artefacts.schema import Kind
from core.artefacts.service import (
    NoCvError,
    NoTargetTitleError,
    decide,
    generate_artefact,
)
from core.db.session import get_current_user_id
from core.llm.types import LLMAdapter
from core.tailoring.context import load_job_context
from core.tailoring.schema import JobContext

router = APIRouter()
_TEXT_CHARS = 300


class OpenSentenceModel(BaseModel):
    """A sentence awaiting the user's decision."""

    id: uuid.UUID
    location: str
    kind: str
    text: str
    issue: str | None
    status: str
    evidence_ref: str | None


class ArtefactModel(BaseModel):
    """One stored letter or pitch."""

    id: uuid.UUID
    status: str
    target_title: str
    attempts: int
    created_at: datetime
    error_message: str | None
    content: dict | None
    open_sentences: list[OpenSentenceModel]
    usage: dict | None


class EvidenceModel(BaseModel):
    """One piece of evidence a sentence can be linked to."""

    id: str
    label: str
    text: str


class ArtefactsView(BaseModel):
    """What the Cover Letter & Pitch page shows for a job."""

    job_group_id: str
    has_saved_research: bool
    evidence: list[EvidenceModel]
    cover_letter: ArtefactModel | None
    pitch: ArtefactModel | None


class DecisionRequest(BaseModel):
    """Request body for a link / remove decision."""

    kind: Literal["cover_letter", "pitch"]
    job_group_id: str
    action: Literal["link", "remove"]
    evidence_ref: str | None = None


def _model(stored) -> ArtefactModel | None:  # type: ignore[no-untyped-def]
    """Build the API model of a stored artefact.

    Args:
        stored: A `StoredArtefact`, or None.

    Returns:
        The model, or None.
    """
    if stored is None:
        return None
    return ArtefactModel(
        id=stored.id,
        status=stored.status,
        target_title=stored.target_title,
        attempts=stored.attempts,
        created_at=stored.created_at,
        error_message=stored.error_message,
        content=stored.content,
        open_sentences=[
            OpenSentenceModel(
                id=o.id,
                location=o.location,
                kind=o.kind,
                text=o.text,
                issue=o.issue,
                status=o.status,
                evidence_ref=o.evidence_ref,
            )
            for o in stored.open_sentences
        ],
        usage=stored.usage,
    )


def _require_job(engine: Engine, job_group_id: str) -> JobContext:
    """Load the job or answer 404.

    Args:
        engine: The app-role engine.
        job_group_id: The job.

    Returns:
        The `JobContext`.

    Raises:
        HTTPException: 404 when the job does not exist.
    """
    job = load_job_context(engine, job_group_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job


def _view(engine: Engine, user_id: uuid.UUID, job: JobContext) -> ArtefactsView:
    """Assemble the page view for a job.

    Args:
        engine: The app-role engine.
        user_id: The user.
        job: The job.

    Returns:
        The latest letter and pitch, with the evidence a link can target.

    Raises:
        HTTPException: 409 when the user has no CV.
    """
    loaded = load_pack(engine, user_id, job)
    if loaded is None:
        raise HTTPException(status_code=409, detail="add your CV first")
    return ArtefactsView(
        job_group_id=job.job_group_id,
        has_saved_research=loaded.pack.has_site,
        evidence=[
            EvidenceModel(id=e.id, label=e.label, text=e.text[:_TEXT_CHARS])
            for e in loaded.pack.items.values()
        ],
        cover_letter=_model(
            store.read_latest(engine, user_id, "cover_letter", job.job_group_id)
        ),
        pitch=_model(store.read_latest(engine, user_id, "pitch", job.job_group_id)),
    )


@router.get("/artefacts/jobs/{job_group_id}", response_model=ArtefactsView)
def get_artefacts(
    job_group_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> ArtefactsView:
    """Show the user's latest cover letter and pitch for a job.

    Args:
        job_group_id: The job.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The page view.
    """
    return _view(engine, user_id, _require_job(engine, job_group_id))


def _generate(
    kind: Kind,
    job_group_id: str,
    user_id: uuid.UUID,
    engine: Engine,
    adapters: dict[str, LLMAdapter],
) -> ArtefactsView:
    """Generate one artefact kind and return the refreshed view.

    Args:
        kind: `cover_letter` or `pitch`.
        job_group_id: The job.
        user_id: The user.
        engine: The app-role engine.
        adapters: LLM adapters by provider.

    Returns:
        The refreshed page view.

    Raises:
        HTTPException: 404 for an unknown job; 409 when the CV or the title
            is missing.
    """
    job = _require_job(engine, job_group_id)
    try:
        generate_artefact(engine, user_id, kind, job, adapters=adapters)
    except (NoCvError, NoTargetTitleError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(engine, user_id, job)


@router.post(
    "/artefacts/jobs/{job_group_id}/cover-letter", response_model=ArtefactsView
)
def generate_letter(
    job_group_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> ArtefactsView:
    """Write a cover letter for a job (a few Claude calls; about $0.05-0.15).

    Args:
        job_group_id: The job.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The refreshed page view.
    """
    return _generate("cover_letter", job_group_id, user_id, engine, adapters)


@router.post("/artefacts/jobs/{job_group_id}/pitch", response_model=ArtefactsView)
def generate_pitch(
    job_group_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> ArtefactsView:
    """Write the 30/60/90 second pitch for a job (a few Claude calls).

    Args:
        job_group_id: The job.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The refreshed page view.
    """
    return _generate("pitch", job_group_id, user_id, engine, adapters)


@router.post(
    "/artefacts/open-sentences/{open_id}/decision", response_model=ArtefactsView
)
def decide_open_sentence(
    open_id: uuid.UUID,
    body: DecisionRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> ArtefactsView:
    """Link an untraced sentence to a source, or remove it.

    Args:
        open_id: The open sentence's row id.
        body: The artefact kind, job, action and, for a link, the evidence id.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The refreshed page view.

    Raises:
        HTTPException: 404 for an unknown job; 409 when the decision is stale
            or refused.
    """
    job = _require_job(engine, body.job_group_id)
    try:
        decide(
            engine,
            user_id,
            body.kind,
            open_id,
            job,
            action=body.action,
            evidence_ref=body.evidence_ref,
        )
    except (store.StaleDecisionError, DecisionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(engine, user_id, job)
