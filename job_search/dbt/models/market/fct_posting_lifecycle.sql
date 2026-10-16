-- fct_posting_lifecycle: how long a posting stays live (PLAN.md Step 21a), a
-- proxy for time-to-fill. Grain: job_group_id (unique).
--
-- still_live: the job appears in the latest fetch run of one of its panel
-- sources. A live job is CENSORED: days_live is NULL, not a fake duration,
-- because it has not disappeared yet. days_live = days between first and last
-- seen for jobs that are gone. With few repeat fetches most jobs are live or
-- have days_live = 0; the signal accrues as collection repeats.

WITH latest_run AS (

    SELECT DISTINCT ON (source_name) source_name, run_id
    FROM {{ source('bronze', 'raw_jobs') }}
    ORDER BY source_name, fetched_at DESC

),

live AS (

    SELECT DISTINCT im.job_group_id
    FROM {{ source('silver_ingest', 'job_identity_map') }} AS im
    INNER JOIN latest_run AS lr
        ON im.source_name = lr.source_name
    INNER JOIN {{ source('bronze', 'raw_jobs') }} AS r
        ON r.source_name = im.source_name
        AND r.source_job_id = im.source_job_id
        AND r.run_id = lr.run_id

)

SELECT
    j.job_group_id,
    j.category,
    j.region,
    j.first_seen_at,
    j.last_seen_at,
    j.posted_at,
    (live.job_group_id IS NOT NULL) AS still_live,
    CASE WHEN live.job_group_id IS NULL
        THEN EXTRACT(EPOCH FROM (j.last_seen_at - j.first_seen_at)) / 86400.0
    END AS days_live,
    CASE WHEN j.posted_at IS NOT NULL
        THEN EXTRACT(EPOCH FROM (j.first_seen_at - j.posted_at)) / 86400.0
    END AS age_days_when_first_seen
FROM {{ ref('int_market_job') }} AS j
LEFT JOIN live
    ON j.job_group_id = live.job_group_id
