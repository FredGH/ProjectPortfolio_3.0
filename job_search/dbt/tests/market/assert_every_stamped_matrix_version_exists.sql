-- Every period's matrix_version must be a version defined in the seed. Passes
-- on zero rows.

SELECT grain, period_start, matrix_version
FROM {{ ref('int_market_period_stamp') }}
WHERE matrix_version NOT IN (SELECT matrix_version FROM {{ ref('query_matrix_versions') }})
