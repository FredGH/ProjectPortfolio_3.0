{{ config(unique_key='sat_instrument_ref_id') }}

WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['instrument_id']) }} AS hub_instrument_key,
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
        _loaded_at AS load_timestamp,
        {{ dbt_utils.generate_surrogate_key([
            'instrument_id', 'name', 'instrument_class', 'currency'
        ]) }} AS hash_diff
    FROM {{ ref('stg_instruments') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY hub_instrument_key ORDER BY load_timestamp DESC) AS rn
    FROM source
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['hub_instrument_key', 'load_timestamp']) }} AS sat_instrument_ref_id,
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
    load_timestamp,
    hash_diff
FROM ranked
WHERE rn = 1
