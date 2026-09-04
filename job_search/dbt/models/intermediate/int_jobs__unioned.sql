-- int_jobs__unioned: one row per (source_name, source_job_id), current
-- version only — bronze is append-only with version rows on payload
-- change (bronze.py's own merge-key design), so this is where that
-- collapses to current state. Grain: job_key (source_name, source_job_id).

WITH unioned AS (

    SELECT * FROM {{ ref('stg_adzuna__jobs') }}
    UNION ALL
    SELECT * FROM {{ ref('stg_reed__jobs') }}
    UNION ALL
    SELECT * FROM {{ ref('stg_greenhouse__jobs') }}
    UNION ALL
    SELECT * FROM {{ ref('stg_jooble__jobs') }}
    UNION ALL
    SELECT * FROM {{ ref('stg_manual__jobs') }}

),

-- Rank every version of the same posting by recency, so only the latest
-- fetch of a changed payload survives.
ranked AS (

    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY source_name, source_job_id
            ORDER BY fetched_at DESC
        ) AS version_rank
    FROM unioned

)

SELECT
    {{ dbt_utils.generate_surrogate_key(['source_name', 'source_job_id']) }}
        AS job_key,
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    title,
    company,
    location,
    description,
    salary_raw,
    posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM ranked
WHERE version_rank = 1
