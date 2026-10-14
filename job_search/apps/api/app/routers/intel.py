"""Company-intel endpoints (PLAN.md Step 19a).

Intel is shared data, keyed on the job's resolved company, so these
endpoints take no user. Research runs synchronously (a crawl and two LLM
calls); the UI waits behind a spinner.
"""

from __future__ import annotations

from datetime import datetime

from app.dependencies import get_app_db_engine, get_fetcher, get_llm_adapters
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import Engine

from core.company_intel import store
from core.company_intel.angle import get_or_create_angle
from core.company_intel.config import load_config
from core.company_intel.fetch import Fetcher
from core.company_intel.keys import company_key
from core.company_intel.resolve import (
    ManualOverride,
    normalise_website_url,
    resolve_company,
)
from core.company_intel.service import get_or_refresh_intel, is_fresh
from core.llm.types import LLMAdapter
from core.tailoring.context import load_job_context, load_job_source

router = APIRouter()


class ResolutionModel(BaseModel):
    """Which company a job is researched under."""

    poster_kind: str
    posted_company: str | None
    end_client: str | None
    website_url: str | None
    method: str


class IntelModel(BaseModel):
    """One stored intel version."""

    version: int
    status: str
    researched_at: datetime
    display_name: str
    fresh: bool
    content: dict
    pages: list[dict]


class VersionModel(BaseModel):
    """One entry of a company's version history."""

    version: int
    researched_at: datetime
    status: str


class AngleModel(BaseModel):
    """A job's competitive angle (analysis, not fact)."""

    intel_version: int
    created_at: datetime
    content: dict


class IntelView(BaseModel):
    """Everything the Company Intel page shows for a job."""

    job_group_id: str
    resolution: ResolutionModel | None
    intel: IntelModel | None
    versions: list[VersionModel]
    angle: AngleModel | None


class ResearchRequest(BaseModel):
    """Request body for starting a research."""

    force: bool = False


class ResearchView(BaseModel):
    """The result of a research call."""

    outcome: str
    message: str
    view: IntelView


class ResolutionRequest(BaseModel):
    """Request body for a manual company override."""

    end_client: str | None = None
    website_url: str | None = None

    @model_validator(mode="after")
    def _needs_something(self) -> ResolutionRequest:
        """Require at least one non-blank field and a usable website address.

        Returns:
            The validated request.

        Raises:
            ValueError: If both fields are empty or the website is unusable.
        """
        website = (self.website_url or "").strip()
        if not (self.end_client or "").strip() and not website:
            raise ValueError("give an end client, a website, or both")
        if website and normalise_website_url(website) is None:
            raise ValueError("the website is not a usable address, e.g. acme.com")
        return self


def _build_view(
    engine: Engine, job_group_id: str, version: int | None = None
) -> IntelView:
    """Assemble the page view for a job.

    Args:
        engine: The app-role engine.
        job_group_id: The job.
        version: A specific intel version, or None for the latest.

    Returns:
        The resolution, the chosen intel version, the version list and the
        job's latest angle.
    """
    config = load_config()
    resolution = store.read_resolution(engine, job_group_id)
    key = company_key(resolution.end_client) if resolution else None
    stored = None
    versions: list[VersionModel] = []
    if key is not None:
        stored = (
            store.read_intel_version(engine, key, version)
            if version is not None
            else store.read_latest_intel(engine, key)
        )
        versions = [
            VersionModel(version=v, researched_at=at, status=status)
            for v, at, status in store.list_intel_versions(engine, key)
        ]
    angle = store.read_latest_angle(engine, job_group_id)
    now = datetime.now(stored.researched_at.tzinfo) if stored else None
    return IntelView(
        job_group_id=job_group_id,
        resolution=(
            ResolutionModel(
                poster_kind=resolution.poster_kind,
                posted_company=resolution.posted_company,
                end_client=resolution.end_client,
                website_url=resolution.website_url,
                method=resolution.method,
            )
            if resolution
            else None
        ),
        intel=(
            IntelModel(
                version=stored.version,
                status=stored.status,
                researched_at=stored.researched_at,
                display_name=stored.display_name,
                fresh=is_fresh(stored.researched_at, now, config.refresh_after_days),  # type: ignore[arg-type]
                content=stored.content.model_dump(),
                pages=stored.pages,
            )
            if stored
            else None
        ),
        versions=versions,
        angle=(
            AngleModel(
                intel_version=angle.intel_version,
                created_at=angle.created_at,
                content=angle.content.model_dump(),
            )
            if angle
            else None
        ),
    )


def _require_job(engine: Engine, job_group_id: str):  # type: ignore[no-untyped-def]
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


@router.get("/intel/jobs/{job_group_id}", response_model=IntelView)
def get_intel(
    job_group_id: str,
    version: int | None = Query(None, ge=1),
    engine: Engine = Depends(get_app_db_engine),
) -> IntelView:
    """Show a job's resolution, company intel and competitive angle.

    Args:
        job_group_id: The job.
        version: A specific intel version; the latest when omitted.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The page view.
    """
    _require_job(engine, job_group_id)
    return _build_view(engine, job_group_id, version)


@router.post("/intel/jobs/{job_group_id}/research", response_model=ResearchView)
def research(
    job_group_id: str,
    body: ResearchRequest,
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
    fetcher: Fetcher = Depends(get_fetcher),
) -> ResearchView:
    """Research a job's company, or reuse fresh stored intel.

    Args:
        job_group_id: The job.
        body: `force` researches again even if the intel is fresh.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.
        fetcher: Injected via `get_fetcher`.

    Returns:
        The outcome and the refreshed page view. The competitive angle is
        written whenever there is intel to reason from.
    """
    job = _require_job(engine, job_group_id)
    source = load_job_source(engine, job_group_id)
    result = get_or_refresh_intel(
        engine,
        job,
        source,
        adapters=adapters,
        fetcher=fetcher,
        config=load_config(),
        force=body.force,
    )
    if result.intel is not None:
        get_or_create_angle(
            engine, job, result.intel, adapters=adapters, force=body.force
        )
    return ResearchView(
        outcome=result.outcome,
        message=result.message,
        view=_build_view(engine, job_group_id),
    )


@router.put("/intel/jobs/{job_group_id}/resolution", response_model=IntelView)
def put_resolution(
    job_group_id: str,
    body: ResolutionRequest,
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> IntelView:
    """Set the end client and/or website for a job (a manual override).

    Args:
        job_group_id: The job.
        body: The end client and/or website to store.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The refreshed page view.
    """
    job = _require_job(engine, job_group_id)
    resolve_company(
        engine,
        job,
        load_job_source(engine, job_group_id),
        adapters=adapters,
        override=ManualOverride(
            end_client=(body.end_client or "").strip() or None,
            website_url=(body.website_url or "").strip() or None,
        ),
    )
    return _build_view(engine, job_group_id)
