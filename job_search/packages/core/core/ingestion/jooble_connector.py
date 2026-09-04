"""JoobleConnector — keyed POST JSON with page-number pagination
(PLAN.md Step 4b).

Jooble's auth is neither a query param (Adzuna) nor HTTP Basic (Reed):
the API key is embedded directly in the URL path
(`https://jooble.org/api/{key}`), and every request is a POST with a
JSON body, not a GET with query params — genuinely the third shape this
project's connectors have needed. Page size is fixed at 30 with no
caller-configurable results-per-page control, unlike Adzuna.

Like AdzunaConnector and ReedConnector, fetch() does not catch exceptions
around the page fetch itself — a failed page propagates so the shared
runner's retry_with_backoff can retry the whole fetch. It does guard the
per-record RawJob construction with try/except KeyError, matching the
fix Step 4's final review required for Adzuna and Reed.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import cast

import httpx

from core.ingestion.raw_job import RawJob
from core.ingestion.url_utils import canonicalise_url

_logger = logging.getLogger(__name__)

_JOOBLE_SEARCH_URL = "https://jooble.org/api/{api_key}"
_DEFAULT_MAX_PAGES = 5
_PAGE_SIZE = 30


@dataclass(frozen=True)
class JoobleQuery:
    """JoobleConnector's `query` type.

    Attributes:
        keywords: Free-text search terms, e.g. "data engineer".
        location: Optional location filter, e.g. "UK", "Manchester".
            `None` means no location filter.
        max_pages: Safety cap on API calls per fetch() — pagination also
            stops early on a short (< 30-result) page.
    """

    keywords: str
    location: str | None = None
    max_pages: int = _DEFAULT_MAX_PAGES


def _hash_result(result: dict[str, object]) -> str:
    """Hash one Jooble result dict for dedup/change detection.

    Args:
        result: One entry from Jooble's `jobs` array.

    Returns:
        The SHA-256 hex digest of the result's canonical JSON form.
    """
    canonical = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _parse_jooble_updated(value: object) -> datetime.datetime | None:
    """Parse Jooble's `updated` field into an aware datetime.

    Args:
        value: The raw `updated` value from a Jooble result dict —
            typically an offset-less ISO-8601 timestamp like
            "2026-08-05T07:54:35.6100000".

    Returns:
        The parsed datetime, normalised to UTC if it was naive, or `None`
        if `value` is missing or unparseable.
    """
    if not isinstance(value, str):
        return None
    try:
        result = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=datetime.UTC)
    return result


def _fetch_jooble_page(
    http_client: httpx.Client,
    *,
    api_key: str,
    keywords: str,
    location: str | None,
    page: int,
) -> dict[str, object]:
    """Fetch one page of Jooble search results.

    Args:
        http_client: The shared HTTP client.
        api_key: Jooble API key, embedded in the URL path.
        keywords: Free-text keyword query.
        location: Optional location filter.
        page: 1-indexed page number.

    Returns:
        The parsed JSON response body (`{"jobs": [...], "totalCount": N}`).

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
    """
    body: dict[str, object] = {"keywords": keywords, "page": str(page)}
    if location:
        body["location"] = location

    response = http_client.post(
        _JOOBLE_SEARCH_URL.format(api_key=api_key), json=body, timeout=15.0
    )
    response.raise_for_status()
    return response.json()


class JoobleConnector:
    """Fetches job postings from Jooble's keyed, paginated search API."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        api_key: str,
        fetch_page_fn: Callable[..., dict[str, object]] = _fetch_jooble_page,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for every page fetch.
            api_key: Jooble API key.
            fetch_page_fn: Injectable per-page fetch — defaults to the
                real Jooble API call.
        """
        self._http_client = http_client
        self._api_key = api_key
        self._fetch_page_fn = fetch_page_fn

    def fetch(
        self, query: JoobleQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield job postings matching `query`, paginating as needed.

        Args:
            query: `JoobleQuery` — keywords, optional location, pagination
                cap.
            since: Only yield postings whose `updated` is at or after
                this time. `None` means no filter.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            One `RawJob` per result, across every fetched page.
        """
        for page in range(1, query.max_pages + 1):
            body = self._fetch_page_fn(
                self._http_client,
                api_key=self._api_key,
                keywords=query.keywords,
                location=query.location,
                page=page,
            )
            results = cast(list[dict[str, object]], body.get("jobs", []))
            if not results:
                return

            fetched_at = datetime.datetime.now(datetime.UTC)
            for result in results:
                updated = _parse_jooble_updated(result.get("updated"))
                if since is not None and updated is not None and updated < since:
                    continue

                try:
                    job_url = str(result["link"])
                    canonical_url = canonicalise_url(job_url)
                    source_job_id = str(result["id"])
                except KeyError:
                    _logger.warning(
                        "Jooble result missing a required field (link/id) for "
                        "keywords=%s page=%s; skipping this record",
                        query.keywords,
                        page,
                        exc_info=True,
                    )
                    continue

                yield RawJob(
                    source_name="jooble",
                    source_job_id=source_job_id,
                    job_url=job_url,
                    job_url_canonical=canonical_url,
                    payload=dict(result),
                    fetched_at=fetched_at,
                    run_id=run_id,
                    request_params={
                        "keywords": query.keywords,
                        "location": query.location,
                        "page": page,
                    },
                    payload_sha256=_hash_result(result),
                )

            if len(results) < _PAGE_SIZE:
                return
