"""The shared ingestion runner (PLAN.md Step 3).

Owns everything connector-agnostic: an optional rate-limit wait, retrying
the whole fetch on failure, landing writes, bronze loads, and run
metadata. A connector's only job is fetch() — this is what makes adding
one "one new file plus one sources.yml block, no runner changes."
"""

from __future__ import annotations

import datetime
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from core.ingestion.bronze import load_to_bronze
from core.ingestion.connector import Connector
from core.ingestion.landing import write_landing_record
from core.ingestion.rate_limiter import TokenBucket
from core.ingestion.raw_job import RawJob
from core.ingestion.retry import retry_with_backoff
from core.ingestion.run_id import generate_run_id
from core.ingestion.run_metadata import RunMetadata, write_run_metadata

_MAX_QUERY_SUMMARY_LENGTH = 200


def _summarize_query(query: object) -> str:
    """Render `query` as a short string safe for a run-metadata record.

    Args:
        query: The connector-specific query object passed to run_connector.

    Returns:
        `str(query)`, truncated to `_MAX_QUERY_SUMMARY_LENGTH` characters
        with a `"...(N more chars)"` suffix if truncation occurred — run
        metadata is a lightweight summary, not a second copy of the
        connector's full input (e.g. a manual entry's entire pasted job
        posting).
    """
    text = str(query)
    if len(text) <= _MAX_QUERY_SUMMARY_LENGTH:
        return text
    remaining = len(text) - _MAX_QUERY_SUMMARY_LENGTH
    return f"{text[:_MAX_QUERY_SUMMARY_LENGTH]}...({remaining} more chars)"


@dataclass(frozen=True)
class RunResult:
    """The outcome of one run_connector() call.

    Attributes:
        run_metadata: The `RunMetadata` this run produced.
        raw_jobs: Every `RawJob` the connector yielded, in order.
        landing_paths: The landing-zone path written for each `raw_jobs`
            entry, at the same index.
    """

    run_metadata: RunMetadata
    raw_jobs: list[RawJob]
    landing_paths: list[str]


def run_connector(
    *,
    connector_key: str,
    connector: Connector,
    query: object,
    since: datetime.datetime | None,
    entry_method: str,
    collection_channel: str = "targeted",
    landing_uri: str,
    database_url: str,
    rate_limiter: TokenBucket | None = None,
    retry_base: float = 2.0,
    retry_max_retries: int = 5,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    load_to_bronze_fn: Callable[..., None] = load_to_bronze,
    write_landing_record_fn: Callable[..., str] = write_landing_record,
    write_run_metadata_fn: Callable[..., str] = write_run_metadata,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[], float] = random.random,
) -> RunResult:
    """Run one connector end to end: fetch, land, load to bronze, record.

    Args:
        connector_key: Identifies this connector for rate limiting and run
            metadata — e.g. "adzuna" or "manual". Distinct from any
            individual `RawJob.source_name`, which can vary per item
            (e.g. a user-typed label for manual entries).
        connector: The `Connector` to run.
        query: Passed through to `connector.fetch()` untouched.
        since: Passed through to `connector.fetch()` untouched.
        entry_method: "api", "manual", or "scraped" — stamped onto every
            bronze row this run produces.
        collection_channel: "targeted" (default) or "discovery" — stamped
            onto every bronze row this run produces, alongside
            entry_method. See PLAN.md Step 4a.
        landing_uri: Root URI of the landing zone.
        database_url: The migration/owner Postgres DSN for bronze loads.
        rate_limiter: When given, `acquire()` is called once before the
            fetch — never per yielded item. `None` means unthrottled.
        retry_base: Base delay in seconds for retrying a failed fetch.
        retry_max_retries: Max retries for a failed fetch.
        retry_on: Exception types that trigger a retry. Anything else
            propagates immediately with zero retries — lets a caller scope
            retries to transient failures instead of retrying every
            exception, including permanent ones.
        load_to_bronze_fn: Injectable bronze loader.
        write_landing_record_fn: Injectable landing-zone writer.
        write_run_metadata_fn: Injectable run-metadata writer.
        sleep_fn: Injectable sleep, threaded into the retry policy.
        jitter_fn: Injectable jitter, threaded into the retry policy.

    Returns:
        The `RunResult` describing what this run produced.

    Raises:
        ValueError: If `collection_channel` is not "targeted" or
            "discovery" — checked before any fetch/landing/bronze work
            starts, so a typo fails fast instead of surfacing mid-loop as
            a Postgres CHECK-constraint violation after some records may
            have already landed.
        Exception: Whatever the connector's `fetch()` raised, once
            retries are exhausted. A `status="failed"` run metadata record
            is written before re-raising.
    """
    run_id = generate_run_id()
    started_at = datetime.datetime.now(datetime.UTC)

    if collection_channel not in ("targeted", "discovery"):
        raise ValueError(
            f"collection_channel must be 'targeted' or 'discovery', got "
            f"{collection_channel!r}"
        )

    if rate_limiter is not None:
        rate_limiter.acquire()

    def _do_fetch() -> list[RawJob]:
        """Materialise the connector's fetch() generator into a list.

        Returns:
            Every `RawJob` the connector yielded, in order.
        """
        return list(connector.fetch(query, since, run_id=run_id))

    try:
        raw_jobs = retry_with_backoff(
            _do_fetch,
            base=retry_base,
            max_retries=retry_max_retries,
            sleep=sleep_fn,
            jitter=jitter_fn,
            retry_on=retry_on,
        )
    except Exception:
        finished_at = datetime.datetime.now(datetime.UTC)
        write_run_metadata_fn(
            landing_uri,
            RunMetadata(
                run_id=run_id,
                source_name=connector_key,
                query=_summarize_query(query),
                records=0,
                started_at=started_at,
                finished_at=finished_at,
                status="failed",
                collection_channel=collection_channel,
            ),
        )
        raise

    landing_paths: list[str] = []
    for raw_job in raw_jobs:
        landing_record = {
            "_source_name": raw_job.source_name,
            "_source_job_id": raw_job.source_job_id,
            "_job_url": raw_job.job_url,
            "_fetched_at": raw_job.fetched_at.isoformat(),
            "_run_id": raw_job.run_id,
            "_request_params": raw_job.request_params,
            "_payload_sha256": raw_job.payload_sha256,
            **raw_job.payload,
        }
        path = write_landing_record_fn(
            landing_uri,
            source_name=raw_job.source_name,
            run_id=raw_job.run_id,
            record=landing_record,
            fetched_at=raw_job.fetched_at,
        )
        landing_paths.append(path)

        load_to_bronze_fn(
            database_url=database_url,
            source_name=raw_job.source_name,
            source_job_id=raw_job.source_job_id,
            job_url=raw_job.job_url,
            job_url_canonical=raw_job.job_url_canonical,
            entry_method=entry_method,
            collection_channel=collection_channel,
            fetched_at=raw_job.fetched_at,
            run_id=raw_job.run_id,
            request_params=raw_job.request_params,
            payload=raw_job.payload,
            payload_sha256=raw_job.payload_sha256,
        )

    finished_at = datetime.datetime.now(datetime.UTC)
    metadata = RunMetadata(
        run_id=run_id,
        source_name=connector_key,
        query=_summarize_query(query),
        records=len(raw_jobs),
        started_at=started_at,
        finished_at=finished_at,
        status="success",
        collection_channel=collection_channel,
    )
    write_run_metadata_fn(landing_uri, metadata)

    return RunResult(
        run_metadata=metadata, raw_jobs=raw_jobs, landing_paths=landing_paths
    )
