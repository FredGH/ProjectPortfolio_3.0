{{ config(
    materialized='incremental',
    incremental_strategy='append',
    full_refresh=false
) }}

-- int_discovery_first_seen: when WE first saw each DISCOVERY-channel posting
-- (PLAN.md Step 21b). Grain: (source_name, source_job_id), insert-only, like
-- int_market_first_seen and for the same reason (bronze moves fetched_at on an
-- unchanged re-fetch). Only real sources count (seeds/source_panel.csv doubles
-- as the allowlist, which keeps the test_source_* rows out).

SELECT
    r.source_name,
    r.source_job_id,
    MIN(r.fetched_at) AS first_seen_at
FROM {{ source('bronze', 'raw_jobs') }} AS r
INNER JOIN {{ ref('source_panel') }} AS p
    ON r.source_name = p.source_name
WHERE r.collection_channel = 'discovery'
    AND r.entry_method = 'api'
{% if is_incremental() %}
    AND NOT EXISTS (
        SELECT 1 FROM {{ this }} AS t
        WHERE t.source_name = r.source_name AND t.source_job_id = r.source_job_id
    )
{% endif %}
GROUP BY r.source_name, r.source_job_id
