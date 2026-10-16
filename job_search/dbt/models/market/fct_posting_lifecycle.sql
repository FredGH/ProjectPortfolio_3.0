-- fct_posting_lifecycle: how long a posting stays live (PLAN.md Step 21a), a
-- proxy for time-to-fill. Grain: job_group_id (unique).
--
-- still_live: one of the job's panel postings was seen within the live window
-- (var lifecycle_live_window_days, default 7) of its source's latest fetch.
-- One collection run covers one query, so "in the latest run" would call every
-- job from an earlier query gone; the window stands in for "the latest pass".
-- Set it to the collection cadence. A live job is CENSORED: days_live is NULL,
-- not a fake duration, because it has not disappeared yet. days_live = days
-- between first and last seen for jobs that are gone.

WITH source_latest AS (

    SELECT r.source_name, MAX(r.fetched_at) AS latest_fetch
    FROM {{ source('bronze', 'raw_jobs') }} AS r
    INNER JOIN {{ ref('source_panel') }} AS p USING (source_name)
    WHERE r.collection_channel = 'targeted' AND r.entry_method = 'api'
    GROUP BY r.source_name

),

per_source_seen AS (

    SELECT
        im.job_group_id,
        im.source_name,
        MAX(r.fetched_at) AS last_fetched
    FROM {{ source('silver_ingest', 'job_identity_map') }} AS im
    INNER JOIN {{ source('bronze', 'raw_jobs') }} AS r
        ON r.source_name = im.source_name AND r.source_job_id = im.source_job_id
    WHERE r.collection_channel = 'targeted' AND r.entry_method = 'api'
    GROUP BY im.job_group_id, im.source_name

),

live AS (

    SELECT DISTINCT s.job_group_id
    FROM per_source_seen AS s
    INNER JOIN source_latest AS l USING (source_name)
    WHERE s.last_fetched >= l.latest_fetch - MAKE_INTERVAL(days => {{ var('lifecycle_live_window_days', 7) }})

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
