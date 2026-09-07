"""Per-job similarity features for Step 8's candidate-pair scoring
(PLAN.md Step 8): a description SimHash fingerprint and Step 6's
parsed-salary output. Computed once per job (core.dedup.
write_similarity_features), never per pair — the pairwise Hamming
distance and salary-overlap comparison happen in dbt (dedup__
similarity_scores), reading both sides' precomputed features.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.dedup.simhash import compute_simhash
from core.normalisation.salary import parse_salary


@dataclass(frozen=True)
class SimilarityFeatures:
    """One job's precomputed Step 8 similarity inputs.

    Attributes:
        description_simhash: 64-bit SimHash fingerprint of the
            description (0 if description is None/empty).
        rate_annualised: Step 6's parse_salary annualised GBP figure, or
            None if unstated.
        rate_currency: The originally-stated currency, or None.
        salary_band: Step 6's 10k-wide GBP band string, or None.
    """

    description_simhash: int
    rate_annualised: float | None
    rate_currency: str | None
    salary_band: str | None


def compute_similarity_features(
    description: str | None, salary_raw: str | None
) -> SimilarityFeatures:
    """Compute one job's Step 8 similarity features.

    Args:
        description: The posting's free text, or None.
        salary_raw: The staging-layer salary_raw column, or None.

    Returns:
        The `SimilarityFeatures`.
    """
    fingerprint = compute_simhash(description or "")
    salary = parse_salary(description, salary_raw)
    return SimilarityFeatures(
        description_simhash=fingerprint,
        rate_annualised=salary.annualised_gbp,
        rate_currency=salary.original_currency,
        salary_band=salary.band,
    )
