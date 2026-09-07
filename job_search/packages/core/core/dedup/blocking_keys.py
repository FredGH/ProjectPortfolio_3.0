"""Blocking-key and content-hash computation for dedup candidate
generation (PLAN.md Step 7).

Reuses Step 6's core.normalisation functions rather than re-deriving
company/title/location normalisation — the entire point of Step 6
existing first. Every field here is computed once per job and stored
(core.dedup.write_blocking_keys), then read by dbt's self-join models —
the same "Python computes, dbt joins" split Step 5a established, since
these normalisation functions are pure Python, not SQL.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from core.normalisation.company import normalise_company
from core.normalisation.location import normalise_location
from core.normalisation.title import strip_title

_WHITESPACE_RE = re.compile(r"\s+")

_BLOCK_KEY_TITLE_PREFIX_LEN = 12
"""PLAN.md Step 7: 'normalise_company || left(strip_title, 12) || country'."""

_CONTENT_HASH_DESCRIPTION_LEN = 1000
"""PLAN.md Step 7: 'left(normalise(description), 1000)'."""


@dataclass(frozen=True)
class BlockingKeyResult:
    """One job's blocking-relevant fields (PLAN.md Step 7).

    Attributes:
        normalised_company: Step 6's normalise_company output (empty
            string if company was None).
        matching_title: Step 6's strip_title output, full length (not
            truncated) — Step 8 reuses this for title similarity, so the
            truncation to 12 chars happens only when building block_key,
            not here.
        country_iso: Step 6's normalise_location country, or None when
            unresolved.
        region: Step 6's normalise_location region, or None.
        block_key: normalised_company + left(matching_title, 12) +
            country_iso, concatenated with '|' separators so that e.g.
            company "AB" + title "C..." can never collide with company
            "A" + title "BC..." the way bare concatenation could.
        content_sha256: SHA-256 hex digest over normalised
            company|title|location|description(left 1000 chars) — an
            exact-duplicate check, deliberately fragile to any real
            difference (including different truncation points between
            sources), unlike the fuzzy signals Step 8 adds.
    """

    normalised_company: str
    matching_title: str
    country_iso: str | None
    region: str | None
    block_key: str
    content_sha256: str


def _normalise_text(raw: str | None) -> str:
    """Lowercase and collapse whitespace — the minimal text
    normalisation content_sha256 needs. Not one of Step 6's named
    functions (Step 6 never built a normalise_description — there's no
    dedup-matching structure to a free-text description beyond casing/
    whitespace), so this stays local to Step 7.

    Args:
        raw: Any free text, or None.

    Returns:
        The lowercased, whitespace-collapsed text, or "" if raw is None.
    """
    if raw is None:
        return ""
    return _WHITESPACE_RE.sub(" ", raw.strip().lower())


def compute_blocking_key(
    company: str | None,
    title: str | None,
    location: str | None,
    description: str | None,
) -> BlockingKeyResult:
    """Compute one job's blocking key and content hash.

    Args:
        company: The posting's company name, or None.
        title: The posting's title, or None.
        location: The posting's location string, or None.
        description: The posting's description text, or None.

    Returns:
        The `BlockingKeyResult`.
    """
    normalised_company = normalise_company(company) or ""
    matching_title = strip_title(title) or ""
    normalised_location = normalise_location(location)
    country_iso = normalised_location.country_iso
    region = normalised_location.region

    block_key = "|".join(
        [
            normalised_company,
            matching_title[:_BLOCK_KEY_TITLE_PREFIX_LEN],
            country_iso or "",
        ]
    )

    content_parts = "|".join(
        [
            _normalise_text(normalised_company),
            _normalise_text(matching_title),
            _normalise_text(f"{country_iso}:{region}"),
            _normalise_text(description)[:_CONTENT_HASH_DESCRIPTION_LEN],
        ]
    )
    content_sha256 = hashlib.sha256(content_parts.encode("utf-8")).hexdigest()

    return BlockingKeyResult(
        normalised_company=normalised_company,
        matching_title=matching_title,
        country_iso=country_iso,
        region=region,
        block_key=block_key,
        content_sha256=content_sha256,
    )
