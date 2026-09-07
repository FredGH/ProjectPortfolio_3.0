-- dedup__exact_duplicates: one row per job that shares job_url_canonical
-- or content_sha256 with at least one other job (PLAN.md Step 7's cheap
-- exact-match check). Grain: (job_key, duplicate_type).

WITH url_groups AS (

    SELECT
        job_key,
        'url_canonical' AS duplicate_type,
        job_url_canonical AS duplicate_group_key,
        COUNT(*) OVER (PARTITION BY job_url_canonical) AS group_size
    FROM {{ ref('silver__job_posting') }}

),

-- Content-hash groups, joined in from the Python-computed blocking-keys
-- table (dbt never recomputes the hash, only groups by it).
hash_groups AS (

    SELECT
        job_key,
        'content_hash' AS duplicate_type,
        content_sha256 AS duplicate_group_key,
        COUNT(*) OVER (PARTITION BY content_sha256) AS group_size
    FROM {{ source('dedup_ingest', 'job_blocking_keys') }}

)

SELECT * FROM url_groups WHERE group_size > 1
UNION ALL
SELECT * FROM hash_groups WHERE group_size > 1
