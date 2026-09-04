-- stg_adzuna__jobs: one row per Adzuna posting fetch, mapped to the
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
    payload -> 'company' ->> 'display_name' AS company,
    payload -> 'location' ->> 'display_name' AS location,
    payload ->> 'description' AS description,
    CASE
        WHEN payload ->> 'salary_min' IS NOT NULL
            OR payload ->> 'salary_max' IS NOT NULL
        THEN CONCAT_WS('-', payload ->> 'salary_min', payload ->> 'salary_max')
        ELSE NULL
    END AS salary_raw,
    NULLIF(payload ->> 'created', '')::timestamptz AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
WHERE source_name = 'adzuna'
