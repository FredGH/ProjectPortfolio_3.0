{{ config(materialized='table') }}

WITH latest_sat AS (
    SELECT DISTINCT ON (hub_instrument_key)
        hub_instrument_key,
        isin,
        name,
        instrument_class,
        currency,
        exchange,
        sector,
        country_of_risk,
        coupon_rate,
        maturity_date,
        expiry_date,
        contract_size,
        underlying_id,
        base_currency,
        quote_currency,
        tenor,
        load_timestamp
    FROM {{ ref('sat_instrument_ref') }}
    ORDER BY hub_instrument_key, load_timestamp DESC
)

SELECT
    h.hub_instrument_key,
    h.instrument_bk AS instrument_id,
    s.isin,
    s.name,
    s.instrument_class,
    s.currency,
    s.exchange,
    s.sector,
    s.country_of_risk,
    s.coupon_rate,
    s.maturity_date,
    s.expiry_date,
    s.contract_size,
    s.underlying_id,
    s.base_currency,
    s.quote_currency,
    s.tenor,
    s.load_timestamp
FROM {{ ref('hub_instrument') }} AS h
JOIN latest_sat AS s USING (hub_instrument_key)
