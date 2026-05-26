{{ config(unique_key='attribution_key') }}

WITH costs AS (
    SELECT * FROM {{ ref('bv_tca_costs') }}
    {% if is_incremental() %}
    WHERE load_timestamp > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

traders AS (
    SELECT trader_id, name, desk, seniority FROM {{ source('stg_raw', 'traders') }}
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['c.trader_id', 'c.trade_date', 'c.instrument_class']) }} AS attribution_key,
    c.trader_id,
    t.name AS trader_name,
    t.desk,
    t.seniority,
    c.trade_date,
    c.instrument_class,
    COUNT(*)                                              AS order_count,
    SUM(c.quantity)                                       AS total_quantity,
    SUM(c.filled_quantity)                                AS total_filled_quantity,
    ROUND(AVG(c.arrival_slippage_bps), 4)                AS avg_slippage_bps,
    ROUND(AVG(c.market_impact_bps), 4)                   AS avg_market_impact_bps,
    ROUND(AVG(c.commission_bps), 4)                      AS avg_commission_bps,
    ROUND(AVG(c.total_cost_bps), 4)                      AS avg_total_cost_bps,
    ROUND(STDDEV(c.arrival_slippage_bps), 4)             AS slippage_stddev_bps,
    SUM(CASE WHEN c.arrival_slippage_bps < 0 THEN 1 ELSE 0 END) AS trades_with_price_improvement,
    SUM(CASE WHEN c.algo_id IS NOT NULL THEN 1 ELSE 0 END)       AS algo_order_count,
    MAX(c.load_timestamp)                                 AS load_timestamp
FROM costs AS c
LEFT JOIN traders AS t USING (trader_id)
GROUP BY
    c.trader_id, t.name, t.desk, t.seniority, c.trade_date, c.instrument_class
