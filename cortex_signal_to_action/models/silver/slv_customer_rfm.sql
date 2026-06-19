-- slv_customer_rfm: RFM scores per unique customer using delivered orders only.
-- Grain: customer_unique_id (unique).

{{ config(materialized='table', tags=['silver']) }}

WITH delivered_orders AS (
    -- Delivered orders only; joins customer_unique_id from the customer dimension.
    SELECT
        c.customer_unique_id,
        c.customer_state,
        o.order_id,
        o.order_purchase_timestamp
    FROM {{ ref('brz_olist_customers') }} AS c
    INNER JOIN {{ ref('brz_olist_orders') }} AS o USING (customer_id)
    WHERE o.order_status = 'delivered'
),

order_revenue AS (
    -- Item-level revenue (price + freight) summed to order level.
    SELECT
        order_id,
        SUM(price + freight_value) AS order_value
    FROM {{ ref('brz_olist_order_items') }}
    GROUP BY order_id
),

order_payment_type AS (
    -- Dominant payment type per order (largest payment_value wins split-payment ties).
    SELECT
        order_id,
        payment_type,
        ROW_NUMBER() OVER (
            PARTITION BY order_id
            ORDER BY SUM(payment_value) DESC
        ) AS payment_rank
    FROM {{ ref('brz_olist_order_payments') }}
    GROUP BY order_id, payment_type
),

product_diversity AS (
    -- Count of distinct products purchased per unique customer.
    SELECT
        d.customer_unique_id,
        COUNT(DISTINCT oi.product_id) AS product_diversity
    FROM delivered_orders AS d
    INNER JOIN {{ ref('brz_olist_order_items') }} AS oi USING (order_id)
    GROUP BY d.customer_unique_id
),

preferred_payment AS (
    -- Most frequently used payment type per unique customer across all orders.
    SELECT
        d.customer_unique_id,
        opt.payment_type,
        ROW_NUMBER() OVER (
            PARTITION BY d.customer_unique_id
            ORDER BY COUNT(*) DESC
        ) AS pref_rank
    FROM delivered_orders AS d
    INNER JOIN order_payment_type AS opt
        ON d.order_id = opt.order_id AND opt.payment_rank = 1
    GROUP BY d.customer_unique_id, opt.payment_type
),

rfm_raw AS (
    -- Recency (days since last order), frequency (order count), monetary (total spend).
    SELECT
        d.customer_unique_id,
        MAX(d.customer_state)                                             AS customer_state,
        DATEDIFF('day', MAX(d.order_purchase_timestamp), CURRENT_DATE()) AS recency_days,
        COUNT(DISTINCT d.order_id)                                        AS frequency,
        SUM(COALESCE(r.order_value, 0))                                   AS monetary_value
    FROM delivered_orders AS d
    LEFT JOIN order_revenue AS r USING (order_id)
    GROUP BY d.customer_unique_id
),

rfm_scored AS (
    -- Apply NTILE(5) independently per dimension; lower recency_days = more recent = score 5.
    SELECT
        customer_unique_id,
        customer_state,
        recency_days,
        frequency,
        monetary_value,
        NTILE(5) OVER (ORDER BY recency_days ASC)    AS recency_score,
        NTILE(5) OVER (ORDER BY frequency DESC)       AS frequency_score,
        NTILE(5) OVER (ORDER BY monetary_value DESC)  AS monetary_score
    FROM rfm_raw
    WHERE monetary_value > 0
)

SELECT
    s.customer_unique_id,
    s.customer_state,
    s.recency_days,
    s.frequency,
    s.monetary_value,
    s.recency_score,
    s.frequency_score,
    s.monetary_score,
    ROUND((s.recency_score + s.frequency_score + s.monetary_score) / 3.0, 2) AS rfm_score,
    COALESCE(pp.payment_type, 'unknown')                                       AS preferred_payment,
    COALESCE(pd.product_diversity, 0)                                          AS product_diversity,
    CURRENT_TIMESTAMP()::timestamp_ntz                                         AS _loaded_at
FROM rfm_scored AS s
LEFT JOIN preferred_payment AS pp
    ON s.customer_unique_id = pp.customer_unique_id AND pp.pref_rank = 1
LEFT JOIN product_diversity AS pd USING (customer_unique_id)
