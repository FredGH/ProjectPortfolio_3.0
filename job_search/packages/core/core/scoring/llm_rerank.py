"""Stage 4 of the scoring funnel: LLM re-rank of the top 50 (PLAN.md Step
15). Never sends more than 50 jobs per user per run — the plan's own
"Done when" criterion.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.llm import gateway
from core.llm.json_response import parse_json_response
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter

TASK = "job_scoring"
PROMPT_VERSION = "claude.v1"
TOP_N = 50

_SELECT_PRE_LLM_TOP = text(
    "SELECT job_group_id, "
    "COALESCE(vector_similarity_score, 0) + COALESCE(reranker_score, 0) "
    "+ COALESCE(skill_coverage_score, 0) AS pre_llm_score "
    "FROM scoring.job_score WHERE user_id = :user_id AND hard_filter_passed = true "
    "ORDER BY pre_llm_score DESC LIMIT :top_n"
)
_SELECT_JOB_TEXT = text(
    "SELECT string_agg(chunk_text, ' ') AS jd_text FROM scoring.job_chunk_embedding "
    "WHERE job_group_id = :job_group_id"
)
_SELECT_CV_TEXT = text(
    "SELECT string_agg(chunk_text, ' ') AS cv_text FROM scoring.cv_chunk_embedding "
    "WHERE user_id = :user_id"
)
_CLEAR_LLM_FIELDS = text(
    "UPDATE scoring.job_score SET llm_fit_score = NULL, llm_rationale = NULL, "
    "llm_missing_skills = NULL, llm_stretch_flag = NULL "
    "WHERE user_id = :user_id AND job_group_id = ANY(:job_group_ids)"
)
_UPDATE_LLM_FIELDS = text(
    "UPDATE scoring.job_score SET llm_fit_score = :fit_score, "
    "llm_rationale = :rationale, llm_missing_skills = :missing_skills, "
    "llm_stretch_flag = :stretch_flag "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)


def run_llm_rerank(
    app_engine: Engine,
    user_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    top_n: int = TOP_N,
    config_path: Path | None = None,
) -> int:
    """Run the LLM re-rank stage for one user's top-scoring jobs.

    Every job in this run's top-N pool has its `llm_fit_score`,
    `llm_rationale`, `llm_missing_skills`, and `llm_stretch_flag` cleared to
    NULL before the per-job loop runs, so a job that was LLM-scored on a
    previous run but is reconsidered-and-fails (a bad/malformed reply) or
    reconsidered-and-drops-out-of-the-ranking this run never keeps a stale
    non-NULL value from an earlier run. A job outside this run's top-N pool
    entirely (never reconsidered) keeps whatever it had — the pre-LLM
    ranking always re-evaluates who is "in" the top-N fresh each run, so
    only actually-reconsidered jobs need clearing.

    Args:
        app_engine: The app-role engine (RLS-enforced).
        user_id: Whose top jobs to re-rank.
        adapters: LLM adapters keyed by provider; must include the task's
            provider ("anthropic").
        top_n: Send at most this many jobs. Defaults to `TOP_N` (50, the
            plan's own cap); tests pass a smaller value so a fixture with a
            handful of jobs can prove the cap is respected without inserting
            50+ rows.
        config_path: Task-config override (tests).

    Returns:
        The number of jobs successfully re-ranked (a parse/API failure on
        one job is skipped, not fatal to the rest — same retry-safe pattern
        as core.skills.llm_map).
    """
    template = load_prompt(TASK, load_task_config(TASK, config_path).prompt_family, 1)
    with session_scope(app_engine, user_id=user_id) as conn:
        top_jobs = conn.execute(
            _SELECT_PRE_LLM_TOP, {"user_id": user_id, "top_n": top_n}
        ).all()
        cv_text = (
            conn.execute(_SELECT_CV_TEXT, {"user_id": user_id}).scalar_one_or_none()
            or ""
        )

    if top_jobs:
        with session_scope(app_engine, user_id=user_id) as conn:
            # Clear every reconsidered job's LLM fields up front so a job
            # that fails or drops out this run never keeps a stale value —
            # same pattern as core.scoring.similarity._CLEAR_RERANK_SCORES.
            conn.execute(
                _CLEAR_LLM_FIELDS,
                {
                    "user_id": user_id,
                    "job_group_ids": [row.job_group_id for row in top_jobs],
                },
            )

    reranked = 0
    for row in top_jobs:
        with app_engine.connect() as conn:
            jd_text = (
                conn.execute(
                    _SELECT_JOB_TEXT, {"job_group_id": row.job_group_id}
                ).scalar_one_or_none()
                or ""
            )
        prompt = template.format(cv_text=cv_text, jd_text=jd_text)
        try:
            response = gateway.complete(
                TASK,
                prompt,
                prompt_version=PROMPT_VERSION,
                adapters=adapters,
                config_path=config_path,
            )
            data = parse_json_response(response.text.strip())
            fit_score = int(data["fit_score"])
            missing_skills = list(data["missing_skills"])
            stretch_flag = bool(data["stretch_flag"])
            rationale = str(data["rationale"])[:500]
        except Exception:  # noqa: BLE001 — a bad reply skips this job, not the run
            continue
        with session_scope(app_engine, user_id=user_id) as conn:
            conn.execute(
                _UPDATE_LLM_FIELDS,
                {
                    "user_id": user_id,
                    "job_group_id": row.job_group_id,
                    "fit_score": fit_score,
                    "rationale": rationale,
                    "missing_skills": missing_skills,
                    "stretch_flag": stretch_flag,
                },
            )
        reranked += 1
    return reranked
