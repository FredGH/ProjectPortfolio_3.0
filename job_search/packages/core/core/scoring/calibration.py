"""Weight fitting and calibration for the scoring funnel (PLAN.md Step 16).

Rank correlation, not least-squares regression, is the fitting objective:
PLAN.md's own "Done when" criterion is about ranking agreement ("top 10 by
computed score substantially matches top 10 by hand ranking"), not about
predicting a label's exact numeric value.
"""

from __future__ import annotations

from itertools import product

from scipy.stats import spearmanr

_COMPONENTS = ("vector_similarity", "reranker", "skill_coverage", "llm_fit")

# Every multiple of 0.05 from 0.0 to 1.0 — the grid search's per-component
# candidate values. 1,771 four-tuples sum to 1.0 out of this grid; trivial
# to evaluate all of them (no need for a smarter optimizer at this scale).
_STEP = 0.05
_LEVELS = tuple(round(i * _STEP, 2) for i in range(int(1 / _STEP) + 1))


def _blend(row: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean of a row's components — same formula as blend.py's
    inline computation, factored out here since this module calls it
    from both the grid search and the agreement check.

    Args:
        row: Component name -> score, e.g. {"vector_similarity": 0.8, ...}.
        weights: Component name -> weight. Assumed to sum to 1.0 (the
            grid search only ever produces such vectors).

    Returns:
        The weighted-mean blended score.
    """
    return sum(row[c] * weights[c] for c in _COMPONENTS)


def _spearman_agreement(
    rows: list[dict[str, float]],
    labels: list[float],
    weights: dict[str, float],
) -> float | None:
    """Spearman rank correlation between blended scores and labels.

    Args:
        rows: One dict of component scores per job, same order as `labels`.
        labels: The numeric label (1.0/0.5/0.0) for each row, same order.
        weights: The weight vector to blend `rows` with.

    Returns:
        The Spearman correlation, or None when it is mathematically
        undefined (e.g. every label is identical, so there is no rank
        variation to correlate against) — scipy returns `nan` in that
        case; this function converts `nan` to `None` so no caller ever
        has to special-case a NaN float.
    """
    blended = [_blend(row, weights) for row in rows]
    correlation, _p_value = spearmanr(blended, labels)
    if correlation != correlation:  # NaN != NaN is the classic NaN check
        return None
    return float(correlation)


def _grid_search_weights(
    fit_rows: list[dict[str, float]], fit_labels: list[float]
) -> dict[str, float]:
    """Find the weight vector maximizing Spearman agreement on the fit set.

    Searches every 4-tuple of multiples of 0.05 in [0, 1] that sums to
    1.0. Ties are broken toward the smoothest distribution (lowest max
    single weight), then by iteration order, for full determinism.

    Args:
        fit_rows: One dict of component scores per fit-set job.
        fit_labels: The numeric label for each row, same order.

    Returns:
        The winning weight vector, one entry per component in
        `_COMPONENTS`, summing to 1.0.
    """
    best_weights: dict[str, float] | None = None
    best_score = float("-inf")
    best_max_weight = float("inf")
    for combo in product(_LEVELS, repeat=len(_COMPONENTS)):
        if abs(sum(combo) - 1.0) > 1e-9:
            continue
        weights = dict(zip(_COMPONENTS, combo))
        agreement = _spearman_agreement(fit_rows, fit_labels, weights)
        if agreement is None:
            continue
        max_weight = max(combo)
        better = agreement > best_score + 1e-9
        tied_but_smoother = (
            abs(agreement - best_score) <= 1e-9 and max_weight < best_max_weight
        )
        if better or tied_but_smoother:
            best_weights = weights
            best_score = agreement
            best_max_weight = max_weight
    if best_weights is None:
        # Every candidate produced an undefined agreement (e.g. every
        # fit_label is identical) — fall back to equal weights, the same
        # placeholder blend.py itself uses pre-calibration.
        return {c: 1.0 / len(_COMPONENTS) for c in _COMPONENTS}
    return best_weights
