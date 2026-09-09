-- assert_fct_market_demand_excludes_manual_entries: PLAN.md Step 11's
-- explicit "Done when" criterion. A test passes on zero rows returned
-- — this must never find a manual entry, even if a future edit
-- accidentally loosens the model's WHERE clause.

SELECT job_group_id
FROM {{ ref('fct_market_demand') }}
WHERE entry_method != 'api'
