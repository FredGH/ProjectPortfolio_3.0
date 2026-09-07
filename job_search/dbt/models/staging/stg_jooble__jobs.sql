-- stg_jooble__jobs: one row per Jooble posting fetch, mapped to the
-- shared staging contract. bronze.raw_jobs is append-only/versioned, so
-- source_job_id can repeat across fetched_at/payload_sha256 versions of
-- the same posting — see int_jobs__unioned for the current-state collapse.

SELECT
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    payload ->> 'title' AS title,
    payload ->> 'company' AS company,
    payload ->> 'location' AS location,
    payload ->> 'snippet' AS description,
    NULLIF(payload ->> 'salary', '') AS salary_raw,
    -- Jooble's `updated` has no UTC offset (e.g. "2026-08-05T07:54:35.61"),
    -- matching JoobleConnector's own _parse_jooble_updated, which treats
    -- naive values as UTC. A plain `::timestamptz` cast would instead
    -- interpret it in whatever the server's session TimeZone happens to
    -- be, silently shifting posted_at with no failing test to catch it.
    (NULLIF(payload ->> 'updated', '')::timestamp AT TIME ZONE 'UTC')
        AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
-- entry_method = 'api' excludes a manual entry whose free-text source_name
-- field happens to match 'jooble' (ManualJobQuery.source_name is
-- user-supplied, not validated against real source names) — without this,
-- such a row would land in both this model and stg_manual__jobs.
WHERE source_name = 'jooble'
    AND entry_method = 'api'
