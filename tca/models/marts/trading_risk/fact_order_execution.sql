-- Primary TCA fact table. counterparty_id is denormalized here and MUST be in
-- every mart query (enforced by tca_service.py).
WITH orders AS (
    SELECT DISTINCT ON (hub_order_key) *
    FROM {{ ref('bv_order_enriched') }}
    ORDER BY hub_order_key, load_timestamp DESC
),

costs AS (
    SELECT DISTINCT ON (hub_order_key) *
    FROM {{ ref('bv_tca_costs') }}
    ORDER BY hub_order_key, load_timestamp DESC
),

benchmarks AS (
    SELECT DISTINCT ON (hub_order_key) *
    FROM {{ ref('bv_peer_benchmark') }}
    ORDER BY hub_order_key, load_timestamp DESC
),

alpha AS (
    SELECT DISTINCT ON (hub_order_key) *
    FROM {{ ref('bv_alpha_decay') }}
    ORDER BY hub_order_key, load_timestamp DESC
),

mifid AS (
    SELECT DISTINCT ON (hub_order_key) *
    FROM {{ ref('bv_mifid_fields') }}
    ORDER BY hub_order_key, load_timestamp DESC
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['o.hub_order_key']) }} AS fact_id,
    o.hub_order_key,
    o.instrument_id,
    o.instrument_class,
    o.counterparty_id,
    o.trader_id,
    o.algo_id,
    o.venue_id,
    o.side,
    o.order_type,
    o.currency,
    o.quantity,
    o.filled_quantity,
    o.fill_count,
    o.fill_rate_pct,
    o.arrival_price,
    o.avg_fill_price,
    b.vwap_price,
    b.twap_price,
    b.close_price,
    b.edsp_price,
    -- Cost decomposition
    c.arrival_slippage_bps,
    c.market_impact_bps,
    c.commission_bps,
    c.timing_cost_bps,
    c.total_cost_bps,
    c.execution_quality,
    -- Benchmark slippage
    b.vwap_slippage_bps,
    b.twap_slippage_bps,
    b.close_slippage_bps,
    b.session_volume,
    b.intraday_vol_pct,
    -- Alpha decay
    a.alpha_t30m_bps,
    a.alpha_t1h_bps,
    a.alpha_t4h_bps,
    a.alpha_close_bps,
    a.vol_regime,
    -- MiFID II
    m.mifir_transaction_report_id,
    m.execution_venue_mic,
    m.pre_trade_waiver_type,
    m.post_trade_deferral_type,
    m.is_otc,
    m.si_flag,
    m.settlement_date,
    -- Timing
    o.order_time,
    o.first_fill_time,
    o.last_fill_time,
    o.trade_date,
    o.load_timestamp
FROM orders AS o
LEFT JOIN costs      AS c USING (hub_order_key)
LEFT JOIN benchmarks AS b USING (hub_order_key)
LEFT JOIN alpha      AS a USING (hub_order_key)
LEFT JOIN mifid      AS m USING (hub_order_key)
