"""Job-title normalisation: three fields, three purposes (DECISIONS.md
§5, PLAN.md Step 6).

title_raw is always verbatim. strip_title is for dedup matching ONLY —
it deliberately removes seniority, which is exactly why it must never be
reused for a generated document. title_for_display strips only
decoration and keeps seniority/qualifiers; it's the string every
generated CV/cover-letter headline uses.

Scope note — trailing free-form LOCATION suffixes are NOT stripped.
The backlog subtask and PLAN.md Step 6 name "location suffixes"
alongside req IDs, (m/f/d) and pipe-suffixes, but only the work-mode
suffix ("- Remote"/"- Hybrid"/"- Onsite") is removed here. Stripping an
actual place name ("- Manchester", ", London") reliably needs a location
gazetteer: without one, the same "- <words>" shape also covers
legitimate qualifiers ("Software Engineer - Data Engineering",
"Senior Network Planner - Occupancy" — a real bronze title), so a
generic rule would silently mangle real titles. This is a deliberately
smaller, safer scope than the backlog's literal wording, in the same
spirit as this module's other rules: over-stripping a title corrupts
both the dedup key and every generated document that quotes it, while
leaving a location in place merely makes two variants of one posting
slightly less likely to match.
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


def title_raw(raw: str | None) -> str | None:
    """Return the source title, verbatim, always.

    Args:
        raw: The posting's title as stored in int_jobs__unioned. `None`
            when the source has no title for this row —
            int_jobs__unioned.title is nullable (manual entries with
            failed extraction).

    Returns:
        `raw`, unchanged — including `None`, which propagates rather than
        raising. Exists so callers never have to remember whether "the
        raw title" means skipping normalisation entirely — it's a named
        function with the same contract as its siblings.
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


def strip_title(raw: str | None) -> str | None:
    """Strip a title down to its matching-only form.

    Removes seniority prefixes on top of everything `title_for_display`
    removes. **Never use this output in a generated document** — see
    DECISIONS.md §5.

    Args:
        raw: The source title, or `None` when the row has no title
            (int_jobs__unioned.title is nullable).

    Returns:
        The stripped title, for dedup matching only, or `None` when
        `raw` is `None` — no title is no signal, so it propagates as
        `None` rather than raising.
    """
    if raw is None:
        return None

    text = _strip_decoration(raw)
    previous = None
    while previous != text:
        previous = text
        text = _SENIORITY_PREFIX_RE.sub("", text)
    return text.strip()


def title_for_display(raw: str | None) -> str | None:
    """Strip only decoration, keeping seniority and qualifiers.

    This is the string every generated CV/cover-letter/elevator-pitch
    uses (DECISIONS.md §5) — never `strip_title`'s output.

    Args:
        raw: The source title, or `None` when the row has no title
            (int_jobs__unioned.title is nullable).

    Returns:
        The decoration-stripped title, safe for generated documents, or
        `None` when `raw` is `None` — no title is no signal, so it
        propagates as `None` rather than raising. A caller rendering a
        document is expected to handle that `None` explicitly rather
        than printing an empty headline.
    """
    if raw is None:
        return None

    return _strip_decoration(raw)
