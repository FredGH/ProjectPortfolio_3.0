-- stg_reed__jobs: one row per Reed posting, mapped to the shared staging
-- contract. Grain: source_job_id (unique within source_name).

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
    TO_TIMESTAMP(NULLIF(payload ->> 'date', ''), 'DD/MM/YYYY') AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
WHERE source_name = 'reed'
