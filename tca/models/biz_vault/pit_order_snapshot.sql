{{ config(unique_key='snapshot_id', materialized='table') }}

-- Point-in-time snapshot: one row per order with latest satellite data.
-- Rebuilt as table daily (not incremental) to reflect any late-arriving updates.
WITH orders AS (
    SELECT
        hub_order_key,
        instrument_id,
        instrument_class,
        side,
        order_type,
        quantity,
        arrival_price,
        order_time,
        trade_date,
        counterparty_id,
        trader_id,
        algo_id,
        venue_id,
        currency,
        status,
        load_timestamp
    FROM {{ ref('sat_order_details') }}
),

tca AS (
    SELECT
        hub_order_key,
        arrival_slippage_bps,
        market_impact_bps,
        commission_bps,
        timing_cost_bps,
        total_cost_bps,
        execution_quality,
        avg_fill_price,
        filled_quantity
    FROM {{ ref('bv_tca_costs') }}
),

alpha AS (
    SELECT
        hub_order_key,
        alpha_t30m_bps,
        alpha_t1h_bps,
        alpha_t4h_bps,
        alpha_close_bps,
        vol_regime
    FROM {{ ref('bv_alpha_decay') }}
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['o.hub_order_key', "CURRENT_DATE::TEXT"]) }} AS snapshot_id,
    o.hub_order_key,
    o.instrument_id,
    o.instrument_class,
    o.side,
    o.order_type,
    o.quantity,
    o.arrival_price,
    o.order_time,
    o.trade_date,
    o.counterparty_id,
    o.trader_id,
    o.algo_id,
    o.venue_id,
    o.currency,
    o.status,
    t.arrival_slippage_bps,
    t.market_impact_bps,
    t.commission_bps,
    t.timing_cost_bps,
    t.total_cost_bps,
    t.execution_quality,
    t.avg_fill_price,
    t.filled_quantity,
    a.alpha_t30m_bps,
    a.alpha_t4h_bps,
    a.alpha_close_bps,
    a.vol_regime,
    CURRENT_TIMESTAMP AS snapshot_at
FROM orders AS o
LEFT JOIN tca   AS t USING (hub_order_key)
LEFT JOIN alpha AS a USING (hub_order_key)
