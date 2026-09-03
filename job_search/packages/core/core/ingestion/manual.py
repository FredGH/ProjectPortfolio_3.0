"""Orchestrates the manual job-entry pipeline (PLAN.md Step 2), routed
through the shared runner and ManualConnector (PLAN.md Step 3) — one code
path into landing and bronze, same as every future connector.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from core.ingestion.extraction import ExtractedJobFields, extract_job_fields
from core.ingestion.manual_connector import ManualConnector, ManualJobQuery
from core.ingestion.runner import RunResult, run_connector
from core.llm.types import LLMAdapter


@dataclass(frozen=True)
class ManualIngestResult:
    """The outcome of one manual job-entry ingestion.

    Attributes:
        source_name: The source this record came from.
        job_url: The original (uncanonicalised) job URL.
        job_url_canonical: The canonicalised job URL.
        source_job_id: The extracted or hashed job identifier.
        run_id: The ULID identifying this ingestion run.
        landing_path: The path written to in the landing zone.
        payload_sha256: SHA-256 hex digest of the dedup-relevant payload.
        extracted: The (possibly all-None, on extraction failure) parsed
            fields, merged with any user overrides.
        field_source: Maps each user-overridden field name to `"user"`.
    """

    source_name: str
    job_url: str
    job_url_canonical: str
    source_job_id: str
    run_id: str
    landing_path: str
    payload_sha256: str
    extracted: ExtractedJobFields
    field_source: dict[str, str]


def ingest_manual_job(
    *,
    source_name: str,
    job_url: str,
    job_spec: str,
    posted_date: datetime.date | None = None,
    company: str | None = None,
    title: str | None = None,
    location: str | None = None,
    notes: str | None = None,
    landing_uri: str,
    database_url: str,
    http_client: httpx.Client,
    llm_adapters: dict[str, LLMAdapter],
    load_to_bronze_fn: Callable[..., None] | None = None,
    extract_fn: Callable[..., ExtractedJobFields] = extract_job_fields,
    run_connector_fn: Callable[..., RunResult] = run_connector,
) -> ManualIngestResult:
    """Run the manual job-entry pipeline end to end.

    Args:
        source_name: Where this posting came from, e.g. "linkedin_manual".
        job_url: The raw job URL, as pasted.
        job_spec: The full posting text, stored verbatim.
        posted_date: When the job was posted, if known.
        company: User-supplied company override.
        title: User-supplied title override.
        location: User-supplied location override.
        notes: Free-text notes travelling with the job.
        landing_uri: Root URI of the landing zone.
        database_url: The migration/owner Postgres DSN for the bronze load.
        http_client: Used for the canonical URL's redirect resolution.
        llm_adapters: Every available LLM adapter, keyed by provider.
        load_to_bronze_fn: Injectable bronze loader, threaded through to
            `run_connector_fn`. `None` (the default) lets `run_connector`
            use its own real default rather than this function importing
            `load_to_bronze` itself just to pass it along.
        extract_fn: Injectable extraction function, threaded through to
            `ManualConnector`.
        run_connector_fn: Injectable runner entrypoint — defaults to the
            real `run_connector`.

    Returns:
        The `ManualIngestResult` describing what was ingested.
    """
    query = ManualJobQuery(
        source_name=source_name,
        job_url=job_url,
        job_spec=job_spec,
        posted_date=posted_date,
        company=company,
        title=title,
        location=location,
        notes=notes,
    )
    connector = ManualConnector(
        http_client=http_client, llm_adapters=llm_adapters, extract_fn=extract_fn
    )

    kwargs: dict[str, object] = {
        "connector_key": "manual",
        "connector": connector,
        "query": query,
        "since": None,
        "entry_method": "manual",
        "landing_uri": landing_uri,
        "database_url": database_url,
    }
    if load_to_bronze_fn is not None:
        kwargs["load_to_bronze_fn"] = load_to_bronze_fn

    result = run_connector_fn(**kwargs)
    raw_job = result.raw_jobs[0]
    landing_path = result.landing_paths[0]

    extracted = ExtractedJobFields(**raw_job.payload["parsed"])
    field_source = raw_job.payload["field_source"]

    return ManualIngestResult(
        source_name=raw_job.source_name,
        job_url=raw_job.job_url,
        job_url_canonical=raw_job.job_url_canonical,
        source_job_id=raw_job.source_job_id,
        run_id=raw_job.run_id,
        landing_path=landing_path,
        payload_sha256=raw_job.payload_sha256,
        extracted=extracted,
        field_source=field_source,
    )
