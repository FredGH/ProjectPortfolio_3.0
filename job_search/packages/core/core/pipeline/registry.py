"""The pipeline stage catalog — the single source of truth for what
the pipeline consists of. `test_pipeline_registry.py`'s
`test_every_cli_subcommand_has_a_stages_entry_or_is_excluded` fails CI
if a new `apps/pipeline/app/cli.py` subcommand ships with no matching
entry here (or an entry on the documented exclusion list) — see
docs/superpowers/specs/2026-09-30-pipeline-dashboard-design.md's
README section for the requirement this enforces.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.pipeline import stage_functions as sf
from core.skills.review import count_unmapped


@dataclass(frozen=True)
class StageSpec:
    """One automated pipeline stage.

    Attributes:
        name: Matches the CLI subcommand name exactly.
        depends_on: Other automated stage names this one's staleness is
            computed against. Never a review-stage name — see
            REVIEW_STAGES' own depends_on docstring for why.
        per_user: Whether this stage's runs are scoped by user_id.
        run: The wrapper from `stage_functions` — takes a `params` dict,
            returns a result dict, raises on failure.
        has_run_button: False for `ingest`/`run-evals` — see this
            plan's Global Constraints.
    """

    name: str
    depends_on: tuple[str, ...]
    per_user: bool
    run: Callable[[dict], dict]
    has_run_button: bool = True


@dataclass(frozen=True)
class ReviewStageSpec:
    """One human-review pipeline stage.

    Attributes:
        name: Display name (not a CLI subcommand — these have none).
        depends_on: Automated stage names only. A review stage has no
            "last completed run" timestamp of its own (a human queue
            has no single completion event), so it can never appear on
            the right-hand side of another stage's `depends_on` for
            staleness purposes — `compute_staleness` (Task 7) walks
            through a review stage to the nearest upstream automated
            stage instead.
        page_path: The existing UI page this links to (Streamlit's own
            page-path convention, e.g. "Dedup_Review_Queue").
        pending_count: Runs directly against the engine — no HTTP call
            to the existing page's own API endpoints, since those often
            return the sample itself, not just a count. Takes engine and
            optional user_id (for RLS-scoped tables).
    """

    name: str
    depends_on: tuple[str, ...]
    page_path: str
    pending_count: Callable[[Engine, uuid.UUID | None], int]


def _count_dedup_pending(engine: Engine, user_id: uuid.UUID | None) -> int:
    """Mirrors GET /dedup/pairs-to-label's own two-mode WHERE clause
    (apps/api/app/routers/dedup.py:124), as a plain COUNT(*) instead of
    a stratified sample. Global stage — user_id unused."""
    with engine.connect() as conn:
        thresholds_row = conn.execute(
            text(
                "SELECT auto_match_threshold, auto_reject_threshold "
                "FROM dedup.calibration_thresholds ORDER BY calibrated_at DESC LIMIT 1"
            )
        ).one_or_none()
        if thresholds_row is None:
            return conn.execute(
                text(
                    "SELECT count(*) FROM dedup.dedup__similarity_scores AS s "
                    "LEFT JOIN dedup.pair_labels AS l "
                    "ON s.job_key_a = l.job_key_a AND s.job_key_b = l.job_key_b "
                    "WHERE l.job_key_a IS NULL AND s.hard_veto = false"
                )
            ).scalar_one()
        return conn.execute(
            text(
                "SELECT count(*) FROM dedup.dedup__similarity_scores AS s "
                "LEFT JOIN dedup.pair_labels AS l "
                "ON s.job_key_a = l.job_key_a AND s.job_key_b = l.job_key_b "
                "WHERE l.job_key_a IS NULL AND s.hard_veto = false "
                "AND s.blended_score > :auto_reject AND s.blended_score < :auto_match"
            ),
            {
                "auto_reject": thresholds_row.auto_reject_threshold,
                "auto_match": thresholds_row.auto_match_threshold,
            },
        ).scalar_one()


def _count_categorisation_pending(
    engine: Engine, user_id: uuid.UUID | None
) -> int:
    """Mirrors GET /classification/jobs-to-review's own
    total_unreviewed_count query (apps/api/app/routers/classification.py:225),
    unfiltered (no country_iso) and without the snippet-only-sources
    exclusion — a documented simplification: this count may run very
    slightly high relative to the review page's own filtered sample
    when snippet-only-source jobs are pending, which is acceptable for
    a dashboard status number, not the review queue itself. Global
    stage — user_id unused."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT count(*) FROM gold.dim_job AS d "
                "LEFT JOIN classification.category_review_labels AS r "
                "ON d.job_group_id = r.job_group_id "
                "WHERE r.job_group_id IS NULL AND d.category IS NOT NULL"
            )
        ).scalar_one()


def _count_skill_review_pending(engine: Engine, user_id: uuid.UUID | None) -> int:
    """Reuses core.skills.review.count_unmapped — the exact same
    function GET /skills/review/count itself calls — rather than a
    hand-duplicated query, so this can never drift from the real
    schema. Global stage — user_id unused."""
    with engine.connect() as conn:
        return count_unmapped(conn)


def _count_scoring_calibration_pending(
    engine: Engine, user_id: uuid.UUID | None
) -> int:
    """How many more labels the current user needs before 30. Must run
    through session_scope with the real user_id — scoring.job_label is
    RLS-scoped, so a bare engine.connect() silently returns 0 for every
    user rather than each user's own count."""
    with session_scope(engine, user_id=user_id) as conn:
        labeled = conn.execute(
            text("SELECT count(*) FROM scoring.job_label")
        ).scalar_one()
    return max(0, 30 - labeled)


def _extract_job_skills_not_via_run_stage(params: dict) -> dict:
    """Guard: extract-job-skills is driven by its own runner, never run_stage."""
    raise RuntimeError("extract-job-skills must be started via extraction_run")


STAGES: dict[str, StageSpec] = {
    "enrich-engagement-terms": StageSpec(
        name="enrich-engagement-terms", depends_on=(), per_user=False,
        run=sf.run_enrich_engagement_terms,
    ),
    "compute-blocking-keys": StageSpec(
        name="compute-blocking-keys", depends_on=("enrich-engagement-terms",),
        per_user=False, run=sf.run_compute_blocking_keys,
    ),
    "compute-similarity-features": StageSpec(
        name="compute-similarity-features", depends_on=("compute-blocking-keys",),
        per_user=False, run=sf.run_compute_similarity_features,
    ),
    "compute-title-similarity-scores": StageSpec(
        name="compute-title-similarity-scores",
        depends_on=("compute-similarity-features",), per_user=False,
        run=sf.run_compute_title_similarity_scores,
    ),
    "cluster-jobs": StageSpec(
        name="cluster-jobs", depends_on=("compute-title-similarity-scores",),
        per_user=False, run=sf.run_cluster_jobs,
    ),
    "compute-survivorship": StageSpec(
        name="compute-survivorship", depends_on=("cluster-jobs",), per_user=False,
        run=sf.run_compute_survivorship,
    ),
    "classify-jobs": StageSpec(
        name="classify-jobs", depends_on=("compute-survivorship",), per_user=False,
        run=sf.run_classify_jobs,
    ),
    "load-esco": StageSpec(
        name="load-esco", depends_on=(), per_user=False, run=sf.run_load_esco,
    ),
    "embed-esco": StageSpec(
        name="embed-esco", depends_on=("load-esco",), per_user=False,
        run=sf.run_embed_esco,
    ),
    "extract-job-skills": StageSpec(
        name="extract-job-skills", depends_on=(), per_user=False,
        # Never run via run_stage: POST /pipeline/stages/extract-job-skills/run
        # special-cases this stage and drives core.skills.extraction_run
        # (start_run + run_loop) directly. The callable below exists only
        # so the dependency graph and completeness test see the stage,
        # and raises so a mistaken run_stage call can't record a false
        # "completed".
        run=_extract_job_skills_not_via_run_stage,
    ),
    "map-skills": StageSpec(
        name="map-skills", depends_on=("embed-esco", "extract-job-skills"),
        per_user=False, run=sf.run_map_skills,
    ),
    "llm-map-skills": StageSpec(
        name="llm-map-skills", depends_on=("map-skills",), per_user=False,
        run=sf.run_llm_map_skills,
    ),
    "map-cv-skills": StageSpec(
        name="map-cv-skills", depends_on=("embed-esco",), per_user=True,
        run=sf.run_map_cv_skills,
    ),
    "score-filter-jobs": StageSpec(
        name="score-filter-jobs", depends_on=("classify-jobs",), per_user=True,
        run=sf.run_score_filter_jobs,
    ),
    "chunk-embed-jobs": StageSpec(
        name="chunk-embed-jobs", depends_on=("score-filter-jobs",), per_user=False,
        run=sf.run_chunk_embed_jobs,
    ),
    "chunk-embed-cv": StageSpec(
        name="chunk-embed-cv", depends_on=("map-cv-skills",), per_user=True,
        run=sf.run_chunk_embed_cv,
    ),
    "score-similarity": StageSpec(
        name="score-similarity", depends_on=("chunk-embed-jobs", "chunk-embed-cv"),
        per_user=True, run=sf.run_score_similarity,
    ),
    "score-skill-coverage": StageSpec(
        name="score-skill-coverage", depends_on=("map-cv-skills", "llm-map-skills"),
        per_user=True, run=sf.run_score_skill_coverage,
    ),
    "score-llm-rerank": StageSpec(
        name="score-llm-rerank", depends_on=("score-similarity", "score-skill-coverage"),
        per_user=True, run=sf.run_score_llm_rerank,
    ),
    "score-blend": StageSpec(
        name="score-blend", depends_on=("score-llm-rerank",), per_user=True,
        run=sf.run_score_blend,
    ),
}


REVIEW_STAGES: dict[str, ReviewStageSpec] = {
    "dedup-review": ReviewStageSpec(
        name="Dedup Review", depends_on=("cluster-jobs",),
        page_path="Dedup_Review_Queue", pending_count=_count_dedup_pending,
    ),
    "categorisation-review": ReviewStageSpec(
        name="Categorisation Review", depends_on=("classify-jobs",),
        page_path="Categorisation_Review", pending_count=_count_categorisation_pending,
    ),
    "skill-review": ReviewStageSpec(
        name="Skill Review", depends_on=("llm-map-skills",),
        page_path="Skill_Review", pending_count=_count_skill_review_pending,
    ),
    "scoring-calibration": ReviewStageSpec(
        name="Scoring Calibration", depends_on=("score-blend",),
        page_path="Scoring_Calibration", pending_count=_count_scoring_calibration_pending,
    ),
}
