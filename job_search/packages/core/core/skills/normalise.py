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
_PAREN_GROUP_RE = re.compile(r"\([^()]*\)")

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


def candidate_forms(raw: str) -> list[str]:
    """List the keys to look a skill string up under, most specific first.

    `normalise_skill` is deliberately left alone: its output is a persisted
    key (`skill_mapping`, `skill_alias`, `esco.skill_label`, `job_skill_raw`),
    so changing it would mean re-keying all of them. Instead the mapper tries
    these forms in order. Only a parenthetical qualifier is dropped —
    "MySQL (RDS)" -> "mysql" — so a genuine slash term like "CI/CD" stays whole;
    splitting a list such as "TypeScript/React" into several skills needs a
    schema change and is not done here.

    Args:
        raw: The skill string as it appeared in a CV or JD.

    Returns:
        `[whole, head]`: `whole` is exactly `normalise_skill(raw)` (the
        persisted key, whose trailing ")" is stripped); `head` is `whole` with
        every parenthetical qualifier removed (an unclosed one runs to the
        end), present only when it differs from `whole` and is not empty.
        Empty if nothing meaningful remains.
    """
    whole = normalise_skill(raw)
    if not whole:
        return []
    stripped = whole
    previous = None
    while previous != stripped:
        previous = stripped
        stripped = _PAREN_GROUP_RE.sub(" ", stripped)
    head = normalise_skill(stripped.split("(", 1)[0])
    return [whole, head] if head and head != whole else [whole]


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
