"""The review queue (PLAN.md Step 21b): human judgment is the validation layer.

The model will confidently name clusters that are artefacts of the collection.
Nothing is a finding until a person confirms it with a rationale. A dismissal
suppresses re-prompting for a configured period, matched across cluster runs by
overlapping titles (cluster ids change every run).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.emergent.config import EmergentConfig
from core.emergent.store import (
    StoredCandidate,
    StoredCluster,
    list_candidates,
    list_clusters,
)

VERDICTS = ("confirmed", "dismissed")
_SAME_CLUSTER_OVERLAP = 0.5


@dataclass(frozen=True)
class QueueEntry:
    """A candidate with its cluster and the user's verdict.

    Attributes:
        candidate: The named hypothesis.
        cluster: Its cluster (employer breadth, hybridity, novelty).
        verdict: `pending`, `confirmed` or `dismissed`.
        rationale: The user's rationale for a verdict.
    """

    candidate: StoredCandidate
    cluster: StoredCluster
    verdict: str
    rationale: str | None


def record_review(
    engine: Engine,
    user_id: uuid.UUID,
    candidate_id: uuid.UUID,
    verdict: str,
    rationale: str,
) -> None:
    """Record a verdict.

    Args:
        engine: The app-role engine.
        user_id: The reviewer.
        candidate_id: The candidate.
        verdict: `confirmed` or `dismissed`.
        rationale: Why (required: an unexplained verdict is not a judgment).

    Raises:
        ValueError: If the verdict is unknown or the rationale is blank.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    if not rationale.strip():
        raise ValueError("a rationale is required")
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "INSERT INTO emergent.emergent_role_review (user_id, candidate_id, "
                "verdict, rationale) VALUES (:u, :c, :v, :r)"
            ),
            {"u": user_id, "c": candidate_id, "v": verdict, "r": rationale.strip()},
        )


def _latest_reviews(engine: Engine, user_id: uuid.UUID) -> list:
    """Read the user's reviews with the titles of the clusters reviewed.

    Args:
        engine: The app-role engine.
        user_id: The reviewer.

    Returns:
        Rows of `(candidate_id, verdict, rationale, decided_at, titles)`, newest
        first.
    """
    with session_scope(engine, user_id=user_id) as conn:
        return list(
            conn.execute(
                text(
                    "SELECT r.candidate_id, r.verdict, r.rationale, r.decided_at, "
                    "c.canonical_titles AS titles FROM emergent.emergent_role_review r "
                    "JOIN emergent.role_candidate rc ON rc.id = r.candidate_id "
                    "JOIN emergent.title_cluster c ON c.run_id = rc.run_id "
                    "AND c.cluster_id = rc.cluster_id ORDER BY r.decided_at DESC"
                )
            ).all()
        )


def _overlap(a: list[str], b: list[str]) -> float:
    """Jaccard overlap of two title lists.

    Args:
        a: The first list.
        b: The second list.

    Returns:
        |a and b| / |a or b|, or 0 for two empty lists.
    """
    union = set(a) | set(b)
    return len(set(a) & set(b)) / len(union) if union else 0.0


def review_queue(
    engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    config: EmergentConfig,
    now: datetime,
) -> list[QueueEntry]:
    """Build the user's review queue for a run.

    Args:
        engine: The app-role engine.
        user_id: The reviewer.
        run_id: The cluster run.
        config: The emergent config (suppression period).
        now: The reference time.

    Returns:
        Named candidates with the user's verdict. A candidate whose cluster
        overlaps one the user dismissed within `dismiss_suppress_days` is left
        out (suppressed); a dismissal older than that comes back for a fresh
        look.
    """
    clusters = {
        c.cluster_id: c for c in list_clusters(engine, run_id, only_candidates=True)
    }
    reviews = _latest_reviews(engine, user_id)
    own = {r.candidate_id: r for r in reversed(reviews)}  # newest wins
    horizon = now - timedelta(days=config.dismiss_suppress_days)
    suppressed_titles = [
        list(r.titles)
        for r in reviews
        if r.verdict == "dismissed" and r.decided_at >= horizon
    ]
    queue = []
    for candidate in list_candidates(engine, run_id):
        cluster = clusters.get(candidate.cluster_id)
        if cluster is None:
            continue
        mine = own.get(candidate.id)
        if mine is None and any(
            _overlap(cluster.titles, t) >= _SAME_CLUSTER_OVERLAP
            for t in suppressed_titles
        ):
            continue
        queue.append(
            QueueEntry(
                candidate,
                cluster,
                mine.verdict if mine else "pending",
                mine.rationale if mine else None,
            )
        )
    return queue
