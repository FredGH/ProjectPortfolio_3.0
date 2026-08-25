-- cortex_cost_audit: ad-hoc cost analysis over METERING_DAILY_HISTORY.
-- Covers all Cortex (AI services) charges alongside warehouse compute charges
-- so analysts can compare AI inference costs vs transformation costs.
--
-- Compile with: dbt compile --select analyses/cortex_cost_audit
-- Run the output SQL directly in a Snowflake worksheet (requires ACCOUNTADMIN or
-- a role with MONITOR USAGE privilege to read ACCOUNT_USAGE views).
--
-- OUTPUT COLUMNS:
--   report_date, cost_category, resource_name,
--   credits_used, estimated_usd (@ $3.00/credit), query_count

WITH warehouse_costs AS (
    -- Traditional warehouse compute charges
    SELECT
        START_TIME::DATE                         AS report_date,
        'warehouse_compute'                      AS cost_category,
        WAREHOUSE_NAME                           AS resource_name,
        SUM(CREDITS_USED)                        AS credits_used,
        COUNT(*)                                 AS query_count
    FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
    WHERE START_TIME >= DATEADD('day', -30, CURRENT_DATE)
    GROUP BY 1, 2, 3
),

cortex_costs AS (
    -- Cortex AI charges from METERING_DAILY_HISTORY (service_type = 'AI')
    SELECT
        USAGE_DATE                               AS report_date,
        'cortex_ai'                              AS cost_category,
        COALESCE(SERVICE_ATTRIBUTE_1, 'unknown') AS resource_name,
        SUM(CREDITS_USED)                        AS credits_used,
        NULL                                     AS query_count
    FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY
    WHERE USAGE_DATE >= DATEADD('day', -30, CURRENT_DATE)
      AND SERVICE_TYPE = 'AI_SERVICES'
    GROUP BY 1, 2, 3
),

serverless_costs AS (
    -- Serverless task charges (TASK_ANOMALY_DETECTION, TASK_COST_REPORT, etc.)
    SELECT
        COMPLETED_TIME::DATE                     AS report_date,
        'serverless_tasks'                       AS cost_category,
        NAME                                     AS resource_name,
        SUM(CREDITS_USED)                        AS credits_used,
        COUNT(*)                                 AS query_count
    FROM SNOWFLAKE.ACCOUNT_USAGE.SERVERLESS_TASK_HISTORY
    WHERE COMPLETED_TIME >= DATEADD('day', -30, CURRENT_DATE)
    GROUP BY 1, 2, 3
),

all_costs AS (
    SELECT * FROM warehouse_costs
    UNION ALL
    SELECT * FROM cortex_costs
    UNION ALL
    SELECT * FROM serverless_costs
),

-- Observability-layer Cortex costs (from CORTEX_USAGE_LOG if observability is enabled)
obs_cortex AS (
    SELECT
        logged_at::DATE                          AS report_date,
        'cortex_ai_observed'                     AS cost_category,
        function_name                            AS resource_name,
        SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) / 1000.0 * 0.04
                                                 AS credits_used,
        SUM(calls_count)                         AS query_count
    FROM CSTA_MARKETING_SHARED.OBSERVABILITY.CORTEX_USAGE_LOG
    WHERE logged_at >= DATEADD('day', -30, CURRENT_TIMESTAMP)
    GROUP BY 1, 2, 3
)

SELECT
    report_date,
    cost_category,
    resource_name,
    ROUND(credits_used, 4)                       AS credits_used,
    ROUND(credits_used * 3.0, 2)                 AS estimated_usd,
    query_count
FROM (
    SELECT * FROM all_costs
    UNION ALL
    SELECT * FROM obs_cortex
) AS combined
WHERE credits_used > 0
ORDER BY
    report_date DESC,
    credits_used DESC
