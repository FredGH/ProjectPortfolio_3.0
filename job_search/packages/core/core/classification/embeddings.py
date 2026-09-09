"""Embedding nearest-centroid classification (PLAN.md Step 11a, stage
2) — the fallback for titles the rules stage (stage 1) couldn't
confidently place. Centroids are rebuilt fresh from the hand-curated
seed set (config/category_seed_examples.yml) on every run rather than
cached: a few dozen embedding calls against a local model is cheap,
and this sidesteps an entire "has the seed set changed" invalidation
problem for a 3-point backlog item.
"""

from __future__ import annotations

import math
from pathlib import Path

import httpx
import yaml

from core.embedding.ollama import embed_text

_DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "category_seed_examples.yml"
)

_CONFIDENT_COSINE_THRESHOLD = 0.75
"""Below this cosine similarity to the nearest centroid, this stage
declines to classify (returns None) and cascades to the LLM stage
rather than guess. A starting value, not empirically tuned — PLAN.md's
own "hand-check 100 classifications" acceptance activity is what
should adjust it if agreement comes in low."""


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Args:
        a: The first vector.
        b: The second vector.

    Returns:
        A value in [-1, 1], or 0.0 if either vector has zero magnitude
        (avoids a division-by-zero rather than raising — a zero vector
        has no meaningful direction to compare).
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def load_seed_examples(seed_path: Path | None = None) -> dict[str, list[str]]:
    """Load the hand-curated category -> example-titles seed set.

    Args:
        seed_path: Path to the seed YAML file. Defaults to
            config/category_seed_examples.yml at the repository root.

    Returns:
        category -> list of example title strings.
    """
    path = seed_path or _DEFAULT_SEED_PATH
    raw = yaml.safe_load(path.read_text())
    return raw["categories"]


def build_centroids(
    seed_examples: dict[str, list[str]],
    *,
    base_url: str,
    model: str,
    client: httpx.Client,
) -> dict[str, list[float]]:
    """Compute one centroid embedding per category from its seed examples.

    Args:
        seed_examples: category -> list of example title strings (see
            load_seed_examples).
        base_url: Ollama server base URL.
        model: Ollama embedding model tag.
        client: The HTTP client to issue requests with.

    Returns:
        category -> centroid vector (the element-wise mean of that
        category's example embeddings).
    """
    centroids: dict[str, list[float]] = {}
    for category, examples in seed_examples.items():
        vectors = [
            embed_text(example, base_url=base_url, model=model, client=client)
            for example in examples
        ]
        dimension = len(vectors[0])
        centroids[category] = [
            sum(vector[i] for vector in vectors) / len(vectors)
            for i in range(dimension)
        ]
    return centroids


def classify_by_embedding(
    title: str,
    centroids: dict[str, list[float]],
    *,
    base_url: str,
    model: str,
    client: httpx.Client,
) -> tuple[str, float] | None:
    """Classify one title by cosine distance to the nearest centroid.

    Args:
        title: The job title to classify.
        centroids: Precomputed category -> centroid vector (see
            build_centroids — computed once per batch, not once per
            title).
        base_url: Ollama server base URL.
        model: Ollama embedding model tag.
        client: The HTTP client to issue requests with.

    Returns:
        `(category, cosine_similarity)` for the nearest centroid, or
        `None` if the best match falls below
        `_CONFIDENT_COSINE_THRESHOLD` — the caller must cascade to the
        LLM stage next, never guess.
    """
    vector = embed_text(title, base_url=base_url, model=model, client=client)
    best_category, best_score = max(
        (
            (category, _cosine_similarity(vector, centroid))
            for category, centroid in centroids.items()
        ),
        key=lambda pair: pair[1],
    )
    if best_score < _CONFIDENT_COSINE_THRESHOLD:
        return None
    return best_category, best_score
