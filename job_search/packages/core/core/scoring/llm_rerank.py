"""Stage 4 of the scoring funnel: LLM re-rank of the top 50 (PLAN.md Step
15). Never sends more than 50 jobs per user per run — the plan's own
"Done when" criterion.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
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

# A MEAN of only the non-NULL components, not a SUM of COALESCE(x, 0) — a
# job with one strong component (e.g. skill_coverage_score=0.9 only) must
# rank above a job with all three components present but each mediocre
# (e.g. all at 0.2): SUM would rank the mediocre-but-complete job higher
# (0.6 > 0.9 is false, but 0.2+0.2+0.2=0.6 vs 0.9 — SUM favors more
# components over higher per-component fit). NULLIF guards the all-NULL
# case (no components at all) so that job's mean is NULL, not a
# division-by-zero error. Postgres's actual default for ORDER BY ... DESC
# is NULLS FIRST (nulls sort as "larger than any value"), the opposite of
# what "NULLs sort last" intuition suggests — verified against a live
# query, not assumed — so NULLS LAST is spelled out explicitly to put an
# all-NULL job at the bottom of the ranking, not the top.
_SELECT_PRE_LLM_TOP = text(
    "SELECT job_group_id, "
    "(COALESCE(vector_similarity_score, 0) + COALESCE(reranker_score, 0) "
    "+ COALESCE(skill_coverage_score, 0)) "
    "/ NULLIF("
    "(CASE WHEN vector_similarity_score IS NOT NULL THEN 1 ELSE 0 END) "
    "+ (CASE WHEN reranker_score IS NOT NULL THEN 1 ELSE 0 END) "
    "+ (CASE WHEN skill_coverage_score IS NOT NULL THEN 1 ELSE 0 END), "
    "0) AS pre_llm_score "
    "FROM scoring.job_score WHERE user_id = :user_id AND hard_filter_passed = true "
    "ORDER BY pre_llm_score DESC NULLS LAST LIMIT :top_n"
)
# Read straight from gold.dim_job.description rather than aggregating
# scoring.job_chunk_embedding: the chunk table has no ORDER BY-able
# sequencing across sections and chunks overlap by design, so a plain
# string_agg over it is scrambled/repeated text, not the job description.
_SELECT_JOB_DESCRIPTION = text(
    "SELECT description FROM gold.dim_job WHERE job_group_id = :job_group_id"
)
# ORDER BY section, chunk_index for stable, non-scrambled CV text — chunks
# overlap by design, so an unordered string_agg would interleave them
# unpredictably.
_SELECT_CV_TEXT = text(
    "SELECT string_agg(chunk_text, ' ' ORDER BY section, chunk_index) AS cv_text "
    "FROM scoring.cv_chunk_embedding WHERE user_id = :user_id"
)
_CLEAR_LLM_FIELDS = text(
    "UPDATE scoring.job_score SET llm_fit_score = NULL, llm_rationale = NULL, "
    "llm_missing_skills = NULL, llm_stretch_flag = NULL "
    "WHERE user_id = :user_id AND hard_filter_passed = true"
)
_UPDATE_LLM_FIELDS = text(
    "UPDATE scoring.job_score SET llm_fit_score = :fit_score, "
    "llm_rationale = :rationale, llm_missing_skills = :missing_skills, "
    "llm_stretch_flag = :stretch_flag "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)


@dataclass(frozen=True)
class RerankSummary:
    """The outcome of one `run_llm_rerank` call.

    Attributes:
        checked: How many top-N jobs had usable text and were actually
            sent to the LLM (whether or not the reply parsed).
        reranked: How many of those were successfully re-ranked (a
            parse/API failure is checked but not reranked).
    """

    checked: int
    reranked: int


def run_llm_rerank(
    app_engine: Engine,
    user_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    top_n: int = TOP_N,
    config_path: Path | None = None,
) -> RerankSummary:
    """Run the LLM re-rank stage for one user's top-scoring jobs.

    Every `hard_filter_passed = true` job for this user — the full
    candidate pool that could have made this run's top-N, not just the
    jobs that actually did — has its `llm_fit_score`, `llm_rationale`,
    `llm_missing_skills`, and `llm_stretch_flag` cleared to NULL before the
    top-N is even selected. Clearing only the current top-N (this run's
    `top_jobs`) cannot catch a job that drops OUT of the top-N this run
    (its `vector_similarity_score`/`reranker_score`/`skill_coverage_score`
    shifted since it was last LLM-scored) — by construction, a dropped-out
    job is absent from `top_jobs`, so a clear scoped to `top_jobs` would
    leave its stale LLM fields in place forever. Clearing the whole
    candidate pool up front — mirrors core.scoring.similarity's
    `_CLEAR_RERANK_SCORES` pattern, which clears `reranker_score` for
    `results.keys()` (every job considered this run) before setting the
    new value only for the current top-N subset — guarantees a dropped-out
    job ends up correctly NULL regardless of why it dropped out.

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
        A `RerankSummary` with how many top-N jobs were checked (had usable
        text and were actually sent to the LLM) and how many of those were
        successfully re-ranked (a parse/API failure is checked but skipped,
        not fatal to the rest — same retry-safe pattern as
        core.skills.llm_map). If the user has no CV text at all, this is a
        no-op run: `RerankSummary(checked=0, reranked=0)`, nothing cleared,
        no LLM call made.
    """
    template = load_prompt(TASK, load_task_config(TASK, config_path).prompt_family, 1)
    with session_scope(app_engine, user_id=user_id) as conn:
        cv_text = (
            conn.execute(_SELECT_CV_TEXT, {"user_id": user_id}).scalar_one_or_none()
            or ""
        )
    if not cv_text.strip():
        # No CV chunks at all for this user — there is nothing meaningful
        # to compare any job against. Return early rather than sending
        # empty CV text to the LLM for every top-N job and saving whatever
        # score comes back as if it were real; also leaves any existing
        # LLM fields untouched rather than clearing them for no reason.
        return RerankSummary(checked=0, reranked=0)

    with session_scope(app_engine, user_id=user_id) as conn:
        # Clear the FULL candidate pool's LLM fields first, before this
        # run's top-N is even selected — see the docstring above for why a
        # clear scoped to just `top_jobs` can never catch a job that drops
        # out of the top-N entirely.
        conn.execute(_CLEAR_LLM_FIELDS, {"user_id": user_id})
        top_jobs = conn.execute(
            _SELECT_PRE_LLM_TOP, {"user_id": user_id, "top_n": top_n}
        ).all()

    checked = 0
    reranked = 0
    for row in top_jobs:
        with app_engine.connect() as conn:
            jd_text = (
                conn.execute(
                    _SELECT_JOB_DESCRIPTION, {"job_group_id": row.job_group_id}
                ).scalar_one_or_none()
                or ""
            )
        if not jd_text.strip():
            # No usable job description yet — skip this job entirely
            # rather than sending empty text to the LLM and saving
            # whatever score comes back as if it were real. Its LLM
            # fields stay NULL (already cleared above) this run.
            continue
        checked += 1
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
    return RerankSummary(checked=checked, reranked=reranked)
