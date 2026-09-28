-- fct_job_score: one row per (user_id, job_group_id) — the shared job pool,
-- scored independently per user (PLAN.md Step 15). Thin pass-through over
-- scoring.job_score, enriched with dim_job's display fields so a caller
-- doesn't need a second join for every consumer.
-- Grain: (user_id, job_group_id) (unique).
--
-- This is the first per-user model in `gold`. `job_search_app` gets
-- automatic SELECT on every gold table via migration 0014's
-- `ALTER DEFAULT PRIVILEGES ... IN SCHEMA gold`, which would otherwise let
-- any app-role query return every user's scores/rationales — a
-- tenant-isolation gap. The `table` materialization drops and recreates
-- this relation on every run, so RLS cannot be set up once in a migration;
-- it must be re-applied with a `post_hook` after every build, matching
-- scoring's own per-table `_rls()` helper (0028) for policy naming and the
-- `app.current_user_id` GUC.

{{ config(
    post_hook=[
        "ALTER TABLE {{ this }} ENABLE ROW LEVEL SECURITY",
        "CREATE POLICY fct_job_score_isolation ON {{ this }} "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)",
        "GRANT SELECT ON {{ this }} TO job_search_app"
    ]
) }}

SELECT
    s.user_id,
    s.job_group_id,
    j.title_for_display,
    j.company,
    s.hard_filter_passed,
    s.vector_similarity_score,
    s.embedding_model,
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
