"""Salary parsing to an annualised GBP band (PLAN.md Step 6).

A thin wrapper over core.enrichment.engagement_terms.
extract_engagement_terms (PLAN.md Step 5a) — that module already solves
day-rate/annual disambiguation and rate-basis detection; re-deriving it
here would duplicate the exact regexes Step 5a already tests against
real data. This module adds only what Step 5a deliberately left out:
currency conversion to GBP and banding for coarse dedup comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.enrichment.engagement_terms import extract_engagement_terms

_APPROXIMATE_FX_TO_GBP = {"GBP": 1.0, "USD": 0.79, "EUR": 0.85}
"""Static, approximate rates — this is a coarse dedup-matching signal,
not a financial calculation, so a live FX API is deliberately not used.
Update by hand if these drift far enough to matter."""

_BAND_WIDTH_GBP = 10_000


@dataclass(frozen=True)
class ParsedSalary:
    """A posting's salary, normalised for coarse comparison across postings.

    Attributes:
        annualised_gbp: The rate annualised and converted to GBP — this
            is the GBP-converted figure, unlike
            `EngagementTerms.rate_annualised`, which stays in the
            currency the posting stated. `None` when no rate was stated
            (rate_basis == "unknown").
        band: A GBP band string, e.g. "80000-90000", or `None` when
            annualised_gbp is `None`.
        original_currency: The currency the rate was actually stated in,
            before conversion — preserved for provenance.
        rate_basis: Passed through from extract_engagement_terms (annual,
            daily, hourly, or unknown).
    """

    annualised_gbp: float | None
    band: str | None
    original_currency: str | None
    rate_basis: str


def _band(amount: float) -> str:
    """Bucket an amount into a fixed-width GBP band.

    Args:
        amount: The annualised GBP amount.

    Returns:
        A string like "80000-90000".
    """
    floor = int(amount // _BAND_WIDTH_GBP) * _BAND_WIDTH_GBP
    return f"{floor}-{floor + _BAND_WIDTH_GBP}"


def parse_salary(description: str | None, salary_raw: str | None) -> ParsedSalary:
    """Parse a posting's salary to an annualised GBP band.

    Args:
        description: The posting's free text, passed through to
            extract_engagement_terms.
        salary_raw: The staging-layer salary_raw column, passed through
            to extract_engagement_terms.

    Returns:
        The `ParsedSalary`.
    """
    terms = extract_engagement_terms(description, salary_raw)
    if terms.rate_annualised is None:
        return ParsedSalary(
            annualised_gbp=None,
            band=None,
            original_currency=terms.rate_currency,
            rate_basis=terms.rate_basis,
        )

    fx_rate = _APPROXIMATE_FX_TO_GBP.get(terms.rate_currency or "GBP", 1.0)
    annualised_gbp = terms.rate_annualised * fx_rate
    return ParsedSalary(
        annualised_gbp=annualised_gbp,
        band=_band(annualised_gbp),
        original_currency=terms.rate_currency,
        rate_basis=terms.rate_basis,
    )
