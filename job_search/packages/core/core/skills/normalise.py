"""Canonical string form for skill names (PLAN.md Step 14).

Every skill string — a CV skill, a JD-extracted skill, an ESCO label, an
alias — is reduced to this form before any comparison, so "Google  Cloud
Platform" and "google cloud platform" are the same key. Deliberately no
stemming or stop-word removal: "Java" and "JavaScript" must stay
distinct, and "Google Cloud" vs "Google Cloud Platform" is handled by
alias entries, not fuzzy string rules.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")

# Punctuation stripped from both ends. "." is NOT here (a leading "." is
# part of ".NET"); a trailing "." is stripped separately below. "+" and
# "#" are never stripped ("C++", "C#").
_EDGE_CHARS = " ,;:!?\"'()[]{}<>-–—/\\|*•·"

MAX_SKILL_CHARS = 100
"""Longest plausible skill name, in characters.

A local 8B model sometimes answers with a sentence ("experience with
distributed systems at scale in a fast-paced environment") rather than a
skill. Such a string is nobody's skill, floods the review list and costs
an embedding call; past ~2.7 KB it also overflows the `job_skill_raw`
primary-key index and raises an uncaught `OperationalError`. The longest
real ESCO preferred label is far below this bound."""

MAX_SKILL_WORDS = 8
"""Most whitespace-separated words a plausible skill name has.

Catches a short-but-still-sentence answer that slips under
`MAX_SKILL_CHARS`."""


def normalise_skill(raw: str) -> str:
    """Reduce a skill string to its canonical comparison key.

    Args:
        raw: The skill string as it appeared in a CV, JD, or ESCO label.

    Returns:
        The NFKC-normalised, lowercased string with "&" expanded to
        "and", whitespace collapsed, and surrounding punctuation
        removed. Empty if nothing meaningful remains.
    """
    text = unicodedata.normalize("NFKC", raw).lower().replace("&", " and ")
    text = _WHITESPACE_RE.sub(" ", text).strip(_EDGE_CHARS)
    return text.rstrip(".").strip(_EDGE_CHARS)


def is_plausible_skill(normalised: str) -> bool:
    """Decide whether a normalised string can be a skill name at all.

    A cheap sanity bound, not a taxonomy check: it only rejects what no
    skill name ever looks like, so a real skill is never dropped.

    Args:
        normalised: A string already through `normalise_skill`.

    Returns:
        True if the string is non-empty and within both
        `MAX_SKILL_CHARS` and `MAX_SKILL_WORDS`.
    """
    if not normalised:
        return False
    return (
        len(normalised) <= MAX_SKILL_CHARS
        and len(normalised.split()) <= MAX_SKILL_WORDS
    )
