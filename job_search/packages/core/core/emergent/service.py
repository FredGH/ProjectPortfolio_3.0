"""Orchestrates emergent-role detection (PLAN.md Step 21b)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import Engine

from core.company_intel.llm import LlmTaskError
from core.emergent import store
from core.emergent.cluster import (
    Embedder,
    analyse,
    category_centroids,
    label_clusters,
    unit_rows,
)
from core.emergent.config import EmergentConfig
from core.emergent.jobs import load_discovery_jobs
from core.emergent.maturity import Maturity, assess
from core.emergent.name import COST_PER_CLUSTER_USD, NamingError, name_cluster
from core.emergent.titles import TitleStats, build_title_rows, titles_by_phrase
from core.llm.types import LLMAdapter


@dataclass(frozen=True)
class TitlesResult:
    """What building the canonical titles did.

    Attributes:
        titles: Canonical titles stored.
        stats: What was dropped.
        agency_jobs: Agency jobs excluded before scoring.
        maturity: The corpus maturity.
    """

    titles: int
    stats: TitleStats
    agency_jobs: int
    maturity: Maturity


@dataclass(frozen=True)
class ClusterOutcome:
    """What a clustering run produced.

    Attributes:
        run_id: The new run, or None when there were no titles.
        n_titles: Titles clustered.
        n_clusters: Clusters found.
        n_noise: Titles left as noise.
        n_candidates: Clusters that are emergent candidates.
        maturity: The corpus maturity.
    """

    run_id: uuid.UUID | None
    n_titles: int
    n_clusters: int
    n_noise: int
    n_candidates: int
    maturity: Maturity


class NotMatureError(RuntimeError):
    """The latest run is a preview: naming it would spend money on noise."""


@dataclass
class NamingResult:
    """What naming candidates did.

    Attributes:
        named: Candidates named.
        failed: Clusters whose naming failed, with the reason.
        calls: Usage records of every LLM call.
    """

    named: int = 0
    failed: list[tuple[int, str]] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)


def build_titles(engine: Engine, config: EmergentConfig) -> TitlesResult:
    """Rebuild the canonical titles from the discovery corpus.

    Args:
        engine: The app-role engine.
        config: The emergent config.

    Returns:
        Counts, the drops, and the corpus maturity.
    """
    jobs, agency = load_discovery_jobs(engine)
    rows, stats = build_title_rows(jobs, config)
    store.replace_titles(engine, rows)
    return TitlesResult(len(rows), stats, agency, assess(jobs, config))


def embed_titles(
    engine: Engine, titles: list[str], embed: Embedder, model: str
) -> dict[str, list[float]]:
    """Embed titles, reusing the cache.

    Args:
        engine: The app-role engine.
        titles: Canonical titles.
        embed: Title to vector.
        model: The embedding model name (a cache hit needs the same model).

    Returns:
        Title to vector for every requested title.
    """
    cached = store.load_embeddings(engine, model)
    fresh = {t: embed(t) for t in titles if t not in cached}
    if fresh:
        store.save_embeddings(engine, model, fresh)
    cached.update(fresh)
    return {t: cached[t] for t in titles}


def run_clustering(
    engine: Engine,
    config: EmergentConfig,
    embed: Embedder,
    model: str,
    seed_examples: dict[str, list[str]],
    now: datetime,
) -> ClusterOutcome:
    """Cluster the canonical titles and store the run.

    Args:
        engine: The app-role engine.
        config: The emergent config.
        embed: Title to vector.
        model: The embedding model name.
        seed_examples: Category to example titles (the taxonomy's centroids).
        now: The reference time.

    Returns:
        The outcome. Earlier runs are never overwritten.
    """
    jobs, _agency = load_discovery_jobs(engine)
    maturity = assess(jobs, config)
    by_title = titles_by_phrase(jobs, config)
    titles = sorted(by_title)
    if not titles:
        return ClusterOutcome(None, 0, 0, 0, 0, maturity)
    vectors = unit_rows(
        np.array(list(embed_titles(engine, titles, embed, model).values()), dtype=float)
    )
    labels = label_clusters(
        vectors, config.min_cluster_size, config.min_samples, config.cluster_selection
    )
    clusters = analyse(
        titles,
        vectors,
        labels,
        by_title,
        category_centroids(
            {
                c: t
                for c, t in seed_examples.items()
                if c not in config.ignore_categories
            },
            embed,
        ),
        config,
        maturity,
        now,
    )
    n_noise = int((labels == -1).sum())
    run_id = store.save_run(
        engine,
        {
            "min_cluster_size": config.min_cluster_size,
            "embedding_model": model,
            "candidate_cosine_threshold": config.candidate_cosine_threshold,
            "min_employers": config.min_employers,
        },
        len(titles),
        n_noise,
        maturity,
        clusters,
    )
    return ClusterOutcome(
        run_id,
        len(titles),
        len(clusters),
        n_noise,
        sum(1 for c in clusters if c.is_candidate),
        maturity,
    )


def unnamed_candidates(engine: Engine, run_id: uuid.UUID) -> list[store.StoredCluster]:
    """List candidate clusters that have no name yet.

    Args:
        engine: The app-role engine.
        run_id: The run.

    Returns:
        Candidate clusters without a stored hypothesis.
    """
    named = {c.cluster_id for c in store.list_candidates(engine, run_id)}
    return [
        c
        for c in store.list_clusters(engine, run_id, True)
        if c.cluster_id not in named
    ]


def estimate_cost(n_clusters: int) -> float:
    """Estimate what naming costs.

    Args:
        n_clusters: How many clusters would be named.

    Returns:
        A rough dollar figure.
    """
    return round(n_clusters * COST_PER_CLUSTER_USD, 2)


def name_candidates(
    engine: Engine,
    run_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    allow_preview: bool = False,
    config_path: Path | None = None,
) -> NamingResult:
    """Name every unnamed candidate cluster of a run.

    Args:
        engine: The app-role engine.
        run_id: The run.
        adapters: LLM adapters by provider.
        allow_preview: Name clusters of a run made while the corpus was too
            young. Off by default: a preview's "candidates" are mostly
            functions outside the taxonomy for ordinary reasons (legal,
            finance), and a name makes noise look like a finding.
        config_path: Task-config override (tests).

    Raises:
        NotMatureError: If the run was a preview and `allow_preview` is False.

    Returns:
        The result. Only clusters that cleared the employer floor and sit
        outside the taxonomy are named (`is_candidate`), each stored
        `uncorroborated`. A failed naming does not stop the others.
    """
    run = store.get_run(engine, run_id)
    if run is not None and not run.mature and not allow_preview:
        raise NotMatureError(
            f"this run is a preview ({run.maturity_note}); naming it would spend "
            "money on noise. Wait for more history, or pass allow_preview."
        )
    result = NamingResult()
    for cluster in unnamed_candidates(engine, run_id):
        skills = store.top_skills(engine, cluster.job_group_ids)
        try:
            naming = name_cluster(
                cluster, skills, adapters=adapters, config_path=config_path
            )
        except (NamingError, LlmTaskError) as exc:
            result.failed.append((cluster.cluster_id, str(exc)))
            continue
        result.calls.append(naming.run.record)
        store.save_candidate(
            engine,
            run_id,
            cluster.cluster_id,
            naming.name,
            naming.characterisation,
            skills,
            naming.run.model,
            naming.run.prompt_version,
            naming.run.record,
        )
        result.named += 1
    return result
