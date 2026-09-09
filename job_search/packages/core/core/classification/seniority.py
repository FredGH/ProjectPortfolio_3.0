"""Seniority-band derivation (PLAN.md Step 11a: "junior / mid / senior
/ lead / principal — from the same pass" as categorisation). Rules-only
— no embedding/LLM stage exists for this field, since PLAN.md never
describes one; the title's own seniority prefix is a strong, cheap
signal on its own.

Uses a fresh regex rather than reusing core.normalisation.title's
private `_SENIORITY_PREFIX_RE` (that pattern strips a *prefix only* and
is private to its module) — this module needs to match the term
anywhere in the title and map it to a band, a different job entirely,
even though the underlying keyword set intentionally mirrors it for
consistency (DECISIONS.md §5's three-title-fields design already
established which seniority words this project treats as significant).
"""

from __future__ import annotations

import re

_SENIORITY_TERMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bprincipal\b|\bstaff\+?\b", re.IGNORECASE), "principal"),
    (re.compile(r"\blead\b", re.IGNORECASE), "lead"),
    (re.compile(r"\bsenior\b|\bsr\.?\b", re.IGNORECASE), "senior"),
    (
        re.compile(r"\bjunior\b|\bjr\.?\b|\bgraduate\b|\bassociate\b", re.IGNORECASE),
        "junior",
    ),
]


def derive_seniority_band(title: str | None) -> str:
    """Derive a seniority band from a job title's own wording.

    Args:
        title: The job title to inspect (title_for_display or
            title_raw — title_for_display is preferred since it keeps
            seniority terms; never strip_title's output, which removes
            them).

    Returns:
        One of 'junior', 'mid', 'senior', 'lead', 'principal'. Defaults
        to 'mid' when the title is `None` or names no seniority term —
        "no signal" means the middle of the band, not a guess at either
        extreme.
    """
    if title is not None:
        for pattern, band in _SENIORITY_TERMS:
            if pattern.search(title):
                return band
    return "mid"
