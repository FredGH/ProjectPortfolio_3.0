"""POST /ingest/manual — the manual job-entry endpoint (PLAN.md Step 2)."""

from __future__ import annotations

import datetime

import httpx
from app.dependencies import get_app_db_engine, get_http_client, get_llm_adapters
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Engine, text

from core.ingestion.manual import ingest_manual_job
from core.llm.types import LLMAdapter
from core.settings import get_settings

router = APIRouter()


class ManualIngestRequest(BaseModel):
    """The paste-form's submission payload.

    Attributes:
        source_name: Where this posting came from, e.g. "linkedin_manual".
        job_url: The raw job URL, as pasted.
        job_spec: The full posting text.
        posted_date: When the job was posted, if known.
        company: User-supplied company override.
        title: User-supplied title override.
        location: User-supplied location override.
        notes: Free-text notes travelling with the job.
    """

    source_name: str
    job_url: str
    job_spec: str
    posted_date: datetime.date | None = None
    company: str | None = None
    title: str | None = None
    location: str | None = None
    notes: str | None = None


class ManualIngestResponse(BaseModel):
    """What the caller learns about a completed ingestion.

    Attributes:
        source_job_id: The extracted or hashed job identifier.
        job_url_canonical: The canonicalised job URL.
        payload_sha256: SHA-256 hex digest of the dedup-relevant payload.
        landing_path: The path written to in the landing zone.
        extracted: The parsed (and user-overridden) fields.
        field_source: Maps each user-overridden field name to `"user"`.
    """

    source_job_id: str
    job_url_canonical: str
    payload_sha256: str
    landing_path: str
    extracted: dict[str, str | None]
    field_source: dict[str, str]


@router.post("/ingest/manual", response_model=ManualIngestResponse)
def ingest_manual(
    request: ManualIngestRequest,
    http_client: httpx.Client = Depends(get_http_client),
    llm_adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> ManualIngestResponse:
    """Ingest one manually-pasted job posting.

    Args:
        request: The paste-form's submission payload.
        http_client: Injected via `get_http_client`.
        llm_adapters: Injected via `get_llm_adapters`.

    Returns:
        The `ManualIngestResponse` describing what was ingested.
    """
    settings = get_settings()
    result = ingest_manual_job(
        source_name=request.source_name,
        job_url=request.job_url,
        job_spec=request.job_spec,
        posted_date=request.posted_date,
        company=request.company,
        title=request.title,
        location=request.location,
        notes=request.notes,
        landing_uri=settings.landing_uri,
        database_url=settings.database_url,
        http_client=http_client,
        llm_adapters=llm_adapters,
    )
    return ManualIngestResponse(
        source_job_id=result.source_job_id,
        job_url_canonical=result.job_url_canonical,
        payload_sha256=result.payload_sha256,
        landing_path=result.landing_path,
        extracted=result.extracted.model_dump(),
        field_source=result.field_source,
    )


@router.get("/sources", response_model=list[str])
def list_sources(
    engine: Engine = Depends(get_app_db_engine),
) -> list[str]:
    """List distinct source names already used, for the paste form's dropdown.

    Args:
        engine: Injected via `get_app_db_engine` — the RLS-enforced app
            role, which has a `SELECT`-only grant on `bronze.raw_jobs`
            (Task 3's migration).

    Returns:
        Distinct `source_name` values from `bronze.raw_jobs`, sorted
        alphabetically. Empty on a fresh database — the form falls back to
        free text only in that case.
    """
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT DISTINCT source_name FROM bronze.raw_jobs "
                "ORDER BY source_name"
            )
        )
        return [row.source_name for row in result]
