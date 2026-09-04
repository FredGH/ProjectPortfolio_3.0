-- stg_reed__jobs: one row per Reed posting fetch, mapped to the shared
-- staging contract. bronze.raw_jobs is append-only/versioned, so
-- source_job_id can repeat across fetched_at/payload_sha256 versions of
-- the same posting — see int_jobs__unioned for the current-state collapse.

SELECT
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    payload ->> 'jobTitle' AS title,
    payload ->> 'employerName' AS company,
    payload ->> 'locationName' AS location,
    payload ->> 'jobDescription' AS description,
    CASE
        WHEN payload ->> 'minimumSalary' IS NOT NULL
            OR payload ->> 'maximumSalary' IS NOT NULL
        THEN CONCAT_WS(
            ' ',
            CONCAT_WS(
                '-', payload ->> 'minimumSalary', payload ->> 'maximumSalary'
            ),
            payload ->> 'currency'
        )
        ELSE NULL
    END AS salary_raw,
    -- Reed's `date` is "DD/MM/YYYY" (day-first) — same format
    -- ReedConnector's own _parse_reed_date already assumes. A plain
    -- `::timestamptz` cast relies on Postgres's datestyle (MDY by
    -- default here) and hard-errors on any real row where day > 12
    -- (e.g. "25/11/2025"), so this parses the format explicitly instead.
    -- TO_TIMESTAMP() itself resolves the parsed fields against the
    -- server's session TimeZone, so the round-trip through ::timestamp
    -- (recovering the parsed wall-clock value) and back via
    -- `AT TIME ZONE 'UTC'` (treating that value as UTC, matching
    -- ReedConnector's own assumption) keeps posted_at correct
    -- regardless of the server's session TimeZone setting.
    (
        TO_TIMESTAMP(NULLIF(payload ->> 'date', ''), 'DD/MM/YYYY')::timestamp
            AT TIME ZONE 'UTC'
    ) AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
-- entry_method = 'api' excludes a manual entry whose free-text source_name
-- field happens to match 'reed' (ManualJobQuery.source_name is
-- user-supplied, not validated against real source names) — without this,
-- such a row would land in both this model and stg_manual__jobs.
WHERE source_name = 'reed'
    AND entry_method = 'api'
