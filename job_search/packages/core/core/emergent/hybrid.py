"""Cross-category hybridity (PLAN.md Step 21b).

For a cluster centroid, compare the cosine to the nearest single category
centroid with the best two-centroid blend. A role much closer to
0.6 x software_engineer + 0.4 x data_engineer than to either alone is a hybrid:
a function forming in the space between two categories (the Forward Deployed
Engineer pattern).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np


@dataclass(frozen=True)
class Blend:
    """The best two-category blend for a centroid.

    Attributes:
        category_a: The first category.
        weight_a: Its weight.
        category_b: The second category.
        weight_b: Its weight (1 - weight_a).
        cosine: The cosine of the centroid to the blend.
        gain: The blend's cosine minus the best single-category cosine.
    """

    category_a: str
    weight_a: float
    category_b: str
    weight_b: float
    cosine: float
    gain: float


def _unit(v: np.ndarray) -> np.ndarray:
    """Normalise a vector to unit length.

    Args:
        v: A vector.

    Returns:
        The unit vector, or the zero vector unchanged.
    """
    norm = float(np.linalg.norm(v))
    return v if norm == 0 else v / norm


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two vectors.

    Args:
        a: The first vector.
        b: The second vector.

    Returns:
        The cosine, or 0.0 if either is the zero vector.
    """
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return 0.0 if na == 0 or nb == 0 else float(np.dot(a, b) / (na * nb))


def nearest_category(
    centroid: np.ndarray, categories: dict[str, np.ndarray]
) -> tuple[str, float]:
    """Find the closest single category centroid.

    Args:
        centroid: A cluster centroid.
        categories: Category name to centroid.

    Returns:
        `(category, cosine)`; ties break by name so the result is stable.
    """
    return max(
        ((name, cosine(centroid, vec)) for name, vec in categories.items()),
        key=lambda pair: (pair[1], pair[0]),
    )


def best_blend(
    centroid: np.ndarray,
    categories: dict[str, np.ndarray],
    *,
    min_gain: float,
    min_weight: float,
    step: float = 0.1,
) -> Blend | None:
    """Find a convincing two-category blend, if any.

    Args:
        centroid: A cluster centroid.
        categories: Category name to centroid (at least two).
        min_gain: Required cosine gain over the best single category.
        min_weight: Minimum weight of each side.
        step: Weight grid step.

    Returns:
        The best blend when it beats the best single category by `min_gain`
        with both weights at least `min_weight`; otherwise None.
    """
    if len(categories) < 2:
        return None
    _, single = nearest_category(centroid, categories)
    best: Blend | None = None
    steps = int(round(1 / step))
    for (name_a, vec_a), (name_b, vec_b) in combinations(sorted(categories.items()), 2):
        for i in range(1, steps):
            w = i * step
            if min(w, 1 - w) < min_weight - 1e-9:
                continue
            c = cosine(centroid, w * _unit(vec_a) + (1 - w) * _unit(vec_b))
            if best is None or c > best.cosine:
                best = Blend(
                    name_a, round(w, 2), name_b, round(1 - w, 2), c, c - single
                )
    if best is None or best.gain < min_gain:
        return None
    return best
