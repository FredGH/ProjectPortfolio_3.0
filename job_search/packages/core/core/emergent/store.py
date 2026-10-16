"""Persistence for emergent-role detection (PLAN.md Step 21b).

Titles, embeddings, cluster runs, clusters and named candidates are shared,
derived data (no RLS); only reviews are per user (see review.py).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text

from core.emergent.cluster import ClusterResult
from core.emergent.maturity import Maturity
from core.emergent.titles import TitleRow


@dataclass(frozen=True)
class StoredCluster:
    """A stored cluster.

    Attributes:
        run_id: The run it belongs to.
        cluster_id: Its id within the run.
        titles: Its canonical titles.
        job_group_ids: The jobs behind it.
        n_employers: Distinct non-agency employers.
        nearest_category: The closest category.
        nearest_cosine: The cosine to it.
        is_candidate: Outside the taxonomy and over the employer floor.
        candidate_note: Why or why not.
        hybrid: The blend, as a dict, or None.
        novelty: The novelty index, or None.
        novelty_note: Why None, or the factors.
    """

    run_id: uuid.UUID
    cluster_id: int
    titles: list[str]
    job_group_ids: list[str]
    n_employers: int
    nearest_category: str | None
    nearest_cosine: float | None
    is_candidate: bool
    candidate_note: str | None
    hybrid: dict | None
    novelty: float | None
    novelty_note: str | None


@dataclass(frozen=True)
class StoredRun:
    """A stored cluster run.

    Attributes:
        id: Run id.
        created_at: When it ran.
        params: The parameters used.
        n_titles: Titles clustered.
        n_clusters: Clusters found.
        n_noise: Titles left as noise.
        history_days: Corpus history at the time.
        mature: Whether the maturity gate was met.
        maturity_note: What was missing, if anything.
    """

    id: uuid.UUID
    created_at: datetime
    params: dict
    n_titles: int
    n_clusters: int
    n_noise: int
    history_days: float
    mature: bool
    maturity_note: str | None


@dataclass(frozen=True)
class StoredCandidate:
    """A named, uncorroborated hypothesis.

    Attributes:
        id: Row id.
        run_id: The run.
        cluster_id: The cluster.
        name: The proposed name.
        characterisation: A short description.
        skill_signature: Its characteristic skills.
        corroboration: `uncorroborated` or `corroborated`.
        created_at: When it was named.
    """

    id: uuid.UUID
    run_id: uuid.UUID
    cluster_id: int
    name: str
    characterisation: str
    skill_signature: list[str]
    corroboration: str
    created_at: datetime


def replace_titles(engine: Engine, rows: list[TitleRow]) -> None:
    """Rebuild `emergent.title_canonical` from scratch.

    Args:
        engine: The app-role engine.
        rows: The canonical titles.
    """
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM emergent.title_canonical"))
        for r in rows:
            conn.execute(
                text(
                    "INSERT INTO emergent.title_canonical (canonical_title, "
                    "first_seen_at, "
                    "last_seen_at, distinct_employer_count, distinct_source_count, "
                    "postings, example_titles) VALUES (:t, :f, :l, :e, :s, :p, :x)"
                ),
                {
                    "t": r.canonical_title,
                    "f": r.first_seen_at,
                    "l": r.last_seen_at,
                    "e": len(r.employers),
                    "s": len(r.sources),
                    "p": r.postings,
                    "x": r.examples,
                },
            )


def load_embeddings(engine: Engine, model: str) -> dict[str, list[float]]:
    """Load cached title embeddings for a model.

    Args:
        engine: The app-role engine.
        model: The embedding model name.

    Returns:
        Canonical title to its cached vector.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT canonical_title, vector FROM emergent.title_embedding WHERE "
                "model = :m"
            ),
            {"m": model},
        ).all()
    return {r.canonical_title: list(r.vector) for r in rows}


def save_embeddings(
    engine: Engine, model: str, vectors: dict[str, list[float]]
) -> None:
    """Cache title embeddings.

    Args:
        engine: The app-role engine.
        model: The embedding model name.
        vectors: Canonical title to vector.
    """
    with engine.begin() as conn:
        for title, vector in vectors.items():
            conn.execute(
                text(
                    "INSERT INTO emergent.title_embedding (canonical_title, model, "
                    "vector) "
                    "VALUES (:t, :m, :v) ON CONFLICT (canonical_title, model) "
                    "DO UPDATE SET vector = :v, created_at = now()"
                ),
                {"t": title, "m": model, "v": vector},
            )


def save_run(
    engine: Engine,
    params: dict,
    n_titles: int,
    n_noise: int,
    maturity: Maturity,
    clusters: list[ClusterResult],
) -> uuid.UUID:
    """Save one cluster run (never overwriting an earlier one).

    Args:
        engine: The app-role engine.
        params: The parameters used.
        n_titles: Titles clustered.
        n_noise: Titles left as noise.
        maturity: The corpus maturity at run time.
        clusters: The clusters found.

    Returns:
        The new run's id.
    """
    with engine.begin() as conn:
        run_id = conn.execute(
            text(
                "INSERT INTO emergent.cluster_run (params, n_titles, n_clusters, "
                "n_noise, "
                "history_days, mature, maturity_note) VALUES (CAST(:p AS jsonb), :nt, "
                ":nc, :nn, :h, :m, :note) RETURNING id"
            ),
            {
                "p": json.dumps(params),
                "nt": n_titles,
                "nc": len(clusters),
                "nn": n_noise,
                "h": maturity.history_days,
                "m": maturity.mature,
                "note": maturity.note or None,
            },
        ).scalar_one()
        for c in clusters:
            conn.execute(
                text(
                    "INSERT INTO emergent.title_cluster (run_id, cluster_id, "
                    "canonical_titles, "
                    "job_group_ids, n_titles, n_employers, centroid, nearest_category, "
                    "nearest_cosine, is_candidate, candidate_note, hybrid_a, "
                    "hybrid_weight_a, "
                    "hybrid_b, hybrid_weight_b, hybrid_gain, novelty, novelty_note) "
                    "VALUES "
                    "(:r, :c, :t, :j, :nt, :ne, :cen, :nc, :ncos, :cand, :note, :ha, "
                    ":wa, "
                    ":hb, :wb, :g, :nov, :novn)"
                ),
                {
                    "r": run_id,
                    "c": c.cluster_id,
                    "t": c.titles,
                    "j": c.job_ids,
                    "nt": len(c.titles),
                    "ne": c.n_employers,
                    "cen": [float(x) for x in c.centroid],
                    "nc": c.nearest_category,
                    "ncos": c.nearest_cosine,
                    "cand": c.is_candidate,
                    "note": c.candidate_note,
                    "ha": c.blend.category_a if c.blend else None,
                    "wa": c.blend.weight_a if c.blend else None,
                    "hb": c.blend.category_b if c.blend else None,
                    "wb": c.blend.weight_b if c.blend else None,
                    "g": c.blend.gain if c.blend else None,
                    "nov": c.novelty,
                    "novn": c.novelty_note,
                },
            )
    return run_id


def latest_run(engine: Engine) -> StoredRun | None:
    """Read the newest cluster run.

    Args:
        engine: The app-role engine.

    Returns:
        The run, or None when none has been made.
    """
    with engine.begin() as conn:
        r = conn.execute(
            text(
                "SELECT id, created_at, params, n_titles, n_clusters, n_noise, "
                "history_days, mature, maturity_note FROM emergent.cluster_run "
                "ORDER BY created_at DESC LIMIT 1"
            )
        ).one_or_none()
    if r is None:
        return None
    return StoredRun(
        r.id,
        r.created_at,
        r.params,
        r.n_titles,
        r.n_clusters,
        r.n_noise,
        r.history_days,
        r.mature,
        r.maturity_note,
    )


def list_clusters(
    engine: Engine, run_id: uuid.UUID, only_candidates: bool = False
) -> list[StoredCluster]:
    """List a run's clusters.

    Args:
        engine: The app-role engine.
        run_id: The run.
        only_candidates: Restrict to emergent candidates.

    Returns:
        The clusters by id.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT run_id, cluster_id, canonical_titles, job_group_ids, "
                "n_employers, "
                "nearest_category, nearest_cosine, is_candidate, candidate_note, "
                "hybrid_a, "
                "hybrid_weight_a, hybrid_b, hybrid_weight_b, hybrid_gain, novelty, "
                "novelty_note FROM emergent.title_cluster WHERE run_id = :r "
                "AND (NOT :only OR is_candidate) ORDER BY cluster_id"
            ),
            {"r": run_id, "only": only_candidates},
        ).all()
    return [
        StoredCluster(
            r.run_id,
            r.cluster_id,
            list(r.canonical_titles),
            list(r.job_group_ids),
            r.n_employers,
            r.nearest_category,
            r.nearest_cosine,
            r.is_candidate,
            r.candidate_note,
            (
                None
                if r.hybrid_a is None
                else {
                    "category_a": r.hybrid_a,
                    "weight_a": r.hybrid_weight_a,
                    "category_b": r.hybrid_b,
                    "weight_b": r.hybrid_weight_b,
                    "gain": r.hybrid_gain,
                }
            ),
            r.novelty,
            r.novelty_note,
        )
        for r in rows
    ]


def save_candidate(
    engine: Engine,
    run_id: uuid.UUID,
    cluster_id: int,
    name: str,
    characterisation: str,
    skills: list[str],
    model: str,
    prompt_version: str,
    usage: dict,
) -> uuid.UUID:
    """Save (or replace) the named hypothesis for a cluster.

    Args:
        engine: The app-role engine.
        run_id: The run.
        cluster_id: The cluster.
        name: The proposed name.
        characterisation: A short description.
        skills: The skill signature.
        model: The naming model.
        prompt_version: The prompt version.
        usage: Token use and cost.

    Returns:
        The candidate's id. It is always stored `uncorroborated`.
    """
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO emergent.role_candidate (run_id, cluster_id, name, "
                "characterisation, skill_signature, model, prompt_version, usage) "
                "VALUES "
                "(:r, :c, :n, :ch, :s, :m, :pv, CAST(:u AS jsonb)) ON CONFLICT ON "
                "CONSTRAINT uq_role_candidate_run_cluster DO UPDATE SET name = :n, "
                "characterisation = :ch, skill_signature = :s, model = :m, "
                "prompt_version = :pv, usage = CAST(:u AS jsonb), created_at = now() "
                "RETURNING id"
            ),
            {
                "r": run_id,
                "c": cluster_id,
                "n": name,
                "ch": characterisation,
                "s": skills,
                "m": model,
                "pv": prompt_version,
                "u": json.dumps(usage),
            },
        ).scalar_one()


def list_candidates(engine: Engine, run_id: uuid.UUID) -> list[StoredCandidate]:
    """List a run's named candidates.

    Args:
        engine: The app-role engine.
        run_id: The run.

    Returns:
        The candidates by cluster id.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, run_id, cluster_id, name, characterisation, "
                "skill_signature, "
                "corroboration, created_at FROM emergent.role_candidate WHERE run_id "
                "= :r "
                "ORDER BY cluster_id"
            ),
            {"r": run_id},
        ).all()
    return [
        StoredCandidate(
            r.id,
            r.run_id,
            r.cluster_id,
            r.name,
            r.characterisation,
            list(r.skill_signature),
            r.corroboration,
            r.created_at,
        )
        for r in rows
    ]


def top_skills(engine: Engine, job_group_ids: list[str], limit: int = 8) -> list[str]:
    """Find the most common extracted skills among some jobs.

    Args:
        engine: The app-role engine.
        job_group_ids: The jobs.
        limit: How many skills.

    Returns:
        Skill labels by number of jobs mentioning them.
    """
    if not job_group_ids:
        return []
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT s.canonical_label FROM silver.silver__bridge_job_skill b "
                "JOIN silver.silver__skill s USING (skill_id) "
                "WHERE b.job_group_id = ANY(:ids) GROUP BY s.canonical_label "
                "ORDER BY count(DISTINCT b.job_group_id) DESC, s.canonical_label "
                "LIMIT :n"
            ),
            {"ids": job_group_ids, "n": limit},
        ).all()
    return [r.canonical_label for r in rows]


def candidate_exists(engine: Engine, candidate_id: uuid.UUID) -> bool:
    """Say whether a named candidate exists.

    Args:
        engine: The app-role engine.
        candidate_id: The candidate's id.

    Returns:
        True when a `role_candidate` row has that id.
    """
    with engine.begin() as conn:
        return bool(
            conn.execute(
                text("SELECT 1 FROM emergent.role_candidate WHERE id = :i"),
                {"i": candidate_id},
            ).first()
        )


def get_run(engine: Engine, run_id: uuid.UUID) -> StoredRun | None:
    """Read one cluster run.

    Args:
        engine: The app-role engine.
        run_id: The run.

    Returns:
        The run, or None.
    """
    with engine.begin() as conn:
        r = conn.execute(
            text(
                "SELECT id, created_at, params, n_titles, n_clusters, n_noise, "
                "history_days, mature, maturity_note FROM emergent.cluster_run "
                "WHERE id = :i"
            ),
            {"i": run_id},
        ).one_or_none()
    if r is None:
        return None
    return StoredRun(
        r.id,
        r.created_at,
        r.params,
        r.n_titles,
        r.n_clusters,
        r.n_noise,
        r.history_days,
        r.mature,
        r.maturity_note,
    )
