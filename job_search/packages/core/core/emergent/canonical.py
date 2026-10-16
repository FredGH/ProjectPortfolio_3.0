"""Canonical title extraction (PLAN.md Step 21b).

Beyond Step 6's `strip_title` (a dedup aid): reduce a title to the phrase that
names the function, so "Senior ML Engineer - London (Hybrid)" and "ML Engineer"
count as one title, and drop marketing titles ("Code Ninja") entirely.
"""

from __future__ import annotations

import re
import unicodedata

from core.emergent.config import EmergentConfig

_SEPARATORS = re.compile(r"\s[-–—|:/]\s|[,|(\[]")
_GENDER = re.compile(r"\(?\b[mfwdx]\s*/\s*[mfwdx](\s*/\s*[mfwdx])?\b\)?", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9+#&.\- ]")


def is_stoplisted(raw: str, config: EmergentConfig) -> bool:
    """Say whether a title uses title-inflation wording.

    Args:
        raw: The raw title.
        config: The emergent config (its stoplist).

    Returns:
        True when any stoplist word or phrase appears as a whole word.
    """
    low = raw.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in config.stoplist)


def canonicalise(
    raw: str | None, config: EmergentConfig, company: str | None = None
) -> str | None:
    """Reduce a raw title to its canonical function phrase.

    Args:
        raw: The posting's title.
        config: The emergent config (level words, stoplist).
        company: The employer, so its name inside the title is removed.

    Returns:
        The lowercased phrase with level words, gender markers, employer name
        and everything after a separator (team, location, work model) removed;
        None when the title is empty, stoplisted, or nothing meaningful is left.
    """
    if not raw or not raw.strip() or is_stoplisted(raw, config):
        return None
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    text = _GENDER.sub(" ", text)
    head = _SEPARATORS.split(text.strip(" -–—|:/,"), maxsplit=1)[0]
    head = head.lower()
    if company:
        head = re.sub(rf"\b{re.escape(company.lower())}\b", " ", head)
    head = _NON_WORD.sub(" ", head)
    words = [w for w in head.split() if w not in config.level_words]
    phrase = " ".join(words).strip(" .-")
    if len(phrase) < 2 or not re.search(r"[a-z]", phrase):
        return None
    return phrase
