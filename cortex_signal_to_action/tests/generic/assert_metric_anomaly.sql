-- assert_metric_anomaly: Tier 4 time-series anomaly detection.
-- Aggregates the target column by a date dimension and flags time periods where the
-- metric deviates by more than threshold_pct from its rolling lookback average.
--
-- Parameters:
--   date_column          — temporal grouping column (default: 'iso_week')
--   lookback_weeks       — number of prior periods in rolling window (default: 8)
--   threshold_pct        — maximum allowed percentage deviation from rolling mean (default: 50)
--   aggregation_function — SQL aggregate applied per period: 'SUM' or 'AVG' (default: 'SUM')
--
-- Usage in schema.yml:
--   tests:
--     - assert_metric_anomaly:
--         date_column: iso_week
--         lookback_weeks: 8
--         threshold_pct: 50
--         aggregation_function: SUM
--         severity: warn

{% test assert_metric_anomaly(
    model,
    column_name,
    date_column='iso_week',
    lookback_weeks=8,
    threshold_pct=50,
    aggregation_function='SUM'
) %}

WITH periodic_metric AS (
    SELECT
        {{ date_column }} AS period,
        {{ aggregation_function }}({{ column_name }}) AS metric_value
    FROM {{ model }}
    WHERE {{ column_name }} IS NOT NULL
    GROUP BY {{ date_column }}
),

rolling_stats AS (
    SELECT
        period,
        metric_value,
        AVG(metric_value) OVER (
            ORDER BY period
            ROWS BETWEEN {{ lookback_weeks }} PRECEDING AND 1 PRECEDING
        ) AS rolling_mean,
        COUNT(*) OVER (
            ORDER BY period
            ROWS BETWEEN {{ lookback_weeks }} PRECEDING AND 1 PRECEDING
        ) AS rolling_window_size
    FROM periodic_metric
),

anomalies AS (
    SELECT
        period,
        metric_value,
        rolling_mean,
        rolling_window_size,
        CASE
            WHEN rolling_mean IS NULL OR rolling_mean = 0 THEN NULL
            ELSE ABS(metric_value - rolling_mean) / ABS(rolling_mean) * 100.0
        END AS deviation_pct
    FROM rolling_stats
    -- Require a minimum window of 2 periods before flagging anomalies
    WHERE rolling_window_size >= 2
)

SELECT
    '{{ column_name }}'::VARCHAR AS metric_name,
    period,
    ROUND(metric_value::FLOAT, 4) AS metric_value,
    ROUND(rolling_mean::FLOAT, 4) AS rolling_mean,
    ROUND(deviation_pct::FLOAT, 2) AS deviation_pct,
    {{ threshold_pct }}::FLOAT AS threshold_pct,
    rolling_window_size
FROM anomalies
WHERE deviation_pct > {{ threshold_pct }}

{% endtest %}
