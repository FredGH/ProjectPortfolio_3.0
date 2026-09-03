"""GreenhouseConnector — the keyless per-company ATS-board connector
(PLAN.md Step 4).

Unlike a keyed search API, Greenhouse's public board endpoint returns the
company's *entire* open-role list in one call, no pagination and no query
string — the connector's only real job is iterating companies (from the
target_company registry, or an explicit override) and filtering by
`since` in-process, since the API itself has no incremental-fetch param.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from core.db.session import build_engine
from core.db.target_company import TargetCompany, list_active_companies
from core.ingestion.raw_job import RawJob
from core.ingestion.url_utils import canonicalise_url

_logger = logging.getLogger(__name__)

_GREENHOUSE_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs"


@dataclass(frozen=True)
class GreenhouseQuery:
    """GreenhouseConnector's `query` type.

    Attributes:
        board_slugs: Explicit list of board slugs to poll, overriding the
            registry. `None` (the default) means "poll every active
            company in the target_company registry".
    """

    board_slugs: list[str] | None = None


def _hash_job(job: dict[str, object]) -> str:
    """Hash one Greenhouse job dict for dedup/change detection.

    Args:
        job: The raw job dict as returned by the Greenhouse board API.

    Returns:
        The SHA-256 hex digest of the job's canonical JSON form.
    """
    canonical = json.dumps(job, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fetch_greenhouse_board(
    http_client: httpx.Client, board_slug: str
) -> list[dict[str, object]]:
    """Fetch one company's full open-role list from Greenhouse.

    Args:
        http_client: The shared HTTP client.
        board_slug: The company's Greenhouse board token.

    Returns:
        Every job dict in the board's `jobs` array (possibly empty).

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
    """
    response = http_client.get(
        _GREENHOUSE_BOARD_URL.format(board_slug=board_slug),
        params={"content": "true"},
        timeout=15.0,
    )
    response.raise_for_status()
    body = response.json()
    return list(body.get("jobs", []))


def _list_active_greenhouse_companies(
    database_url: str, *, ats_provider: str
) -> list[TargetCompany]:
    """Read the active target_company registry for one ATS provider.

    Args:
        database_url: The Postgres DSN to connect with. target_company has
            no RLS (docs/tenancy.md), so any role with SELECT works.
        ats_provider: Which provider to filter to.

    Returns:
        Every active `TargetCompany` row for that provider.
    """
    engine = build_engine(database_url)
    with engine.connect() as conn:
        return list_active_companies(conn, ats_provider=ats_provider)


@dataclass(frozen=True)
class _Company:
    """Internal (name, board_slug) pair — either from the registry or an
    explicit query.board_slugs override, which has no display name."""

    name: str
    board_slug: str


class GreenhouseConnector:
    """Fetches every open role from one or more companies' Greenhouse boards."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        database_url: str,
        fetch_board_fn: Callable[
            [httpx.Client, str], list[dict[str, object]]
        ] = _fetch_greenhouse_board,
        list_companies_fn: Callable[
            ..., list[TargetCompany]
        ] = _list_active_greenhouse_companies,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for every board fetch.
            database_url: Passed to `list_companies_fn` when
                `query.board_slugs` is `None`.
            fetch_board_fn: Injectable per-board fetch — defaults to the
                real Greenhouse API call.
            list_companies_fn: Injectable registry read — defaults to a
                real `target_company` query.
        """
        self._http_client = http_client
        self._database_url = database_url
        self._fetch_board_fn = fetch_board_fn
        self._list_companies_fn = list_companies_fn

    def fetch(
        self, query: GreenhouseQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield every open role across every company this query resolves to.

        Args:
            query: `GreenhouseQuery` — explicit board_slugs, or None to
                use the active registry.
            since: Only yield jobs whose `updated_at` is at or after this
                time. `None` means no filter.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            One `RawJob` per open role, across every resolved company.
        """
        if query.board_slugs:
            companies = [
                _Company(name=slug, board_slug=slug) for slug in query.board_slugs
            ]
        else:
            companies = [
                _Company(name=c.name, board_slug=c.board_slug)
                for c in self._list_companies_fn(
                    self._database_url, ats_provider="greenhouse"
                )
            ]

        for company in companies:
            try:
                jobs = self._fetch_board_fn(self._http_client, company.board_slug)
            except httpx.HTTPStatusError:
                _logger.warning(
                    "Greenhouse board fetch failed for board_slug=%s; skipping "
                    "this company for this run",
                    company.board_slug,
                    exc_info=True,
                )
                continue

            fetched_at = datetime.datetime.now(datetime.UTC)
            for job in jobs:
                updated_at = _parse_updated_at(job.get("updated_at"))
                if since is not None and updated_at is not None and updated_at < since:
                    continue

                job_url = str(job["absolute_url"])
                canonical_url = canonicalise_url(job_url)
                payload = {
                    **job,
                    "_company_name": company.name,
                    "_board_slug": company.board_slug,
                }
                yield RawJob(
                    source_name="greenhouse",
                    source_job_id=str(job["id"]),
                    job_url=job_url,
                    job_url_canonical=canonical_url,
                    payload=payload,
                    fetched_at=fetched_at,
                    run_id=run_id,
                    request_params={"board_slug": company.board_slug},
                    payload_sha256=_hash_job(job),
                )


def _parse_updated_at(value: object) -> datetime.datetime | None:
    """Parse Greenhouse's `updated_at` field into an aware datetime.

    Args:
        value: The raw `updated_at` value from a Greenhouse job dict,
            typically an ISO-8601 string like "2026-09-01T00:00:00Z".

    Returns:
        The parsed datetime, or `None` if `value` is missing or
        unparseable — treated as "no filter" for that job rather than an
        error, since a malformed date shouldn't drop an otherwise-valid
        posting.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
