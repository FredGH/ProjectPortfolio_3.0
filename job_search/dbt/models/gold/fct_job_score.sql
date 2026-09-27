-- fct_job_score: one row per (user_id, job_group_id) — the shared job pool,
-- scored independently per user (PLAN.md Step 15). Thin pass-through over
-- scoring.job_score, enriched with dim_job's display fields so a caller
-- doesn't need a second join for every consumer.
-- Grain: (user_id, job_group_id) (unique).

SELECT
    s.user_id,
    s.job_group_id,
    j.title_for_display,
    j.company,
    s.hard_filter_passed,
    s.vector_similarity_score,
    s.reranker_score,
    s.skill_coverage_score,
    s.llm_fit_score,
    s.llm_rationale,
    s.llm_missing_skills,
    s.llm_stretch_flag,
    s.final_score,
    s.scored_at
FROM {{ source('scoring', 'job_score') }} AS s
INNER JOIN {{ ref('dim_job') }} AS j USING (job_group_id)
