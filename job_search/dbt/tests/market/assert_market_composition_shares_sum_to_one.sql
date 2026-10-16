-- Each family of shares (work model, contract type, seniority) sums to 1.
-- Passes on zero rows.

SELECT *
FROM {{ ref('fct_market_composition') }}
WHERE ABS(share_remote + share_hybrid + share_onsite + share_work_model_unknown - 1) > 0.0001
    OR ABS(share_permanent + share_contract + share_contract_type_unknown - 1) > 0.0001
    OR ABS(share_junior + share_mid + share_senior + share_lead + share_principal
        + share_seniority_unknown - 1) > 0.0001
