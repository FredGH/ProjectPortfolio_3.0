{{ config(materialized='table') }}

SELECT DISTINCT ON (hub_order_key)
    hub_order_key,
    mifir_transaction_report_id,
    execution_venue_mic,
    pre_trade_waiver_type,
    post_trade_deferral_type,
    is_otc,
    si_flag,
    settlement_date,
    counterparty_lei,
    executing_entity_lei,
    booking_entity,
    load_timestamp
FROM {{ ref('bv_mifid_fields') }}
ORDER BY hub_order_key, load_timestamp DESC
