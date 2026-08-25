-- mrt_customer_segments: customer segmentation via RFM-based clustering, labeled via CORTEX.COMPLETE.
-- Grain: customer_unique_id (unique). Includes predicted_ltv from segment-level monetisation scoring.
-- K-Means approximated in SQL using NTILE(5) on composite RFM score. In production, replace the
-- segment_raw CTE with a Snowpark ML KMeans call: snowflake.ml.modeling.cluster.KMeans(n_clusters=5).

{{ config(materialized='table', tags=['gold']) }}

WITH customer_base AS (
    SELECT
        customer_unique_id,
        customer_state,
        recency_days,
        frequency,
        monetary_value,
        rfm_score,
        recency_score,
        frequency_score,
        monetary_score,
        preferred_payment,
        product_diversity,
        acquisition_channel,
        churn_risk_score
    FROM {{ ref('slv_customer_profile') }}
),

-- Assign segment_id 1–5 via NTILE on composite rfm_score (1=lowest, 5=champions).
segment_raw AS (
    SELECT
        customer_unique_id,
        customer_state,
        recency_days,
        frequency,
        monetary_value,
        rfm_score,
        recency_score,
        frequency_score,
        monetary_score,
        preferred_payment,
        product_diversity,
        acquisition_channel,
        churn_risk_score,
        NTILE(5) OVER (ORDER BY rfm_score, monetary_value) AS segment_id
    FROM customer_base
),

-- Per-segment statistical profile used as CORTEX.COMPLETE prompt context.
segment_profiles AS (
    SELECT
        segment_id,
        COUNT(*)                          AS customer_count,
        ROUND(AVG(rfm_score), 2)          AS avg_rfm_score,
        ROUND(AVG(recency_days), 0)       AS avg_recency_days,
        ROUND(AVG(frequency), 2)          AS avg_frequency,
        ROUND(AVG(monetary_value), 2)     AS avg_monetary_value,
        ROUND(AVG(churn_risk_score), 2)   AS avg_churn_risk
    FROM segment_raw
    GROUP BY segment_id
),

-- One CORTEX.COMPLETE call per segment (5 total) to generate a human-readable label.
segment_labels AS (
    SELECT
        segment_id,
        customer_count,
        avg_rfm_score,
        avg_recency_days,
        avg_frequency,
        avg_monetary_value,
        avg_churn_risk,
        SNOWFLAKE.CORTEX.COMPLETE(
            'claude-haiku-4-5-20251001',
            CONCAT(
                'You are a marketing analyst. Given the following customer segment profile, ',
                'assign a concise 2-4 word marketing label ',
                '(e.g. "Champions", "At Risk", "Loyal Customers", "Hibernating", "New Customers"). ',
                'Respond with ONLY the label text, nothing else. ',
                'Profile — avg RFM score: ', avg_rfm_score::varchar,
                ', avg days since last order: ', avg_recency_days::varchar,
                ', avg orders placed: ', avg_frequency::varchar,
                ', avg total spend BRL: ', avg_monetary_value::varchar,
                ', avg churn risk (0-1): ', avg_churn_risk::varchar
            )
        ) AS segment_label_raw
    FROM segment_profiles
),

segment_labeled AS (
    SELECT
        segment_id,
        customer_count,
        avg_rfm_score,
        avg_recency_days,
        avg_frequency,
        avg_monetary_value,
        avg_churn_risk,
        TRIM(segment_label_raw) AS segment_label
    FROM segment_labels
),

-- Predicted LTV: cumulative spend × recency decay factor.
-- Customers inactive 180+ days discounted 50%; 90-179 days discounted 25%.
customer_ltv AS (
    SELECT
        customer_unique_id,
        segment_id,
        monetary_value * frequency
            * CASE
                WHEN recency_days > 180 THEN 0.5
                WHEN recency_days > 90  THEN 0.75
                ELSE 1.0
              END AS predicted_ltv
    FROM segment_raw
)

SELECT
    sr.customer_unique_id,
    sr.customer_state,
    sr.recency_days,
    sr.frequency,
    sr.monetary_value,
    sr.rfm_score,
    sr.recency_score,
    sr.frequency_score,
    sr.monetary_score,
    sr.preferred_payment,
    sr.product_diversity,
    sr.acquisition_channel,
    sr.churn_risk_score,
    sr.segment_id,
    sl.segment_label,
    sl.avg_rfm_score         AS segment_avg_rfm,
    sl.avg_recency_days      AS segment_avg_recency_days,
    sl.avg_frequency         AS segment_avg_frequency,
    sl.avg_monetary_value    AS segment_avg_monetary_value,
    sl.avg_churn_risk        AS segment_avg_churn_risk,
    sl.customer_count        AS segment_customer_count,
    ltv.predicted_ltv,
    CURRENT_TIMESTAMP()::timestamp_ntz AS _loaded_at
FROM segment_raw AS sr
INNER JOIN segment_labeled AS sl USING (segment_id)
INNER JOIN customer_ltv AS ltv USING (customer_unique_id)
