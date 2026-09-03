"""ManualConnector — Step 2's manual-entry logic, retrofitted onto the
Connector protocol (PLAN.md Step 3), so it goes through the exact same
shared runner as every future API connector.

payload_sha256 is still computed from the pre-extraction "source payload"
only (raw_text, posted_date, notes, overrides) — never from extraction's
output — preserving Step 2's dedup-identity-independent-of-extraction
invariant. Unlike Step 2's original code, the enriched (parsed +
field_source) result is embedded directly into the yielded RawJob's
payload rather than requiring a second bronze write — landing gaining a
snapshot of a since-superseded extraction attempt doesn't violate
"raw_text is never overwritten": raw_text itself is always present and
untouched, so a later re-extraction pass can always read it back out.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from core.ingestion.extraction import (
    ExtractedJobFields,
    apply_user_overrides,
    extract_job_fields,
)
from core.ingestion.raw_job import RawJob
from core.ingestion.url_utils import canonicalise_url, extract_source_job_id
from core.llm.types import LLMAdapter

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManualJobQuery:
    """One manually-pasted job submission — ManualConnector's `query` type.

    Attributes:
        source_name: Where this posting came from, e.g. "linkedin_manual".
        job_url: The raw job URL, as pasted.
        job_spec: The full posting text, stored verbatim.
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


def _hash_payload(source_payload: dict[str, object]) -> str:
    """Hash the dedup-relevant payload, independent of extraction results.

    Args:
        source_payload: The pre-extraction record content.

    Returns:
        The SHA-256 hex digest of the payload's canonical JSON form.
    """
    canonical = json.dumps(source_payload, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ManualConnector:
    """Fetches exactly one RawJob from a manually-pasted job submission."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        llm_adapters: dict[str, LLMAdapter],
        extract_fn: Callable[..., ExtractedJobFields] = extract_job_fields,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for the canonical URL's redirect resolution.
            llm_adapters: Every available LLM adapter, keyed by provider.
            extract_fn: Injectable extraction function — defaults to the
                real `extract_job_fields`.
        """
        self._http_client = http_client
        self._llm_adapters = llm_adapters
        self._extract_fn = extract_fn

    def fetch(
        self, query: ManualJobQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield the one RawJob this manual submission produces.

        Args:
            query: The `ManualJobQuery` describing what was pasted.
            since: Unused — manual entry has no incremental-fetch concept.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            Exactly one `RawJob`.
        """
        canonical_url = canonicalise_url(query.job_url, http_client=self._http_client)
        source_job_id = extract_source_job_id(canonical_url)
        fetched_at = datetime.datetime.now(datetime.UTC)

        source_payload: dict[str, object] = {
            "raw_text": query.job_spec,
            "posted_date": (
                query.posted_date.isoformat() if query.posted_date else None
            ),
            "notes": query.notes,
            "overrides": {
                "company": query.company,
                "title": query.title,
                "location": query.location,
            },
        }
        payload_sha256 = _hash_payload(source_payload)

        try:
            extracted = self._extract_fn(query.job_spec, adapters=self._llm_adapters)
        except Exception:  # noqa: BLE001 — extraction is best-effort by design
            _logger.warning(
                "LLM extraction failed for source_name=%s job_url=%s; "
                "proceeding with an unextracted record (re-runnable from landing)",
                query.source_name,
                query.job_url,
                exc_info=True,
            )
            extracted = ExtractedJobFields()

        merged, field_source = apply_user_overrides(
            extracted,
            {
                "company": query.company,
                "title": query.title,
                "location": query.location,
            },
        )

        payload = {
            **source_payload,
            "parsed": merged.model_dump(),
            "field_source": field_source,
        }

        yield RawJob(
            source_name=query.source_name,
            source_job_id=source_job_id,
            job_url=query.job_url,
            job_url_canonical=canonical_url,
            payload=payload,
            fetched_at=fetched_at,
            run_id=run_id,
            request_params={},
            payload_sha256=payload_sha256,
        )
