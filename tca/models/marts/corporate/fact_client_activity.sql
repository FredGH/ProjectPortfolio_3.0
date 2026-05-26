-- counterparty_id IS NOT NULL enforced — mandatory for CLIENT role row-level security.
WITH executions AS (
    SELECT
        counterparty_id,
        instrument_class,
        trade_date,
        venue_id,
        algo_id,
        side,
        quantity,
        filled_quantity,
        avg_fill_price,
        arrival_slippage_bps,
        market_impact_bps,
        commission_bps,
        total_cost_bps,
        execution_quality
    FROM {{ ref('fact_order_execution') }}
    WHERE counterparty_id IS NOT NULL
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['counterparty_id', 'trade_date::TEXT', 'instrument_class']) }} AS fact_id,
    counterparty_id,
    trade_date,
    instrument_class,
    COUNT(*)                                              AS order_count,
    SUM(quantity)                                         AS total_quantity,
    SUM(filled_quantity)                                  AS total_filled_quantity,
    ROUND(SUM(filled_quantity * avg_fill_price)::numeric, 2)       AS total_notional,
    ROUND(AVG(arrival_slippage_bps)::numeric, 4)                   AS avg_slippage_bps,
    ROUND(MIN(arrival_slippage_bps)::numeric, 4)                   AS best_slippage_bps,
    ROUND(MAX(arrival_slippage_bps)::numeric, 4)                   AS worst_slippage_bps,
    ROUND(AVG(market_impact_bps)::numeric, 4)                      AS avg_market_impact_bps,
    ROUND(AVG(commission_bps)::numeric, 4)                         AS avg_commission_bps,
    ROUND(AVG(total_cost_bps)::numeric, 4)                         AS avg_total_cost_bps,
    SUM(CASE WHEN side = 'BUY'  THEN 1 ELSE 0 END)        AS buy_order_count,
    SUM(CASE WHEN side = 'SELL' THEN 1 ELSE 0 END)        AS sell_order_count,
    SUM(CASE WHEN algo_id IS NOT NULL THEN 1 ELSE 0 END)  AS algo_order_count,
    SUM(CASE WHEN execution_quality = 'EXCELLENT' THEN 1 ELSE 0 END) AS excellent_count,
    SUM(CASE WHEN execution_quality = 'POOR' THEN 1 ELSE 0 END)      AS poor_count
FROM executions
GROUP BY counterparty_id, trade_date, instrument_class
