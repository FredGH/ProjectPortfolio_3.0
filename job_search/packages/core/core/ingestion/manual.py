"""Orchestrates the manual job-entry pipeline end to end (PLAN.md Step 2).

canonicalise_url -> extract_source_job_id -> write to landing ->
best-effort LLM extraction -> load to bronze. Every collaborator is
injected with a real default, matching the pattern used throughout
core.llm and core.db — tests never need live network, Ollama, or Postgres.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from core.ingestion.bronze import load_to_bronze
from core.ingestion.extraction import (
    ExtractedJobFields,
    apply_user_overrides,
    extract_job_fields,
)
from core.ingestion.landing import write_landing_record
from core.ingestion.run_id import generate_run_id
from core.ingestion.url_utils import canonicalise_url, extract_source_job_id
from core.llm.types import LLMAdapter

_logger = logging.getLogger(__name__)


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


def _hash_payload(source_payload: dict[str, object]) -> str:
    """Hash the dedup-relevant payload, independent of extraction results.

    Args:
        source_payload: The pre-extraction record content (raw text, user
            overrides, posted date, notes) — never the parsed fields,
            which can change between reruns without the source changing.

    Returns:
        The SHA-256 hex digest of the payload's canonical JSON form.
    """
    canonical = json.dumps(source_payload, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


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
    load_to_bronze_fn: Callable[..., None] = load_to_bronze,
    extract_fn: Callable[..., ExtractedJobFields] = extract_job_fields,
) -> ManualIngestResult:
    """Run the manual job-entry pipeline end to end.

    Args:
        source_name: Where this posting came from, e.g. "linkedin_manual".
        job_url: The raw job URL, as pasted.
        job_spec: The full posting text, stored verbatim and never
            overwritten — the parse is a derived field.
        posted_date: When the job was posted, if known.
        company: User-supplied company override.
        title: User-supplied title override.
        location: User-supplied location override.
        notes: Free-text notes travelling with the job.
        landing_uri: Root URI of the landing zone.
        database_url: The migration/owner Postgres DSN for the bronze load.
        http_client: Used for the canonical URL's redirect resolution.
        llm_adapters: Every available LLM adapter, keyed by provider.
        load_to_bronze_fn: Injectable bronze loader — defaults to the real
            dlt-backed `load_to_bronze`.
        extract_fn: Injectable extraction function — defaults to the real
            `extract_job_fields`.

    Returns:
        The `ManualIngestResult` describing what was ingested.
    """
    canonical_url = canonicalise_url(job_url, http_client=http_client)
    source_job_id = extract_source_job_id(canonical_url)
    run_id = generate_run_id()
    fetched_at = datetime.datetime.now(datetime.UTC)

    source_payload: dict[str, object] = {
        "raw_text": job_spec,
        "posted_date": posted_date.isoformat() if posted_date else None,
        "notes": notes,
        "overrides": {"company": company, "title": title, "location": location},
    }
    payload_sha256 = _hash_payload(source_payload)

    landing_record = {
        "_source_name": source_name,
        "_source_job_id": source_job_id,
        "_job_url": job_url,
        "_fetched_at": fetched_at.isoformat(),
        "_run_id": run_id,
        "_request_params": {},
        "_payload_sha256": payload_sha256,
        **source_payload,
    }
    landing_path = write_landing_record(
        landing_uri,
        source_name=source_name,
        run_id=run_id,
        record=landing_record,
        fetched_at=fetched_at,
    )

    try:
        extracted = extract_fn(job_spec, adapters=llm_adapters)
    except Exception:  # noqa: BLE001 — extraction is best-effort by design
        _logger.warning(
            "LLM extraction failed for source_name=%s job_url=%s; "
            "proceeding with an unextracted record (re-runnable from landing)",
            source_name,
            job_url,
            exc_info=True,
        )
        extracted = ExtractedJobFields()

    merged, field_source = apply_user_overrides(
        extracted, {"company": company, "title": title, "location": location}
    )

    bronze_payload = {
        **source_payload,
        "parsed": merged.model_dump(),
        "field_source": field_source,
    }
    load_to_bronze_fn(
        database_url=database_url,
        source_name=source_name,
        source_job_id=source_job_id,
        job_url=job_url,
        job_url_canonical=canonical_url,
        entry_method="manual",
        fetched_at=fetched_at,
        run_id=run_id,
        request_params={},
        payload=bronze_payload,
        payload_sha256=payload_sha256,
    )

    return ManualIngestResult(
        source_name=source_name,
        job_url=job_url,
        job_url_canonical=canonical_url,
        source_job_id=source_job_id,
        run_id=run_id,
        landing_path=landing_path,
        payload_sha256=payload_sha256,
        extracted=merged,
        field_source=field_source,
    )
