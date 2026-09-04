-- stg_greenhouse__jobs: one row per Greenhouse posting fetch, mapped to
-- the shared staging contract. bronze.raw_jobs is append-only/versioned,
-- so source_job_id can repeat across fetched_at/payload_sha256 versions
-- of the same posting — see int_jobs__unioned for the current-state
-- collapse.

SELECT
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    payload ->> 'title' AS title,
    payload ->> 'company_name' AS company,
    payload -> 'location' ->> 'name' AS location,
    payload ->> 'content' AS description,
    -- Greenhouse doesn't expose salary data anywhere in its board API.
    NULL AS salary_raw,
    COALESCE(
        NULLIF(payload ->> 'first_published', '')::timestamptz,
        NULLIF(payload ->> 'updated_at', '')::timestamptz
    ) AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
WHERE source_name = 'greenhouse'
