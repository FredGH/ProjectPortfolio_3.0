"""Deterministic, stable bullet IDs (PLAN.md Step 13).

A bullet's ID is computed from its own text and its position within its
experience entry — never taken from an LLM's output. This is what lets
an ID "survive re-extraction" (PLAN.md's "Done when"): re-running
extraction on an unchanged CV reproduces the same IDs for unchanged
bullets, since both inputs to the hash are stable. A bullet whose text
changes gets a new ID by design — Step 17's fabrication guard should
treat edited text as a different claim, not the one it originally
checked.
"""

from __future__ import annotations

import hashlib
import re


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace, so formatting differences
    between extraction runs don't change a bullet's ID.

    Args:
        text: The raw bullet text.

    Returns:
        The normalized text.
    """
    return re.sub(r"\s+", " ", text.strip().lower())


def compute_bullet_id(experience_index: int, text: str) -> str:
    """Compute a stable ID for one bullet.

    Args:
        experience_index: The zero-based index of this bullet's
            experience entry within `CVTruthBase.experience`.
        text: The bullet's raw text.

    Returns:
        A 16-character hex digest, stable across re-extraction runs as
        long as `experience_index` and the bullet's (normalized) text
        are unchanged.
    """
    normalized = _normalize(text)
    digest = hashlib.sha256(f"{experience_index}:{normalized}".encode())
    return digest.hexdigest()[:16]
