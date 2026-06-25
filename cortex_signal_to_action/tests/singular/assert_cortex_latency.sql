-- assert_cortex_latency: Tier 3 Cortex performance gate.
-- Queries CORTEX_USAGE_LOG (populated by observability hooks in Phase 8) and fails
-- if any Cortex function's average latency in the most recent pipeline run exceeds 5 seconds.
--
-- Enabled only when observability_enabled = true (set in Phase 8).
-- Disabled by default so dbt test does not error before CORTEX_USAGE_LOG exists.
--
-- Run: dbt test --select assert_cortex_latency --vars '{"observability_enabled": true}' --target uat

{{ config(enabled=var('observability_enabled', false)) }}

{% set shared_db = var('shared_database', 'CSTA_MARKETING_SHARED') %}

WITH latest_run AS (
    SELECT MAX(run_id) AS run_id
    FROM {{ shared_db }}.OBSERVABILITY.CORTEX_USAGE_LOG
),

latency_by_function AS (
    SELECT
        cul.function_name,
        cul.run_id,
        AVG(cul.latency_ms) / 1000.0 AS avg_latency_seconds,
        COUNT(*) AS call_count,
        MIN(cul.latency_ms) / 1000.0 AS min_latency_seconds,
        MAX(cul.latency_ms) / 1000.0 AS max_latency_seconds,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cul.latency_ms) / 1000.0
            AS p95_latency_seconds
    FROM {{ shared_db }}.OBSERVABILITY.CORTEX_USAGE_LOG AS cul
    INNER JOIN latest_run AS lr USING (run_id)
    GROUP BY cul.function_name, cul.run_id
)

SELECT
    function_name,
    ROUND(avg_latency_seconds, 3) AS avg_latency_seconds,
    ROUND(p95_latency_seconds, 3) AS p95_latency_seconds,
    ROUND(max_latency_seconds, 3) AS max_latency_seconds,
    call_count,
    5.0 AS latency_threshold_seconds,
    run_id
FROM latency_by_function
WHERE avg_latency_seconds > 5.0
