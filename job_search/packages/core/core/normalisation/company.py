"""Company name normalisation for dedup blocking/matching (PLAN.md Step 6).

Matching-only: this is never the string shown to a user or in a
generated document — the raw company name from int_jobs__unioned is.
"""

from __future__ import annotations

import re

_SUFFIX_RE = re.compile(
    r"[,\s]+" r"(ltd|limited|inc|incorporated|gmbh|plc|s\.?a\.?|llc|llp)\.?\s*$",
    re.IGNORECASE,
)
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")
_WHITESPACE_RE = re.compile(r"\s+")

_ALIASES = {
    "facebook": "Meta",
    "alphabet": "Google",
    "alphabet inc": "Google",
}


def normalise_company(raw: str | None) -> str | None:
    """Normalise a company name for dedup matching.

    Strips common legal-entity suffixes (Ltd, Limited, Inc, GmbH, PLC,
    SA, LLC, LLP), a trailing parenthetical (e.g. "(UK)"), collapses
    whitespace, retitles an ALL-CAPS input, and resolves known aliases
    (Meta/Facebook, Google/Alphabet).

    Args:
        raw: The company name as stored in int_jobs__unioned. `None` when
            the source has no company for this row —
            int_jobs__unioned.company is nullable (manual entries with
            failed extraction).

    Returns:
        The normalised name, or `None` when `raw` is `None` — no company
        name is no signal, so it propagates as `None` rather than
        raising. Mixed-case input that isn't ALL-CAPS keeps its original
        casing (a deliberate brand stylisation is not "wrong casing" to
        fix).
    """
    if raw is None:
        return None

    name = _TRAILING_PAREN_RE.sub("", raw)
    # Suffix stripping can leave a fresh trailing parenthetical exposed in
    # principle (not observed in real data, but cheap to guard); loop
    # until stable rather than assuming one pass suffices.
    previous = None
    while previous != name:
        previous = name
        name = _SUFFIX_RE.sub("", name)
        name = _TRAILING_PAREN_RE.sub("", name)
    name = _WHITESPACE_RE.sub(" ", name).strip()

    alias = _ALIASES.get(name.lower())
    if alias:
        return alias

    if name.isupper():
        name = name.title()
    return name
