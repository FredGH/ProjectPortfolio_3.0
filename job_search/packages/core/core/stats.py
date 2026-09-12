"""Sample-size statistics for the categorisation review's QA target.

Standard sample-size-for-a-proportion formula with a finite-population
correction (Cochran, 1977), used to recommend how many hand-checked
`gold.dim_job` classifications give a target confidence/margin of error,
and to report the margin of error a chosen sample size actually implies.
"""

from __future__ import annotations

import math

# z-scores for the confidence levels the review UI offers. p=0.5 (the
# most conservative assumption, maximising required sample size when the
# true agreement rate is unknown) is the only proportion this module is
# used for, so it isn't a parameter.
_Z_SCORES: dict[float, float] = {
    0.80: 1.282,
    0.90: 1.645,
    0.95: 1.960,
    0.99: 2.576,
}
_P = 0.5


def _z_score(confidence: float) -> float:
    """Look up the z-score for a supported confidence level.

    Args:
        confidence: One of 0.80, 0.90, 0.95, 0.99.

    Returns:
        The two-tailed z-score for that confidence level.

    Raises:
        ValueError: If `confidence` isn't one of the supported levels.
    """
    try:
        return _Z_SCORES[confidence]
    except KeyError:
        supported = ", ".join(str(level) for level in sorted(_Z_SCORES))
        raise ValueError(
            f"Unsupported confidence level {confidence!r} — use one of {supported}."
        ) from None


def recommended_sample_size(
    population: int, confidence: float, margin_of_error: float
) -> int:
    """Recommend how many jobs to hand-check for a target precision.

    Args:
        population: Total classified jobs the sample is drawn from
            (reviewed + unreviewed).
        confidence: How confident the resulting agreement-rate estimate
            should be — one of 0.80, 0.90, 0.95, 0.99.
        margin_of_error: The target margin of error, e.g. 0.08 for +/-8
            percentage points.

    Returns:
        The recommended sample size, never more than `population`.
    """
    if population <= 0:
        return 0
    z = _z_score(confidence)
    unconstrained = (z**2 * _P * (1 - _P)) / (margin_of_error**2)
    corrected = unconstrained / (1 + (unconstrained - 1) / population)
    return min(population, math.ceil(corrected))


def margin_of_error_for_sample_size(
    population: int, sample_size: int, confidence: float
) -> float:
    """Report the margin of error a chosen sample size implies.

    The inverse of `recommended_sample_size`'s finite-population
    correction — lets the review UI show the precision trade-off as a
    reviewer raises or lowers their review target.

    Args:
        population: Total classified jobs the sample is drawn from.
        sample_size: The number of jobs the reviewer plans to check.
        confidence: The same confidence level `sample_size` should be
            read at — one of 0.80, 0.90, 0.95, 0.99.

    Returns:
        The margin of error, e.g. 0.08 for +/-8 percentage points. 0.0
        if `sample_size` covers the whole population (a full census).

    Raises:
        ValueError: If `sample_size` is not positive, or exceeds
            `population`.
    """
    if sample_size <= 0:
        raise ValueError(f"sample_size must be positive, got {sample_size}.")
    if sample_size > population:
        raise ValueError(
            f"sample_size ({sample_size}) can't exceed population ({population})."
        )
    if sample_size == population:
        return 0.0
    z = _z_score(confidence)
    unconstrained = sample_size * (population - 1) / (population - sample_size)
    return z * math.sqrt(_P * (1 - _P) / unconstrained)
