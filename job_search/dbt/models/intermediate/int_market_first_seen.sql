{{ config(
    materialized='incremental',
    incremental_strategy='append',
    full_refresh=false
) }}

-- int_market_first_seen: when WE first saw each panel posting (PLAN.md Step
-- 21a trend date). Grain: (source_name, source_job_id), insert-only.
--
-- bronze.raw_jobs cannot answer this: its dlt merge replaces an unchanged
-- re-fetch in place, so MIN(fetched_at) drifts to the latest sighting. This
-- table keeps the first value it ever saw and never updates it (hence
-- full_refresh=false: a rebuild would reset history). Run dbt after each
-- collection pass, so a posting is recorded close to when it first appeared.
--
-- Only the TARGETED channel and API entries count (PLAN.md Step 4a: discovery
-- records are excluded from targeted trend metrics, manual entries from every
-- market chart).

SELECT
    r.source_name,
    r.source_job_id,
    MIN(r.fetched_at) AS first_seen_at
FROM {{ source('bronze', 'raw_jobs') }} AS r
INNER JOIN {{ ref('source_panel') }} AS p
    ON r.source_name = p.source_name
WHERE r.collection_channel = 'targeted'
    AND r.entry_method = 'api'
{% if is_incremental() %}
    AND NOT EXISTS (
        SELECT 1 FROM {{ this }} AS t
        WHERE t.source_name = r.source_name AND t.source_job_id = r.source_job_id
    )
{% endif %}
GROUP BY r.source_name, r.source_job_id
