-- int_discovery_job: the corpus emergent-role detection reads (PLAN.md Step
-- 21b), NOT the targeted one: novelty against a frozen keyword matrix would
-- only rediscover its own assumptions. Grain: job_group_id (unique), API jobs
-- delivered by the discovery channel. is_agency uses the Step 21a rule so
-- agency title churn can be filtered BEFORE novelty scoring.

WITH postings AS (

    SELECT
        im.job_group_id,
        im.source_name,
        fs.first_seen_at,
        MAX(r.fetched_at) AS last_fetched
    FROM {{ source('silver_ingest', 'job_identity_map') }} AS im
    INNER JOIN {{ ref('int_discovery_first_seen') }} AS fs
        ON im.source_name = fs.source_name AND im.source_job_id = fs.source_job_id
    INNER JOIN {{ source('bronze', 'raw_jobs') }} AS r
        ON im.source_name = r.source_name AND im.source_job_id = r.source_job_id
        AND r.collection_channel = 'discovery' AND r.entry_method = 'api'
    GROUP BY im.job_group_id, im.source_name, im.source_job_id, fs.first_seen_at

),

per_job AS (

    SELECT
        job_group_id,
        MIN(first_seen_at) AS first_seen_at,
        MAX(last_fetched) AS last_seen_at,
        ARRAY_AGG(DISTINCT source_name ORDER BY source_name) AS sources
    FROM postings
    GROUP BY job_group_id

),

agency_resolution AS (

    SELECT job_group_id, poster_kind
    FROM {{ source('intel_ingest', 'job_company') }}

)

SELECT
    dj.job_group_id,
    pj.first_seen_at,
    pj.last_seen_at,
    pj.sources,
    dj.title_raw,
    dj.company,
    dj.normalised_company,
    COALESCE(dj.category, 'unclassified') AS category,
    CASE ar.poster_kind
        WHEN 'agency' THEN TRUE
        WHEN 'employer' THEN FALSE
        ELSE {{ agency_name_matches('dj.company') }}
    END AS is_agency
FROM {{ ref('dim_job') }} AS dj
INNER JOIN per_job AS pj
    ON dj.job_group_id = pj.job_group_id
LEFT JOIN agency_resolution AS ar
    ON dj.job_group_id = ar.job_group_id
