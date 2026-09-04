"""ReedConnector — keyed REST with offset-based pagination (PLAN.md
Step 4).

Reed's search endpoint takes `resultsToSkip`/`resultsToTake` rather than
Adzuna's page-number-in-the-URL scheme — genuinely different pagination
shape, which is exactly why PLAN.md picked these two together. Reed's
`jobDescription` field is truncated by Reed itself (confirmed live during
planning, ~500 chars with a literal "..." — not this connector's doing);
stored as-is, since the full text needs a separate per-job endpoint this
connector does not call.
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

_REED_SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
_DEFAULT_MAX_PAGES = 5
_DEFAULT_RESULTS_PER_PAGE = 50


@dataclass(frozen=True)
class ReedQuery:
    """ReedConnector's `query` type.

    Attributes:
        keywords: Free-text search terms, e.g. "data engineer".
        location: Optional UK location name, e.g. "Manchester". `None`
            means no location filter (nationwide UK).
        max_pages: Safety cap on API calls per fetch() — pagination also
            stops early on a short page.
        results_per_page: Results requested per page.
    """

    keywords: str
    location: str | None = None
    max_pages: int = _DEFAULT_MAX_PAGES
    results_per_page: int = _DEFAULT_RESULTS_PER_PAGE


def _hash_result(result: dict[str, object]) -> str:
    """Hash one Reed result dict for dedup/change detection.

    Args:
        result: One entry from Reed's `results` array.

    Returns:
        The SHA-256 hex digest of the result's canonical JSON form.
    """
    canonical = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fetch_reed_page(
    http_client: httpx.Client,
    *,
    api_key: str,
    keywords: str,
    location: str | None,
    results_to_skip: int,
    results_to_take: int,
) -> dict[str, object]:
    """Fetch one page of Reed search results.

    Args:
        http_client: The shared HTTP client.
        api_key: Reed API key, sent as the HTTP Basic auth username with
            a blank password — confirmed live during planning.
        keywords: Free-text keyword query.
        location: Optional UK location filter.
        results_to_skip: Offset into the result set.
        results_to_take: Page size.

    Returns:
        The parsed JSON response body (`{"results": [...], "totalResults":
        N}`).

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
    """
    params: dict[str, str | int] = {
        "keywords": keywords,
        "resultsToSkip": results_to_skip,
        "resultsToTake": results_to_take,
    }
    if location:
        params["locationName"] = location

    response = http_client.get(
        _REED_SEARCH_URL, params=params, auth=(api_key, ""), timeout=15.0
    )
    response.raise_for_status()
    return response.json()


def _parse_reed_date(value: object) -> datetime.date | None:
    """Parse Reed's `date` field (`"DD/MM/YYYY"`) into a date.

    Args:
        value: The raw `date` value from a Reed result dict.

    Returns:
        The parsed date, or `None` if `value` is missing or unparseable —
        treated as "no filter" for that job rather than an error.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None


class ReedConnector:
    """Fetches job postings from Reed's keyed, offset-paginated search API."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        api_key: str,
        fetch_page_fn: Callable[..., dict[str, object]] = _fetch_reed_page,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for every page fetch.
            api_key: Reed API key.
            fetch_page_fn: Injectable per-page fetch — defaults to the
                real Reed API call.
        """
        self._http_client = http_client
        self._api_key = api_key
        self._fetch_page_fn = fetch_page_fn

    def fetch(
        self, query: ReedQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield job postings matching `query`, paginating as needed.

        Args:
            query: `ReedQuery` — keywords, optional location, pagination
                caps.
            since: Only yield postings whose `date` is at or after this
                time (compared by date only — Reed gives no time-of-day).
                `None` means no filter.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            One `RawJob` per result, across every fetched page.
        """
        since_date = since.date() if since is not None else None

        for page in range(query.max_pages):
            skip = page * query.results_per_page
            body = self._fetch_page_fn(
                self._http_client,
                api_key=self._api_key,
                keywords=query.keywords,
                location=query.location,
                results_to_skip=skip,
                results_to_take=query.results_per_page,
            )
            results = cast(list[dict[str, object]], body.get("results", []))
            if not results:
                return

            fetched_at = datetime.datetime.now(datetime.UTC)
            for result in results:
                posted = _parse_reed_date(result.get("date"))
                if (
                    since_date is not None
                    and posted is not None
                    and posted < since_date
                ):
                    continue

                try:
                    job_url = str(result["jobUrl"])
                    canonical_url = canonicalise_url(job_url)
                    yield RawJob(
                        source_name="reed",
                        source_job_id=str(result["jobId"]),
                        job_url=job_url,
                        job_url_canonical=canonical_url,
                        payload=dict(result),
                        fetched_at=fetched_at,
                        run_id=run_id,
                        request_params={
                            "keywords": query.keywords,
                            "location": query.location,
                            "results_to_skip": skip,
                        },
                        payload_sha256=_hash_result(result),
                    )
                except KeyError:
                    _logger.warning(
                        "Reed result at results_to_skip=%s is malformed "
                        "(missing required field); skipping this record",
                        skip,
                        exc_info=True,
                    )
                    continue

            if len(results) < query.results_per_page:
                return
