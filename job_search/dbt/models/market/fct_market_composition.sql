-- fct_market_composition: work-model, contract-vs-permanent and seniority mix
-- as shares, by category x region x month (PLAN.md Step 21a). Grain:
-- (period_start, category, region). Each family of shares sums to 1.
-- "unknown" is a real share, not hidden: the keyword work-model rules leave
-- many postings unknown and the reader should see how many.

SELECT
    j.first_seen_month AS period_start,
    j.category,
    j.region,
    COUNT(*) AS n_postings,
    COUNT(*) FILTER (WHERE j.work_model = 'remote')::numeric / COUNT(*) AS share_remote,
    COUNT(*) FILTER (WHERE j.work_model = 'hybrid')::numeric / COUNT(*) AS share_hybrid,
    COUNT(*) FILTER (WHERE j.work_model = 'onsite')::numeric / COUNT(*) AS share_onsite,
    COUNT(*) FILTER (WHERE j.work_model = 'unknown')::numeric / COUNT(*) AS share_work_model_unknown,
    COUNT(*) FILTER (WHERE j.contract_type = 'permanent')::numeric / COUNT(*) AS share_permanent,
    COUNT(*) FILTER (WHERE j.contract_type = 'contract')::numeric / COUNT(*) AS share_contract,
    COUNT(*) FILTER (WHERE j.contract_type = 'unknown')::numeric / COUNT(*) AS share_contract_type_unknown,
    COUNT(*) FILTER (WHERE j.seniority_band = 'junior')::numeric / COUNT(*) AS share_junior,
    COUNT(*) FILTER (WHERE j.seniority_band = 'mid')::numeric / COUNT(*) AS share_mid,
    COUNT(*) FILTER (WHERE j.seniority_band = 'senior')::numeric / COUNT(*) AS share_senior,
    COUNT(*) FILTER (WHERE j.seniority_band = 'lead')::numeric / COUNT(*) AS share_lead,
    COUNT(*) FILTER (WHERE j.seniority_band = 'principal')::numeric / COUNT(*) AS share_principal,
    COUNT(*) FILTER (WHERE j.seniority_band NOT IN ('junior', 'mid', 'senior', 'lead', 'principal'))::numeric / COUNT(*) AS share_seniority_unknown,
    st.panel_complete,
    st.panel_sources,
    st.matrix_version,
    st.trend_break
FROM {{ ref('int_market_job') }} AS j
INNER JOIN {{ ref('int_market_period_stamp') }} AS st
    ON st.grain = 'month' AND st.period_start = j.first_seen_month
GROUP BY
    j.first_seen_month, j.category, j.region,
    st.panel_complete, st.panel_sources, st.matrix_version, st.trend_break
