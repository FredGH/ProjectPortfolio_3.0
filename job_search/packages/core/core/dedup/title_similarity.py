"""Pairwise title similarity via token-set ratio (PLAN.md Step 8).

The one Step 8 signal that's inherently pairwise, not precomputable per
job — rapidfuzz's token_set_ratio compares two strings' word sets
directly and has no per-job "feature" representation to store ahead of
time the way a SimHash fingerprint does.
"""

from __future__ import annotations

from rapidfuzz import fuzz


def title_token_set_ratio(title_a: str, title_b: str) -> float:
    """Compute token-set ratio similarity between two (matching) titles.

    Args:
        title_a: The first title — expected to already be
            core.normalisation.title.strip_title's output (matching
            form), not the raw display title.
        title_b: The second title, same expectation.

    Returns:
        A similarity score in [0.0, 1.0] (rapidfuzz's native 0-100
        scale, divided by 100).
    """
    return fuzz.token_set_ratio(title_a, title_b) / 100.0
