"""Taxonomy-independent clustering of canonical titles (PLAN.md Step 21b).

Step 11a classifies INTO a fixed taxonomy; this clusters WITHOUT one, then asks
what the taxonomy failed to cover. Titles are embedded with the local model and
grouped with HDBSCAN (density based: it leaves genuine outliers as noise
instead of forcing every title into a group). A cluster far from every category
centroid is an emergent candidate, but only if enough distinct non-agency
employers stand behind it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from core.emergent.config import EmergentConfig
from core.emergent.hybrid import Blend, best_blend, nearest_category
from core.emergent.jobs import DiscoveryJob
from core.emergent.maturity import Maturity
from core.emergent.novelty import novelty_index

Embedder = Callable[[str], list[float]]


@dataclass(frozen=True)
class ClusterResult:
    """One cluster of canonical titles.

    Attributes:
        cluster_id: Its id within the run.
        titles: Its canonical titles.
        job_ids: The jobs behind them.
        n_employers: Distinct non-agency employers behind it.
        it_share: Share of its postings that are IT-related.
        centroid: The mean unit vector of its titles.
        nearest_category: The closest category.
        nearest_cosine: The cosine to it.
        is_candidate: Outside the taxonomy AND over the employer floor.
        candidate_note: Why it is or is not a candidate.
        blend: The convincing two-category blend, if any.
        novelty: The novelty index, or None (gated, below the floor, ...).
        novelty_note: Why novelty is None, or its factors.
    """

    cluster_id: int
    titles: list[str]
    job_ids: list[str]
    n_employers: int
    it_share: float
    centroid: np.ndarray
    nearest_category: str | None
    nearest_cosine: float | None
    is_candidate: bool
    candidate_note: str
    blend: Blend | None
    novelty: float | None
    novelty_note: str


def unit_rows(matrix: np.ndarray) -> np.ndarray:
    """Normalise each row to unit length.

    Args:
        matrix: A 2-D array.

    Returns:
        The rows scaled to length 1 (zero rows unchanged).
    """
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def category_centroids(
    seed_examples: dict[str, list[str]], embed: Embedder
) -> dict[str, np.ndarray]:
    """Build one centroid per category from its seed titles.

    Args:
        seed_examples: Category to example titles.
        embed: Title to vector.

    Returns:
        Category to the mean unit vector of its seeds.
    """
    return {
        category: unit_rows(np.array([embed(t) for t in titles], dtype=float)).mean(
            axis=0
        )
        for category, titles in seed_examples.items()
        if titles
    }


def label_clusters(
    vectors: np.ndarray,
    min_cluster_size: int,
    min_samples: int = 2,
    selection: str = "leaf",
) -> np.ndarray:
    """Cluster unit vectors with HDBSCAN.

    Args:
        vectors: One unit vector per title.
        min_cluster_size: The smallest group HDBSCAN will call a cluster.
        min_samples: How dense a neighbourhood must be.
        selection: `leaf` (fine clusters) or `eom` (merges short titles into
            one blob on real data).

    Returns:
        A label per title; -1 means noise. Fewer titles than
        `min_cluster_size` are all noise.
    """
    if len(vectors) < max(min_cluster_size, 2):
        return np.full(len(vectors), -1)
    from sklearn.cluster import (  # imported lazily: heavy, optional at import time
        HDBSCAN,
    )

    return HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_method=selection,
        copy=True,
    ).fit_predict(vectors)


def analyse(
    titles: list[str],
    vectors: np.ndarray,
    labels: np.ndarray,
    jobs_by_title: dict[str, list[DiscoveryJob]],
    categories: dict[str, np.ndarray],
    config: EmergentConfig,
    maturity: Maturity,
    now: datetime,
) -> list[ClusterResult]:
    """Describe each cluster: nearest category, candidacy, hybridity, novelty.

    Args:
        titles: The canonical titles, aligned with `vectors` and `labels`.
        vectors: Unit vectors per title.
        labels: HDBSCAN labels (-1 = noise, which is skipped).
        jobs_by_title: Canonical title to its non-agency jobs.
        categories: Category centroids.
        config: The emergent config.
        maturity: The corpus maturity; below it novelty is withheld.
        now: The reference time.

    Returns:
        One result per non-noise cluster, by id.
    """
    corpus_start = min(
        (j.first_seen_at for js in jobs_by_title.values() for j in js), default=now
    )
    results: list[ClusterResult] = []
    for cluster_id in sorted(int(c) for c in set(labels.tolist()) if c != -1):
        members = [i for i, label in enumerate(labels.tolist()) if label == cluster_id]
        member_titles = [titles[i] for i in members]
        jobs = [j for t in member_titles for j in jobs_by_title.get(t, [])]
        employers = {j.employer for j in jobs if j.employer}
        centroid = vectors[members].mean(axis=0)
        name, cos = (
            nearest_category(centroid, categories) if categories else (None, None)
        )
        outside = cos is not None and cos < config.candidate_cosine_threshold
        enough = len(employers) >= config.min_employers
        it_share = (
            sum(1 for j in jobs if j.it_term_count >= config.min_it_terms) / len(jobs)
            if jobs
            else 0.0
        )
        it_related = it_share >= config.min_it_share
        if name is None:
            note = "cannot judge: no category centroids to compare with"
        elif not outside:
            note = f"inside the taxonomy: closest to {name} ({cos:.2f})"
        elif not it_related:
            note = (
                f"not IT-related: {it_share:.0%} of postings mention technology "
                f"(needs {config.min_it_share:.0%})"
            )
        elif not enough:
            note = (
                f"noise: {len(employers)} employer(s), floor is {config.min_employers}"
            )
        else:
            note = (
                f"outside the taxonomy (closest {name}, {cos:.2f}) "
                f"with {len(employers)} employers"
            )
        blend = (
            best_blend(
                centroid,
                categories,
                min_gain=config.hybrid_min_gain,
                min_weight=config.hybrid_min_weight,
            )
            if categories
            else None
        )
        if maturity.mature:
            nov = novelty_index(jobs, config, now, corpus_start)
            novelty, novelty_note = nov.value, nov.note
        else:
            novelty, novelty_note = (
                None,
                f"withheld: history too short ({maturity.note})",
            )
        results.append(
            ClusterResult(
                cluster_id=cluster_id,
                titles=member_titles,
                job_ids=sorted({j.job_group_id for j in jobs}),
                n_employers=len(employers),
                it_share=it_share,
                centroid=centroid,
                nearest_category=name,
                nearest_cosine=cos,
                is_candidate=bool(outside and enough and it_related),
                candidate_note=note,
                blend=blend,
                novelty=novelty,
                novelty_note=novelty_note,
            )
        )
    return results
