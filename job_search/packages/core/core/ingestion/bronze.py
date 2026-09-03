"""The dlt load from a landing record into bronze.raw_jobs (PLAN.md Step 2).

Append-only with a dedup twist: unique on (source_name, source_job_id,
payload_sha256) means a re-fetch with an unchanged payload is a no-op,
and a changed payload becomes a new version row — free posting-change
history for Step 21a's lifecycle metric.
"""

from __future__ import annotations

import datetime

import dlt


def _to_dlt_dsn(database_url: str) -> str:
    """Rewrite a SQLAlchemy-style DSN into the plain form dlt/psycopg2 need.

    `core.settings.Settings.database_url` uses the `postgresql+psycopg://`
    scheme SQLAlchemy expects. dlt's Postgres destination connects with
    psycopg2 directly (not SQLAlchemy) and its DSN parser rejects the
    `+psycopg` driver suffix as an "invalid connection option" — observed
    as `psycopg2.ProgrammingError: invalid dsn: invalid connection option
    "postgresql+psycopg://..."`. Stripping the suffix back to the bare
    `postgresql://` scheme is what psycopg2 (and dlt) expect.

    Args:
        database_url: A `postgresql+psycopg://...` (or already-bare
            `postgresql://...`) DSN.

    Returns:
        The DSN with any `+<driver>` suffix removed from the scheme.
    """
    scheme, _, rest = database_url.partition("://")
    bare_scheme = scheme.split("+", 1)[0]
    return f"{bare_scheme}://{rest}"


def load_to_bronze(
    *,
    database_url: str,
    source_name: str,
    source_job_id: str,
    job_url: str,
    job_url_canonical: str,
    entry_method: str,
    fetched_at: datetime.datetime,
    run_id: str,
    request_params: dict[str, object],
    payload: dict[str, object],
    payload_sha256: str,
) -> None:
    """Load one raw-job record into bronze.raw_jobs via a dlt merge load.

    Args:
        database_url: The migration/owner Postgres DSN. This is a batch
            pipeline operation, not a live per-user query — see the
            two-zone rule in docs/tenancy.md — so it connects with the
            same trust level as Alembic, never the RLS-enforced app role.
        source_name: The source this record came from, e.g. "linkedin_manual".
        source_job_id: The extracted or hashed job identifier.
        job_url: The original (uncanonicalised) job URL.
        job_url_canonical: The canonicalised job URL.
        entry_method: "manual", "api", or "scraped".
        fetched_at: When this record was captured.
        run_id: The ULID identifying this ingestion run.
        request_params: Any request parameters that produced this record
            (empty for manual entry).
        payload: The full record payload, including `raw_text`.
        payload_sha256: SHA-256 hex digest of the payload's dedup-relevant
            content — together with source_name/source_job_id, this is the
            merge key that gives the no-op/new-version-row behaviour.

    Raises:
        Exception: Whatever dlt raises on a genuine load failure (network,
            schema conflict). Not caught here — the caller decides whether
            a bronze-load failure should fail the whole ingest request.
    """
    record = {
        "source_name": source_name,
        "source_job_id": source_job_id,
        "job_url": job_url,
        "job_url_canonical": job_url_canonical,
        "entry_method": entry_method,
        "fetched_at": fetched_at,
        "run_id": run_id,
        "request_params": request_params,
        "payload": payload,
        "payload_sha256": payload_sha256,
    }

    resource = dlt.resource(
        [record],
        name="raw_jobs",
        table_name="raw_jobs",
        write_disposition="merge",
        primary_key=("source_name", "source_job_id", "payload_sha256"),
        columns={
            "request_params": {"data_type": "json"},
            "payload": {"data_type": "json"},
        },
        max_table_nesting=0,
    )

    pipeline = dlt.pipeline(
        pipeline_name="job_search_bronze",
        destination=dlt.destinations.postgres(credentials=_to_dlt_dsn(database_url)),
        dataset_name="bronze",
    )
    pipeline.run(resource)
