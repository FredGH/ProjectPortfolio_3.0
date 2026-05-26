{{ config(unique_key='lnk_order_client_key') }}

-- Counterparty isolation: every order is linked to exactly one counterparty_id (non-nullable).
WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['order_id', 'counterparty_id']) }} AS lnk_order_client_key,
        {{ dbt_utils.generate_surrogate_key(['order_id']) }}        AS hub_order_key,
        {{ dbt_utils.generate_surrogate_key(['counterparty_id']) }} AS hub_client_key,
        order_id,
        counterparty_id,
        _loaded_at AS load_timestamp,
        'oms' AS record_source
    FROM {{ ref('stg_orders') }}
    WHERE counterparty_id IS NOT NULL
    {% if is_incremental() %}
    AND _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    lnk_order_client_key,
    hub_order_key,
    hub_client_key,
    order_id,
    counterparty_id,
    load_timestamp,
    record_source
FROM source
