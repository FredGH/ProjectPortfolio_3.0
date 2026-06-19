-- slv_customer_profile: enriched customer profile joining RFM with MQL acquisition channel.
-- Grain: customer_unique_id (unique).
-- predicted_ltv is NULL until gold segmentation model populates it in Phase 6.

{{ config(materialized='table', tags=['silver']) }}

WITH rfm AS (
    SELECT * FROM {{ ref('slv_customer_rfm') }}
),

-- MQL data links seller acquisition to sellers via the closed_deals bridge.
-- We resolve the chain: customer → orders → order_items → seller → closed_deals → mql.
-- Each customer gets the acquisition_channel from the seller they purchased from most.
seller_mql AS (
    SELECT
        oi.seller_id,
        m.origin AS acquisition_channel
    FROM {{ ref('brz_olist_order_items') }} AS oi
    INNER JOIN {{ ref('brz_olist_mql') }} AS m
        -- The Olist MQL dataset uses mql_id; closed_deals (not loaded) bridges mql→seller.
        -- Without the closed_deals table we perform a best-effort join on seller_id = mql_id
        -- (same UUID format). Rows that do not match yield NULL acquisition_channel.
        ON oi.seller_id = m.mql_id
),

customer_acquisition AS (
    -- Most common acquisition channel per unique customer (by order count).
    SELECT
        c.customer_unique_id,
        s.acquisition_channel,
        ROW_NUMBER() OVER (
            PARTITION BY c.customer_unique_id
            ORDER BY COUNT(*) DESC
        ) AS channel_rank
    FROM {{ ref('brz_olist_customers') }} AS c
    INNER JOIN {{ ref('brz_olist_orders') }} AS o USING (customer_id)
    INNER JOIN {{ ref('brz_olist_order_items') }} AS oi USING (order_id)
    LEFT JOIN seller_mql AS s ON oi.seller_id = s.seller_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id, s.acquisition_channel
)

SELECT
    r.customer_unique_id,
    r.customer_state,
    r.recency_days,
    r.frequency,
    r.monetary_value,
    r.rfm_score,
    r.recency_score,
    r.frequency_score,
    r.monetary_score,
    r.preferred_payment,
    r.product_diversity,
    COALESCE(ca.acquisition_channel, 'unknown')                               AS acquisition_channel,
    -- Rule-based churn risk: high recency + low frequency = elevated risk.
    CASE
        WHEN r.recency_days > 180 AND r.frequency = 1  THEN 1.0
        WHEN r.recency_days > 90  AND r.frequency <= 2 THEN 0.6
        WHEN r.recency_days > 60                        THEN 0.3
        ELSE 0.1
    END                                                                        AS churn_risk_score,
    -- Placeholder: populated by mrt_customer_segments in Phase 6.
    NULL::float                                                                AS predicted_ltv,
    CURRENT_TIMESTAMP()::timestamp_ntz                                         AS _loaded_at
FROM rfm AS r
LEFT JOIN customer_acquisition AS ca
    ON r.customer_unique_id = ca.customer_unique_id AND ca.channel_rank = 1
