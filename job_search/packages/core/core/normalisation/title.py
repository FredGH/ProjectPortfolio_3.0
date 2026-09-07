"""Job-title normalisation: three fields, three purposes (DECISIONS.md
§5, PLAN.md Step 6).

title_raw is always verbatim. strip_title is for dedup matching ONLY —
it deliberately removes seniority, which is exactly why it must never be
reused for a generated document. title_for_display strips only
decoration and keeps seniority/qualifiers; it's the string every
generated CV/cover-letter headline uses.
"""

from __future__ import annotations

import re

_SENIORITY_PREFIX_RE = re.compile(
    r"^\s*(senior|sr\.?|junior|jr\.?|lead|principal|staff\+?|associate|"
    r"graduate)\s+",
    re.IGNORECASE,
)
_MFD_RE = re.compile(r"\s*\(m/f/d\)\s*", re.IGNORECASE)
_REQ_ID_RE = re.compile(r"\s*\(?\s*req(?:\s*id)?[\s\-#:]*\d+\s*\)?", re.IGNORECASE)
_REMOTE_SUFFIX_RE = re.compile(
    r"\s*-\s*(remote|hybrid|onsite)(?:/\w+)?\s*$", re.IGNORECASE
)
_PIPE_SUFFIX_RE = re.compile(r"\s*\|.*$")
_WHITESPACE_RE = re.compile(r"\s+")


def title_raw(raw: str) -> str:
    """Return the source title, verbatim, always.

    Args:
        raw: The posting's title as stored in int_jobs__unioned.

    Returns:
        `raw`, unchanged. Exists so callers never have to remember
        whether "the raw title" means skipping normalisation entirely —
        it's a named function with the same contract as its siblings.
    """
    return raw


def _strip_decoration(raw: str) -> str:
    """Remove decoration common to both strip_title and title_for_display.

    Args:
        raw: The source title.

    Returns:
        `raw` with (m/f/d), req IDs, trailing "| ..." segments and a
        trailing "- Remote"/"- Hybrid"/"- Onsite" suffix removed, and
        whitespace collapsed.
    """
    text = _MFD_RE.sub(" ", raw)
    text = _REQ_ID_RE.sub(" ", text)
    text = _PIPE_SUFFIX_RE.sub("", text)
    text = _REMOTE_SUFFIX_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def strip_title(raw: str) -> str:
    """Strip a title down to its matching-only form.

    Removes seniority prefixes on top of everything `title_for_display`
    removes. **Never use this output in a generated document** — see
    DECISIONS.md §5.

    Args:
        raw: The source title.

    Returns:
        The stripped title, for dedup matching only.
    """
    text = _strip_decoration(raw)
    previous = None
    while previous != text:
        previous = text
        text = _SENIORITY_PREFIX_RE.sub("", text)
    return text.strip()


def title_for_display(raw: str) -> str:
    """Strip only decoration, keeping seniority and qualifiers.

    This is the string every generated CV/cover-letter/elevator-pitch
    uses (DECISIONS.md §5) — never `strip_title`'s output.

    Args:
        raw: The source title.

    Returns:
        The decoration-stripped title, safe for generated documents.
    """
    return _strip_decoration(raw)
