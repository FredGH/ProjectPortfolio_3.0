"""Reads and writes for the `intel` tables (PLAN.md Step 19a).

Every function takes the app-role engine. The tables are shared data (no
RLS), so no user id is involved. `company_intel` and `job_competitive_angle`
are append-only: a new version or angle is a new row.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Engine, text

from core.company_intel.schema import (
    AngleContent,
    IntelContent,
    IntelStatus,
    Resolution,
    StoredAngle,
    StoredIntel,
)


@dataclass(frozen=True)
class CachedPage:
    """A crawled page read back from the cache.

    Attributes:
        url: The page's URL.
        markdown: Its text as Markdown.
        links: Its internal links.
        fetched_on: The day it was crawled.
    """

    url: str
    markdown: str
    links: tuple[str, ...]
    fetched_on: date


_INTEL_COLUMNS = (
    "company_key, version, display_name, status, researched_at, content, pages, "
    "resolved_from_job, model, prompt_version, usage"
)


def _intel_from_row(row) -> StoredIntel:  # type: ignore[no-untyped-def]
    """Build a `StoredIntel` from a result row.

    Args:
        row: A row selecting `_INTEL_COLUMNS`.

    Returns:
        The stored intel.
    """
    return StoredIntel(
        company_key=row.company_key,
        version=row.version,
        display_name=row.display_name,
        status=row.status,
        researched_at=row.researched_at,
        content=IntelContent.model_validate(row.content),
        pages=list(row.pages),
        resolved_from_job=row.resolved_from_job,
        model=row.model,
        prompt_version=row.prompt_version,
        usage=dict(row.usage),
    )


def read_latest_intel(engine: Engine, key: str) -> StoredIntel | None:
    """Read a company's newest intel version.

    Args:
        engine: The app-role engine.
        key: The company key.

    Returns:
        The highest version, or None when the company has none.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {_INTEL_COLUMNS} FROM intel.company_intel "
                "WHERE company_key = :k ORDER BY version DESC LIMIT 1"
            ),
            {"k": key},
        ).one_or_none()
    return _intel_from_row(row) if row else None


def read_intel_version(engine: Engine, key: str, version: int) -> StoredIntel | None:
    """Read one specific intel version.

    Args:
        engine: The app-role engine.
        key: The company key.
        version: The version to read.

    Returns:
        That version, or None when it does not exist.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {_INTEL_COLUMNS} FROM intel.company_intel "
                "WHERE company_key = :k AND version = :v"
            ),
            {"k": key, "v": version},
        ).one_or_none()
    return _intel_from_row(row) if row else None


def list_intel_versions(engine: Engine, key: str) -> list[tuple[int, datetime, str]]:
    """List a company's versions, newest first.

    Args:
        engine: The app-role engine.
        key: The company key.

    Returns:
        `(version, researched_at, status)` tuples.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT version, researched_at, status FROM intel.company_intel "
                "WHERE company_key = :k ORDER BY version DESC"
            ),
            {"k": key},
        ).all()
    return [(r.version, r.researched_at, r.status) for r in rows]


def append_intel(
    engine: Engine,
    *,
    company_key: str,
    display_name: str,
    status: IntelStatus,
    content: IntelContent,
    pages: list[dict],
    resolved_from_job: str | None,
    model: str | None,
    prompt_version: str | None,
    usage: dict,
) -> int:
    """Append a new intel version for a company.

    Args:
        engine: The app-role engine.
        company_key: The company key.
        display_name: The name it was researched under.
        status: `ok`, `no_content` or `failed`.
        content: The structured profile.
        pages: Each crawled page: `{url, status, error}`.
        resolved_from_job: The job that triggered the research.
        model: The extraction model.
        prompt_version: The extraction prompt version.
        usage: Token use and cost.

    Returns:
        The new version number (1 for a company's first).
    """
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO intel.company_intel (company_key, version, display_name, "
                "status, content, pages, resolved_from_job, model, prompt_version, "
                "usage) SELECT :k, COALESCE(MAX(version), 0) + 1, :name, :status, "
                "CAST(:content AS jsonb), CAST(:pages AS jsonb), :job, :model, :pv, "
                "CAST(:usage AS jsonb) FROM intel.company_intel WHERE company_key = :k "
                "RETURNING version"
            ),
            {
                "k": company_key,
                "name": display_name,
                "status": status,
                "content": content.model_dump_json(),
                "pages": json.dumps(pages),
                "job": resolved_from_job,
                "model": model,
                "pv": prompt_version,
                "usage": json.dumps(usage),
            },
        ).scalar_one()


def read_cached_page(
    engine: Engine, url: str, *, max_age_days: int, today: date
) -> CachedPage | None:
    """Read the newest cached copy of a page if it is still fresh.

    Args:
        engine: The app-role engine.
        url: The page URL.
        max_age_days: How old a copy may be, in days.
        today: The current day.

    Returns:
        The cached page, or None when there is no copy within the window.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT url, markdown, links, fetched_on FROM intel.crawl_cache "
                "WHERE url = :u AND status = 'ok' AND fetched_on >= :oldest "
                "ORDER BY fetched_on DESC LIMIT 1"
            ),
            {"u": url, "oldest": today - timedelta(days=max_age_days)},
        ).one_or_none()
    if row is None:
        return None
    return CachedPage(row.url, row.markdown, tuple(row.links), row.fetched_on)


def write_cached_page(
    engine: Engine,
    url: str,
    *,
    fetched_on: date,
    markdown: str,
    links: Sequence[str],
) -> None:
    """Cache a crawled page; a second write for the same day is ignored.

    Args:
        engine: The app-role engine.
        url: The page URL.
        fetched_on: The day it was crawled.
        markdown: Its text as Markdown.
        links: Its internal links.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO intel.crawl_cache (url, fetched_on, status, markdown, "
                "links) VALUES (:u, :d, 'ok', :md, CAST(:links AS jsonb)) "
                "ON CONFLICT (url, fetched_on) DO NOTHING"
            ),
            {
                "u": url,
                "d": fetched_on,
                "md": markdown,
                "links": json.dumps(list(links)),
            },
        )


def _resolution_from_row(row) -> Resolution:  # type: ignore[no-untyped-def]
    """Build a `Resolution` from a result row.

    Args:
        row: A row of `intel.job_company`.

    Returns:
        The resolution.
    """
    return Resolution(
        job_group_id=row.job_group_id,
        posted_company=row.posted_company,
        poster_kind=row.poster_kind,
        end_client=row.end_client,
        website_url=row.website_url,
        method=row.method,
    )


def read_resolution(engine: Engine, job_group_id: str) -> Resolution | None:
    """Read how a job's company was resolved.

    Args:
        engine: The app-role engine.
        job_group_id: The job.

    Returns:
        The stored resolution, or None when the job has none.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT job_group_id, posted_company, poster_kind, end_client, "
                "website_url, method FROM intel.job_company WHERE job_group_id = :j"
            ),
            {"j": job_group_id},
        ).one_or_none()
    return _resolution_from_row(row) if row else None


def save_resolution(engine: Engine, resolution: Resolution) -> Resolution:
    """Save a job's resolution without overwriting a manual one.

    Args:
        engine: The app-role engine.
        resolution: The resolution to store.

    Returns:
        What is stored afterwards: `resolution`, or the existing `manual`
        row when `resolution` is automatic.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO intel.job_company (job_group_id, posted_company, "
                "poster_kind, end_client, website_url, method) VALUES (:j, :posted, "
                ":kind, :end, :site, :method) ON CONFLICT (job_group_id) DO UPDATE SET "
                "posted_company = EXCLUDED.posted_company, "
                "poster_kind = EXCLUDED.poster_kind, end_client = EXCLUDED.end_client, "
                "website_url = EXCLUDED.website_url, method = EXCLUDED.method, "
                "resolved_at = now() WHERE intel.job_company.method <> 'manual' "
                "OR EXCLUDED.method = 'manual'"
            ),
            {
                "j": resolution.job_group_id,
                "posted": resolution.posted_company,
                "kind": resolution.poster_kind,
                "end": resolution.end_client,
                "site": resolution.website_url,
                "method": resolution.method,
            },
        )
    stored = read_resolution(engine, resolution.job_group_id)
    assert stored is not None
    return stored


def _angle_from_row(row) -> StoredAngle:  # type: ignore[no-untyped-def]
    """Build a `StoredAngle` from a result row.

    Args:
        row: A row of `intel.job_competitive_angle`.

    Returns:
        The stored angle.
    """
    return StoredAngle(
        job_group_id=row.job_group_id,
        company_key=row.company_key,
        intel_version=row.intel_version,
        content=AngleContent.model_validate(row.content),
        model=row.model,
        prompt_version=row.prompt_version,
        usage=dict(row.usage),
        created_at=row.created_at,
    )


_ANGLE_COLUMNS = (
    "job_group_id, company_key, intel_version, content, model, prompt_version, "
    "usage, created_at"
)


def read_angle(
    engine: Engine, job_group_id: str, key: str, intel_version: int
) -> StoredAngle | None:
    """Read the newest angle written from one intel version.

    Args:
        engine: The app-role engine.
        job_group_id: The job.
        key: The company key.
        intel_version: The intel version the angle must be built from.

    Returns:
        The newest matching angle, or None.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {_ANGLE_COLUMNS} FROM intel.job_competitive_angle "
                "WHERE job_group_id = :j AND company_key = :k AND intel_version = :v "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"j": job_group_id, "k": key, "v": intel_version},
        ).one_or_none()
    return _angle_from_row(row) if row else None


def read_latest_angle(engine: Engine, job_group_id: str) -> StoredAngle | None:
    """Read a job's newest angle, whatever intel version it used.

    Args:
        engine: The app-role engine.
        job_group_id: The job.

    Returns:
        The newest angle, or None.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {_ANGLE_COLUMNS} FROM intel.job_competitive_angle "
                "WHERE job_group_id = :j ORDER BY created_at DESC LIMIT 1"
            ),
            {"j": job_group_id},
        ).one_or_none()
    return _angle_from_row(row) if row else None


def append_angle(
    engine: Engine,
    *,
    job_group_id: str,
    company_key: str,
    intel_version: int,
    content: AngleContent,
    model: str | None,
    prompt_version: str | None,
    usage: dict,
) -> StoredAngle:
    """Append a competitive angle for a job.

    Args:
        engine: The app-role engine.
        job_group_id: The job.
        company_key: The company key.
        intel_version: The intel version it was written from.
        content: The analysis.
        model: The model that wrote it.
        prompt_version: The prompt version.
        usage: Token use and cost.

    Returns:
        The stored angle.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO intel.job_competitive_angle (job_group_id, company_key, "
                "intel_version, content, model, prompt_version, usage) VALUES (:j, "
                ":k, :v, CAST(:content AS jsonb), :model, :pv, CAST(:usage AS jsonb)) "
                f"RETURNING {_ANGLE_COLUMNS}"
            ),
            {
                "j": job_group_id,
                "k": company_key,
                "v": intel_version,
                "content": content.model_dump_json(),
                "model": model,
                "pv": prompt_version,
                "usage": json.dumps(usage),
            },
        ).one()
    return _angle_from_row(row)
