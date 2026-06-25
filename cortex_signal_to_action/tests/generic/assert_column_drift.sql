-- assert_column_drift: Tier 4 statistical drift detection.
-- Computes the z-score of the column's current mean against a stored baseline.
-- Fails (returns rows) when the z-score exceeds z_threshold, indicating the column
-- distribution has drifted significantly from the established baseline.
--
-- Parameters:
--   baseline_mean   — expected mean from the baseline run
--   baseline_stddev — standard deviation from the baseline run
--   z_threshold     — number of standard deviations beyond which drift is declared (default 3.0)
--
-- Usage in schema.yml:
--   tests:
--     - assert_column_drift:
--         baseline_mean: 0.05
--         baseline_stddev: 0.45
--         z_threshold: 3.0
--         severity: warn

{% test assert_column_drift(model, column_name, baseline_mean, baseline_stddev, z_threshold=3.0) %}

WITH column_stats AS (
    SELECT
        AVG({{ column_name }}) AS current_mean,
        COUNT({{ column_name }}) AS non_null_count
    FROM {{ model }}
),

drift AS (
    SELECT
        current_mean,
        non_null_count,
        CASE
            WHEN {{ baseline_stddev }} = 0 THEN NULL
            ELSE ABS(current_mean - {{ baseline_mean }}) / {{ baseline_stddev }}
        END AS z_score
    FROM column_stats
)

SELECT
    '{{ column_name }}'::VARCHAR AS column_name,
    ROUND(current_mean::FLOAT, 6) AS current_mean,
    {{ baseline_mean }}::FLOAT AS baseline_mean,
    {{ baseline_stddev }}::FLOAT AS baseline_stddev,
    ROUND(z_score, 4) AS z_score,
    {{ z_threshold }}::FLOAT AS z_threshold,
    non_null_count
FROM drift
WHERE
    -- Drift detected: z-score exceeds threshold
    z_score > {{ z_threshold }}
    -- Edge case: stddev = 0 but mean shifted (infinite z-score)
    OR ({{ baseline_stddev }} = 0 AND ABS(current_mean - {{ baseline_mean }}) > 0)

{% endtest %}
