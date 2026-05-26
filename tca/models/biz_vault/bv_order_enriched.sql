{{ config(unique_key='hub_order_key') }}

WITH orders AS (
    SELECT DISTINCT ON (hub_order_key) *
    FROM {{ ref('sat_order_details') }}
    ORDER BY hub_order_key, load_timestamp DESC
),

fill_agg AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['order_id']) }}       AS hub_order_key,
        SUM(fill_quantity)                                         AS filled_quantity,
        COUNT(*)                                                   AS fill_count,
        SUM(fill_price * fill_quantity) / SUM(fill_quantity)      AS avg_fill_price,
        MIN(fill_time)                                             AS first_fill_time,
        MAX(fill_time)                                             AS last_fill_time,
        AVG(market_impact_bps)                                     AS avg_market_impact_bps,
        AVG(commission_bps)                                        AS avg_commission_bps
    FROM {{ ref('sat_fill_execution') }}
    {% if is_incremental() %}
    WHERE load_timestamp > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
    GROUP BY order_id
)

SELECT
    o.hub_order_key,
    o.instrument_id,
    o.instrument_class,
    o.side,
    o.order_type,
    o.quantity,
    o.arrival_price,
    o.limit_price,
    o.order_time,
    o.trade_date,
    o.counterparty_id,
    o.trader_id,
    o.algo_id,
    o.venue_id,
    o.currency,
    o.status,
    COALESCE(f.filled_quantity, 0)              AS filled_quantity,
    COALESCE(f.fill_count, 0)                   AS fill_count,
    f.avg_fill_price,
    f.first_fill_time,
    f.last_fill_time,
    f.avg_market_impact_bps,
    f.avg_commission_bps,
    CASE
        WHEN f.filled_quantity > 0
        THEN ROUND(f.filled_quantity::NUMERIC / o.quantity::NUMERIC * 100, 2)
        ELSE 0
    END AS fill_rate_pct,
    o.load_timestamp
FROM orders AS o
LEFT JOIN fill_agg AS f USING (hub_order_key)
{% if is_incremental() %}
WHERE o.load_timestamp > (SELECT MAX(load_timestamp) FROM {{ this }})
{% endif %}
