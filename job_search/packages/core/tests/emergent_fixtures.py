"""Synthetic corpus, embedder and seeds for the emergent-role tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import numpy as np

from core.emergent.jobs import DiscoveryJob

NOW = datetime(2027, 3, 1, tzinfo=UTC)
# Dimensions: software, data, science, forward-deployed (a direction no category has).
_BASE = {
    "software": [1.0, 0.0, 0.0, 0.0],
    "backend": [1.0, 0.1, 0.0, 0.0],
    "data": [0.0, 1.0, 0.0, 0.0],
    "etl": [0.1, 1.0, 0.0, 0.0],
    "scientist": [0.0, 0.0, 1.0, 0.0],
    "research": [0.0, 0.1, 1.0, 0.0],
    "deployed": [0.1, 0.1, 0.0, 1.0],
    "field": [0.1, 0.0, 0.0, 1.0],
    "solutions": [0.1, 0.0, 0.1, 1.0],
    "hybrid": [0.6, 0.4, 0.0, 0.0],
    "sw-data": [0.55, 0.45, 0.0, 0.05],
    "blend": [0.62, 0.38, 0.0, 0.0],
}
SEEDS = {
    "software_engineer": ["software engineer", "backend developer"],
    "data_engineer": ["data engineer", "etl developer"],
    "data_scientist": ["scientist", "research scientist"],
}


def embed(title: str) -> list[float]:
    """Embed a title deterministically from its keywords.

    Args:
        title: A canonical title.

    Returns:
        The mean of its keyword vectors plus a tiny title-specific jitter, so
        similar titles cluster and distinct ones differ.
    """
    parts = [_BASE[k] for k in _BASE if k in title.lower()] or [[0.0, 0.0, 0.0, 0.0]]
    vector = np.mean(np.array(parts, dtype=float), axis=0)
    seed = int(hashlib.sha1(title.encode()).hexdigest()[:8], 16)
    jitter = np.random.default_rng(seed).normal(0.0, 0.01, 4)
    return [float(x) for x in vector + jitter]


def job(title: str, employer: str | None, days_ago: float, n: int = 0) -> DiscoveryJob:
    """Build a discovery job.

    Args:
        title: The raw title.
        employer: The normalised employer.
        days_ago: How long before `NOW` it was first seen.
        n: A disambiguator so two jobs can share title/employer/age.

    Returns:
        A `DiscoveryJob`.
    """
    return DiscoveryJob(
        f"zzfixture-{title}-{employer}-{days_ago}-{n}",
        title,
        employer,
        employer,
        NOW - timedelta(days=days_ago),
        "greenhouse",
    )


def corpus() -> list[DiscoveryJob]:
    """Build a corpus with in-taxonomy roles and one emergent role.

    Returns:
        Jobs over ~100 days: software/data/science roles at many employers, and
        a "forward deployed" family at six employers plus a two-employer fad.
    """
    jobs: list[DiscoveryJob] = []
    for i in range(8):
        jobs += [
            job("Software Engineer", f"sw{i}", 100 - i * 12),
            job("Backend Developer", f"sw{i}", 98 - i * 12),
        ]
        jobs += [
            job("Data Engineer", f"de{i}", 99 - i * 12),
            job("ETL Developer", f"de{i}", 97 - i * 12),
        ]
        jobs += [
            job("Research Scientist", f"ds{i}", 96 - i * 12),
            job("Data Scientist", f"ds{i}", 95 - i * 12),
        ]
    titles = [
        "Forward Deployed Engineer",
        "Field Deployed Engineer",
        "Deployed Solutions Engineer",
    ]
    for i in range(6):
        for t, title in enumerate(titles):
            jobs.append(job(title, f"fd{i}", 90 - i * 10, t))
    for i in range(2):
        jobs += [
            job("Solutions Field Wizard Lead", f"fad{i}", 20 - i),
            job("Field Solutions Architect Hero", f"fad{i}", 19 - i),
        ]
    jobs += [job("Software Engineer", "agency-x", 5), job("Deployed Engineer", None, 4)]
    return jobs
