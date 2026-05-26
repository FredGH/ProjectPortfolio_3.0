{{ config(unique_key='hub_order_key') }}

-- MiFID II RTS 27/28 mandatory fields for transaction reporting.
-- RTS 27: venue execution quality statistics (published quarterly).
-- RTS 28: top 5 execution venues per instrument class (annual).
WITH orders AS (
    SELECT
        hub_order_key,
        instrument_id,
        instrument_class,
        side,
        order_type,
        quantity,
        arrival_price,
        avg_fill_price,
        filled_quantity,
        order_time,
        first_fill_time,
        last_fill_time,
        trade_date,
        counterparty_id,
        trader_id,
        venue_id,
        currency,
        load_timestamp
    FROM {{ ref('bv_order_enriched') }}
    {% if is_incremental() %}
    WHERE load_timestamp > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

clients AS (
    SELECT DISTINCT ON (hc.hub_client_key)
        hc.client_bk AS counterparty_id,
        s.lei
    FROM {{ ref('hub_client') }} AS hc
    JOIN {{ ref('sat_client_profile') }} AS s USING (hub_client_key)
    ORDER BY hc.hub_client_key, s.load_timestamp DESC
),

traders AS (
    SELECT DISTINCT ON (trader_id) trader_id, legal_entity
    FROM {{ source('stg_raw', 'traders') }}
    ORDER BY trader_id, _loaded_at DESC
),

entities AS (
    SELECT DISTINCT ON (entity_id) entity_id, mifid_lei
    FROM {{ source('stg_raw', 'legal_entities') }}
    ORDER BY entity_id, _loaded_at DESC
)

SELECT
    o.hub_order_key,
    o.instrument_id,
    o.instrument_class,
    o.side,
    o.order_type,
    o.quantity,
    o.filled_quantity,
    o.arrival_price,
    o.avg_fill_price,
    o.trade_date,
    o.order_time                              AS transaction_time,
    o.counterparty_id,
    c.lei                                     AS counterparty_lei,
    t.legal_entity                            AS booking_entity,
    e.mifid_lei                               AS executing_entity_lei,
    o.venue_id                                AS execution_venue_mic,
    -- RTS 27 waiver classification (simplified PoC logic)
    CASE
        WHEN o.order_type IN ('VWAP', 'TWAP') THEN 'RFPT'  -- Reference price transaction
        WHEN o.instrument_class IN ('fixed_income', 'fx_derivative') THEN 'ILQD'  -- Illiquid instrument
        ELSE NULL
    END AS pre_trade_waiver_type,
    -- Post-trade deferral (for large trades)
    CASE
        WHEN o.filled_quantity * COALESCE(o.avg_fill_price, o.arrival_price) > 50_000_000 THEN 'LRGS'
        ELSE NULL
    END AS post_trade_deferral_type,
    -- OTC / SI flag
    CASE
        WHEN o.venue_id IN ('BLTX', 'MFTR', 'TRAX', 'GLMX', 'FXALL') THEN TRUE
        ELSE FALSE
    END AS is_otc,
    CASE
        WHEN o.venue_id IN ('BLTX', 'MFTR', 'TRAX', 'GLMX', 'FXALL') THEN TRUE
        ELSE FALSE
    END AS si_flag,
    -- Settlement date (T+2 for equities, T+3 for bonds)
    CASE
        WHEN o.instrument_class IN ('equity', 'equity_future') THEN o.trade_date + INTERVAL '2 days'
        WHEN o.instrument_class = 'fixed_income' THEN o.trade_date + INTERVAL '3 days'
        ELSE o.trade_date + INTERVAL '2 days'
    END AS settlement_date,
    o.currency,
    -- MiFIR transaction report ID: deterministic UUID from order_id
    {{ dbt_utils.generate_surrogate_key(['o.hub_order_key', "'mifir'"]) }} AS mifir_transaction_report_id,
    o.load_timestamp
FROM orders AS o
LEFT JOIN clients AS c ON c.counterparty_id = o.counterparty_id
LEFT JOIN traders AS t ON t.trader_id = o.trader_id
LEFT JOIN entities AS e ON e.entity_id = t.legal_entity
