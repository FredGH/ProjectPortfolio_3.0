{{ config(unique_key='lnk_order_entity_key') }}

-- Links order to PrivateBank legal entity via trader (orders booked on behalf of a legal entity).
WITH traders AS (
    SELECT trader_id, legal_entity FROM {{ source('stg_raw', 'traders') }}
),

source AS (
    SELECT
        o.order_id,
        t.legal_entity AS entity_id,
        o._loaded_at,
        {{ dbt_utils.generate_surrogate_key(['o.order_id', 't.legal_entity']) }} AS lnk_order_entity_key,
        {{ dbt_utils.generate_surrogate_key(['o.order_id']) }}      AS hub_order_key,
        {{ dbt_utils.generate_surrogate_key(['t.legal_entity']) }}  AS hub_legal_entity_key
    FROM {{ ref('stg_orders') }} AS o
    LEFT JOIN traders AS t USING (trader_id)
    WHERE t.legal_entity IS NOT NULL
    {% if is_incremental() %}
    AND o._loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    lnk_order_entity_key,
    hub_order_key,
    hub_legal_entity_key,
    order_id,
    entity_id,
    _loaded_at AS load_timestamp,
    'oms_ref_data' AS record_source
FROM source
