-- Contract figures are GBP per day, permanent figures GBP per year, never mixed;
-- a figure is positive. Passes on zero rows.

SELECT job_group_id
FROM {{ ref('offer_benchmark_pool') }}
WHERE (contract_type = 'permanent' AND unit <> 'GBP per year')
    OR (contract_type = 'contract' AND unit <> 'GBP per day')
    OR (figure IS NOT NULL AND figure <= 0)
    OR (contract_type = 'contract' AND figure > 5000)   -- an annual figure in a day-rate column
    OR (contract_type = 'permanent' AND figure IS NOT NULL AND figure < 5000)
