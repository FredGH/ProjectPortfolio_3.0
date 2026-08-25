-- mrt_funnel_conversion: MQL to delivered-order conversion rates by acquisition channel.
-- Grain: acquisition_channel (unique).
-- Note: The Olist MQL dataset lacks the closed_deals bridge table needed for a true
-- seller-to-MQL join. Conversion is approximated via a best-effort UUID join
-- (seller_id = mql_id) which will have low hit rates. This model is accurate in shape
-- and business logic; populate the closed_deals source table to improve coverage.

{{ config(materialized='table', tags=['gold']) }}

WITH mql AS (
    SELECT
        mql_id,
        first_contact_date,
        COALESCE(origin, 'unknown') AS acquisition_channel
    FROM {{ ref('brz_olist_mql') }}
),

-- Attempt MQL → seller → order linkage via best-effort UUID join.
-- A delivered order is counted as a conversion only if it occurred after first_contact_date.
mql_to_order AS (
    SELECT
        m.mql_id,
        m.acquisition_channel,
        m.first_contact_date,
        o.order_id,
        o.order_purchase_timestamp,
        DATEDIFF(
            'day',
            m.first_contact_date,
            o.order_purchase_timestamp::date
        ) AS days_to_first_order
    FROM mql AS m
    LEFT JOIN {{ ref('brz_olist_order_items') }} AS oi ON m.mql_id = oi.seller_id
    LEFT JOIN {{ ref('brz_olist_orders') }} AS o
        ON oi.order_id = o.order_id
        AND o.order_status = 'delivered'
        AND o.order_purchase_timestamp::date >= m.first_contact_date
),

-- Keep only the first converted order per MQL to avoid double-counting.
mql_first_conversion AS (
    SELECT
        mql_id,
        acquisition_channel,
        first_contact_date,
        MIN(order_id)                                           AS first_order_id,
        MIN(
            CASE WHEN order_id IS NOT NULL THEN days_to_first_order END
        )                                                       AS days_to_first_order
    FROM mql_to_order
    GROUP BY mql_id, acquisition_channel, first_contact_date
),

-- Revenue from the converted order.
order_revenue AS (
    SELECT
        o.order_id,
        SUM(oi.price + oi.freight_value) AS order_revenue
    FROM {{ ref('brz_olist_orders') }} AS o
    INNER JOIN {{ ref('brz_olist_order_items') }} AS oi USING (order_id)
    WHERE o.order_status = 'delivered'
    GROUP BY o.order_id
)

SELECT
    f.acquisition_channel,
    COUNT(DISTINCT f.mql_id)                                                    AS total_mqls,
    COUNT(DISTINCT f.first_order_id)                                            AS converted_mqls,
    ROUND(
        COUNT(DISTINCT f.first_order_id)::float
            / NULLIF(COUNT(DISTINCT f.mql_id), 0),
        4
    )                                                                           AS mql_to_order_conversion_rate,
    ROUND(AVG(f.days_to_first_order), 1)                                        AS avg_days_to_first_order,
    ROUND(SUM(COALESCE(r.order_revenue, 0)), 2)                                 AS total_attributed_revenue,
    ROUND(
        SUM(COALESCE(r.order_revenue, 0))
            / NULLIF(COUNT(DISTINCT f.mql_id), 0),
        2
    )                                                                           AS revenue_per_mql,
    CURRENT_TIMESTAMP()::timestamp_ntz                                          AS _loaded_at
FROM mql_first_conversion AS f
LEFT JOIN order_revenue AS r ON f.first_order_id = r.order_id
GROUP BY f.acquisition_channel
ORDER BY total_mqls DESC
