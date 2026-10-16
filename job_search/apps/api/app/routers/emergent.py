"""Emergent-role endpoints (PLAN.md Step 21b).

The detection pipeline runs from the CLI; these endpoints only read its results
and record the user's verdicts. Below the maturity threshold they refuse to
return a novelty index and label everything else a preview.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from core.db.session import get_current_user_id
from core.emergent import review, store
from core.emergent.config import load_config
from core.emergent.jobs import load_discovery_jobs
from core.emergent.maturity import assess

router = APIRouter()
PREVIEW_LABEL = "preview - history too short, not a finding"


class ReviewRequest(BaseModel):
    """Request body for a verdict.

    Attributes:
        verdict: `confirmed` or `dismissed`.
        rationale: Why (required).
    """

    verdict: Literal["confirmed", "dismissed"]
    rationale: str = Field(max_length=2000)


def _status(engine: Engine) -> dict[str, Any]:
    """Describe the corpus maturity and the latest run.

    Args:
        engine: The app-role engine.

    Returns:
        Maturity figures, the thresholds, and the latest run (or None).
    """
    config = load_config()
    jobs, agency = load_discovery_jobs(engine)
    maturity = assess(jobs, config)
    run = store.latest_run(engine)
    run_mature = run is None or run.mature
    shown = maturity.mature and run_mature
    return {
        "mature": maturity.mature,
        "run_mature": run_mature,
        "missing": maturity.note,
        "history_days": maturity.history_days,
        "periods": maturity.periods,
        "employers": maturity.employers,
        "agency_jobs_excluded": agency,
        "min_history_days": config.min_history_days,
        "min_periods": config.min_periods,
        "min_employers": config.min_employers,
        "novelty_shown": shown,
        "preview_label": None if shown else PREVIEW_LABEL,
        "run": (
            None
            if run is None
            else {
                "id": str(run.id),
                "created_at": run.created_at.isoformat(),
                "n_titles": run.n_titles,
                "n_clusters": run.n_clusters,
                "n_noise": run.n_noise,
            }
        ),
    }


def _cluster(c: store.StoredCluster, shown: bool) -> dict[str, Any]:
    """Build the API shape of a cluster, withholding novelty when immature.

    Args:
        c: The stored cluster.
        shown: Whether a novelty index may be shown.

    Returns:
        The cluster as a dict. `novelty` is None unless `shown`, whatever was
        stored.
    """
    return {
        "cluster_id": c.cluster_id,
        "titles": c.titles,
        "n_employers": c.n_employers,
        "it_share": c.it_share,
        "nearest_category": c.nearest_category,
        "nearest_cosine": c.nearest_cosine,
        "is_candidate": c.is_candidate,
        "candidate_note": c.candidate_note,
        "hybrid": c.hybrid,
        "novelty": c.novelty if shown else None,
        "novelty_note": (
            c.novelty_note
            if shown
            else "withheld: history too short for a novelty index"
        ),
    }


@router.get("/emergent/status")
def status(engine: Engine = Depends(get_app_db_engine)) -> dict[str, Any]:
    """Show corpus maturity and the latest cluster run.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The maturity status.
    """
    return _status(engine)


@router.get("/emergent/clusters")
def clusters(engine: Engine = Depends(get_app_db_engine)) -> dict[str, Any]:
    """List the latest run's clusters (a preview while the corpus is young).

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The status plus the clusters; no novelty index until mature.
    """
    state = _status(engine)
    run = store.latest_run(engine)
    rows = [] if run is None else store.list_clusters(engine, run.id)
    return {
        **state,
        "clusters": [_cluster(c, state["novelty_shown"]) for c in rows],
    }


@router.get("/emergent/candidates")
def candidates(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, Any]:
    """The user's review queue: named hypotheses awaiting a verdict.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The status plus the queue entries. Every candidate is
        `uncorroborated`; a dismissed cluster is suppressed for the configured
        period.
    """
    state = _status(engine)
    run = store.latest_run(engine)
    if run is None:
        return {**state, "candidates": []}
    queue = review.review_queue(
        engine, user_id, run.id, load_config(), datetime.now(UTC)
    )
    return {
        **state,
        "candidates": [
            {
                "id": str(e.candidate.id),
                "name": e.candidate.name,
                "characterisation": e.candidate.characterisation,
                "skill_signature": e.candidate.skill_signature,
                "corroboration": e.candidate.corroboration,
                "verdict": e.verdict,
                "rationale": e.rationale,
                "cluster": _cluster(e.cluster, state["novelty_shown"]),
            }
            for e in queue
        ],
    }


@router.post("/emergent/candidates/{candidate_id}/review")
def review_candidate(
    candidate_id: uuid.UUID,
    body: ReviewRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, str]:
    """Record a confirm / dismiss verdict with a rationale.

    Args:
        candidate_id: The candidate.
        body: The verdict and rationale.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The recorded verdict.

    Raises:
        HTTPException: 404 for an unknown candidate; 422 for a blank
            rationale. Confirming records the verdict only: nothing is
            promoted into the taxonomy.
    """
    if not store.candidate_exists(engine, candidate_id):
        raise HTTPException(status_code=404, detail="unknown candidate")
    try:
        review.record_review(
            engine, user_id, candidate_id, body.verdict, body.rationale
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"verdict": body.verdict}
