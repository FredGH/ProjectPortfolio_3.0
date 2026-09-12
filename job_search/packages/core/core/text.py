"""Shared text-rendering helpers for the Streamlit review pages."""

from __future__ import annotations

import html
import re


def readable_description(raw: str) -> str:
    """Render a posting's raw (HTML-entity-escaped HTML) description as
    plain, human-readable text for side-by-side review.

    Postings from ATS sources (e.g. Greenhouse) carry their description
    as HTML, and it's stored HTML-entity-escaped (literal `&lt;`,
    `&quot;`, ...) rather than as raw tags. This is a display-only
    transform for review pages — the stored `description` field is left
    untouched, since other consumers (survivorship, scoring chunking)
    may depend on its current form.

    Args:
        raw: A posting's raw description text as returned by the API.

    Returns:
        Plain text with HTML tags stripped and entities decoded,
        whitespace collapsed for readability.
    """
    unescaped = html.unescape(raw)
    without_tags = re.sub(r"<[^>]+>", " ", unescaped)
    without_tags = html.unescape(without_tags)
    collapsed = re.sub(r"[ \t]+", " ", without_tags)
    collapsed = re.sub(r"\n\s*\n+", "\n\n", collapsed)
    return collapsed.strip()
