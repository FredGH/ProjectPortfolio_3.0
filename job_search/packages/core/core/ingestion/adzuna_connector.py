"""AdzunaConnector — keyed REST with pagination (PLAN.md Step 4).

Free tier is 1,000 calls/month, so `max_pages` defaults to a conservative
cap: pagination stops at the first short (< results_per_page) page OR
max_pages, whichever comes first, so one ingest run can never silently
burn the whole monthly budget on one broad query.
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

_ADZUNA_SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
_DEFAULT_MAX_PAGES = 5
_DEFAULT_RESULTS_PER_PAGE = 50


@dataclass(frozen=True)
class AdzunaQuery:
    """AdzunaConnector's `query` type.

    Attributes:
        keywords: Free-text search terms, e.g. "data engineer". Empty
            string is valid when `category` is given — see `category`.
        country: Adzuna's two-letter country code, e.g. "gb", "us".
        category: An Adzuna category tag (e.g. "it-jobs", "engineering-jobs"
            — the full list is at Adzuna's `/categories` endpoint, not
            called by this connector). When given, this is a discovery-
            style category sweep instead of a keyword search — live-
            verified during planning to work with `keywords=""` (no
            `what` param sent at all). `None` (the default) means a
            normal keyword search.
        max_pages: Safety cap on API calls per fetch() — pagination also
            stops early on a short page. Defaults to 5 (up to 250 results
            at the default results_per_page).
        results_per_page: Results requested per page. Adzuna's own max is
            50.
    """

    keywords: str = ""
    country: str = ""
    category: str | None = None
    max_pages: int = _DEFAULT_MAX_PAGES
    results_per_page: int = _DEFAULT_RESULTS_PER_PAGE


def _hash_result(result: dict[str, object]) -> str:
    """Hash one Adzuna result dict for dedup/change detection.

    Args:
        result: One entry from Adzuna's `results` array.

    Returns:
        The SHA-256 hex digest of the result's canonical JSON form.
    """
    canonical = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fetch_adzuna_page(
    http_client: httpx.Client,
    *,
    app_id: str,
    app_key: str,
    country: str,
    page: int,
    results_per_page: int,
    what: str,
    category: str | None,
    max_days_old: int | None,
) -> dict[str, object]:
    """Fetch one page of Adzuna search results.

    Args:
        http_client: The shared HTTP client.
        app_id: Adzuna application ID.
        app_key: Adzuna application key.
        country: Adzuna's two-letter country code.
        page: 1-indexed page number.
        results_per_page: Results requested for this page.
        what: Free-text keyword query. Omitted from the request entirely
            when empty (a category-only sweep sends no `what` at all).
        category: An Adzuna category tag for a category sweep, or `None`
            for a normal keyword search.
        max_days_old: Only return postings at most this many days old, or
            `None` for no age filter.

    Returns:
        The parsed JSON response body (`{"results": [...], "count": N}`).

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
    """
    params: dict[str, str | int] = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": results_per_page,
        "content-type": "application/json",
    }
    if what:
        params["what"] = what
    if category:
        params["category"] = category
    if max_days_old is not None:
        params["max_days_old"] = max_days_old

    response = http_client.get(
        _ADZUNA_SEARCH_URL.format(country=country, page=page),
        params=params,
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


class AdzunaConnector:
    """Fetches job postings from Adzuna's keyed, paginated search API."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        app_id: str,
        app_key: str,
        fetch_page_fn: Callable[..., dict[str, object]] = _fetch_adzuna_page,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for every page fetch.
            app_id: Adzuna application ID.
            app_key: Adzuna application key.
            fetch_page_fn: Injectable per-page fetch — defaults to the
                real Adzuna API call.
        """
        self._http_client = http_client
        self._app_id = app_id
        self._app_key = app_key
        self._fetch_page_fn = fetch_page_fn

    def fetch(
        self, query: AdzunaQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield job postings matching `query`, paginating as needed.

        Args:
            query: `AdzunaQuery` — keywords, country, and pagination caps.
            since: Only fetch postings at most this old — translated into
                Adzuna's `max_days_old` parameter (rounded up to whole
                days). `None` means no age filter.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            One `RawJob` per result, across every fetched page.
        """
        max_days_old = None
        if since is not None:
            age = datetime.datetime.now(datetime.UTC) - since
            whole_days = age.days + (1 if age.seconds or age.microseconds else 0)
            max_days_old = max(1, whole_days)

        for page in range(1, query.max_pages + 1):
            body = self._fetch_page_fn(
                self._http_client,
                app_id=self._app_id,
                app_key=self._app_key,
                country=query.country,
                page=page,
                results_per_page=query.results_per_page,
                what=query.keywords,
                category=query.category,
                max_days_old=max_days_old,
            )
            results = cast(list[dict[str, object]], body.get("results", []))
            if not results:
                return

            fetched_at = datetime.datetime.now(datetime.UTC)
            for result in results:
                try:
                    job_url = str(result["redirect_url"])
                    canonical_url = canonicalise_url(job_url)
                    yield RawJob(
                        source_name="adzuna",
                        source_job_id=str(result["id"]),
                        job_url=job_url,
                        job_url_canonical=canonical_url,
                        payload=dict(result),
                        fetched_at=fetched_at,
                        run_id=run_id,
                        request_params={
                            "what": query.keywords,
                            "category": query.category,
                            "country": query.country,
                            "page": page,
                        },
                        payload_sha256=_hash_result(result),
                    )
                except KeyError:
                    _logger.warning(
                        "Adzuna result on page=%s is malformed (missing "
                        "required field); skipping this record",
                        page,
                        exc_info=True,
                    )
                    continue

            if len(results) < query.results_per_page:
                return
