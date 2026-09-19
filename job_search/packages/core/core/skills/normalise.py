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
