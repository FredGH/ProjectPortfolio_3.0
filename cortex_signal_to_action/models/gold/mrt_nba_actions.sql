-- mrt_nba_actions: next-best-action recommendations per customer segment via CORTEX.COMPLETE.
-- Grain: segment_id (unique). One NBA recommendation generated per segment (5 total calls).

{{ config(materialized='table', tags=['gold', 'cortex']) }}

WITH segment_agg AS (
    SELECT
        segment_id,
        ANY_VALUE(segment_label)          AS segment_label,
        COUNT(*)                          AS customer_count,
        ROUND(AVG(rfm_score), 2)          AS avg_rfm_score,
        ROUND(AVG(churn_risk_score), 2)   AS avg_churn_risk,
        ROUND(AVG(predicted_ltv), 0)      AS avg_predicted_ltv,
        ROUND(AVG(monetary_value), 0)     AS avg_monetary_value,
        ROUND(AVG(recency_days), 0)       AS avg_recency_days,
        ROUND(AVG(frequency), 2)          AS avg_frequency
    FROM {{ ref('mrt_customer_segments') }}
    GROUP BY segment_id
),

-- Mode(preferred_payment) per segment using QUALIFY after GROUP BY.
top_payment AS (
    SELECT segment_id, preferred_payment AS top_payment_method
    FROM {{ ref('mrt_customer_segments') }}
    GROUP BY segment_id, preferred_payment
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY segment_id
        ORDER BY COUNT(*) DESC, preferred_payment
    ) = 1
),

-- Mode(acquisition_channel) per segment.
top_channel AS (
    SELECT segment_id, acquisition_channel AS top_acquisition_channel
    FROM {{ ref('mrt_customer_segments') }}
    GROUP BY segment_id, acquisition_channel
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY segment_id
        ORDER BY COUNT(*) DESC, acquisition_channel
    ) = 1
),

segment_summary AS (
    SELECT
        a.segment_id,
        a.segment_label,
        a.customer_count,
        a.avg_rfm_score,
        a.avg_churn_risk,
        a.avg_predicted_ltv,
        a.avg_monetary_value,
        a.avg_recency_days,
        a.avg_frequency,
        COALESCE(p.top_payment_method, 'unknown')    AS top_payment_method,
        COALESCE(c.top_acquisition_channel, 'unknown') AS top_acquisition_channel
    FROM segment_agg AS a
    LEFT JOIN top_payment AS p USING (segment_id)
    LEFT JOIN top_channel AS c USING (segment_id)
),

-- One CORTEX.COMPLETE call per segment to generate a structured NBA recommendation.
nba_raw AS (
    SELECT
        segment_id,
        segment_label,
        customer_count,
        avg_rfm_score,
        avg_churn_risk,
        avg_predicted_ltv,
        SNOWFLAKE.CORTEX.COMPLETE(
            'claude-haiku-4-5-20251001',
            CONCAT(
                'You are a marketing strategist. Generate a next-best-action plan for this customer segment. ',
                'Respond ONLY with valid JSON using exactly this structure: ',
                '{"action": "<one of: reactivation_campaign, loyalty_reward, upsell_offer, ',
                'win_back_discount, retention_alert, cross_sell_recommendation>", ',
                '"channel": "<one of: email, sms, push_notification, social_ads, direct_mail>", ',
                '"message_draft": "<50-80 word personalised message>", ',
                '"priority": "<one of: high, medium, low>", ',
                '"expected_uplift_pct": <integer 0-30>}. ',
                'Segment label: ', segment_label,
                '. Customers: ', customer_count::varchar,
                '. Avg days since last order: ', avg_recency_days::varchar,
                '. Avg orders: ', avg_frequency::varchar,
                '. Avg spend BRL: ', avg_monetary_value::varchar,
                '. Avg churn risk (0-1): ', avg_churn_risk::varchar,
                '. Top acquisition channel: ', top_acquisition_channel,
                '. Top payment method: ', top_payment_method
            )
        ) AS nba_json_raw
    FROM segment_summary
)

SELECT
    segment_id,
    segment_label,
    customer_count,
    avg_rfm_score,
    avg_churn_risk,
    avg_predicted_ltv,
    -- Parse structured NBA fields; fall back to safe defaults on malformed JSON.
    TRY_PARSE_JSON(nba_json_raw)                                        AS next_best_action_json,
    COALESCE(
        TRY_PARSE_JSON(nba_json_raw):action::varchar,
        'retention_alert'
    )                                                                   AS action,
    COALESCE(
        TRY_PARSE_JSON(nba_json_raw):channel::varchar,
        'email'
    )                                                                   AS channel,
    TRY_PARSE_JSON(nba_json_raw):message_draft::varchar                 AS message_draft,
    COALESCE(
        TRY_PARSE_JSON(nba_json_raw):priority::varchar,
        'medium'
    )                                                                   AS priority,
    TRY_CAST(
        TRY_PARSE_JSON(nba_json_raw):expected_uplift_pct AS float
    )                                                                   AS expected_uplift_pct,
    CURRENT_TIMESTAMP()::timestamp_ntz                                  AS _loaded_at
FROM nba_raw
ORDER BY segment_id
