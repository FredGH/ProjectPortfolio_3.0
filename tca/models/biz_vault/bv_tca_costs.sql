{{ config(unique_key='hub_order_key') }}

-- TCA cost decomposition: arrival slippage, market impact, commission, timing cost.
-- Convention: positive bps = cost (adverse for the client).
WITH enriched AS (
    SELECT * FROM {{ ref('bv_order_enriched') }}
    {% if is_incremental() %}
    WHERE load_timestamp > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

costs AS (
    SELECT
        hub_order_key,
        instrument_class,
        counterparty_id,
        trader_id,
        algo_id,
        venue_id,
        trade_date,
        side,
        order_type,
        quantity,
        filled_quantity,
        arrival_price,
        avg_fill_price,
        -- Arrival slippage: positive = client paid more / received less than arrival mid
        CASE
            WHEN side = 'BUY'
            THEN ROUND(((avg_fill_price - arrival_price) / NULLIF(arrival_price, 0) * 10000)::numeric, 4)
            ELSE ROUND(((arrival_price - avg_fill_price) / NULLIF(arrival_price, 0) * 10000)::numeric, 4)
        END AS arrival_slippage_bps,
        -- Market impact from execution (weighted avg of fill-level impact)
        ROUND(COALESCE(avg_market_impact_bps, 0)::numeric, 4) AS market_impact_bps,
        -- Commission cost
        ROUND(COALESCE(avg_commission_bps, 0)::numeric, 4) AS commission_bps,
        -- Timing cost: spread decay from order_time to last_fill_time (approx)
        ROUND(
            (ABS(EXTRACT(EPOCH FROM (last_fill_time - order_time)) / 3600.0)
            * 0.5)::numeric,  -- 0.5 bps per hour of delay (PoC approximation)
            4
        ) AS timing_cost_bps,
        load_timestamp
    FROM enriched
    WHERE avg_fill_price IS NOT NULL
)

SELECT
    hub_order_key,
    instrument_class,
    counterparty_id,
    trader_id,
    algo_id,
    venue_id,
    trade_date,
    side,
    order_type,
    quantity,
    filled_quantity,
    arrival_price,
    avg_fill_price,
    arrival_slippage_bps,
    market_impact_bps,
    commission_bps,
    timing_cost_bps,
    ROUND(
        (arrival_slippage_bps + market_impact_bps + commission_bps + timing_cost_bps)::numeric,
        4
    ) AS total_cost_bps,
    CASE
        WHEN arrival_slippage_bps < 5  THEN 'EXCELLENT'
        WHEN arrival_slippage_bps < 20 THEN 'GOOD'
        WHEN arrival_slippage_bps < 50 THEN 'FAIR'
        ELSE 'POOR'
    END AS execution_quality,
    load_timestamp
FROM costs
