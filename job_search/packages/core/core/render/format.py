"""Pure text rules for rendering a CV (Step 18): dates, acronym expansion on
first use, and the output filename."""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})$")
_MAX_NAME_CHARS = 150
_OPEN_END = frozenset({"present", "current", "now", "ongoing", "to date"})
_MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ],
        start=1,
    )
}

ACRONYMS = {
    "ELT": "Extract, Load, Transform",
    "ETL": "Extract, Transform, Load",
    "GCP": "Google Cloud Platform",
    "AWS": "Amazon Web Services",
    "CI/CD": "Continuous Integration/Continuous Delivery",
    "API": "Application Programming Interface",
    "NLP": "Natural Language Processing",
    "ML": "Machine Learning",
}
"""The only acronyms ever expanded. Deliberately small and curated."""


def _format_date(value: str) -> str:
    """Render one date as `MM/YYYY`, `YYYY` or `Present`.

    Args:
        value: A raw truth-base date such as `2019-01`, `2019`, `March 2024`
            or `Present`.

    Returns:
        The normalised date; an unrecognised value is returned unchanged
        (and logged) rather than guessed at.
    """
    text = value.strip()
    if text.lower() in _OPEN_END:
        return "Present"
    match = _YEAR_MONTH_RE.match(text)
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(2)}/{match.group(1)}"
    if _YEAR_RE.match(text):
        return text
    match = _MONTH_YEAR_RE.match(text)
    if match:
        month = _MONTHS.get(match.group(1)[:3].lower())
        if month:
            return f"{month:02d}/{match.group(2)}"
    logger.warning("date %r is not in a known format; kept verbatim", value)
    return text


def format_date_range(
    start: str | None, end: str | None, *, open_ended: bool = True
) -> str:
    """Render a role's dates as `MM/YYYY – MM/YYYY`.

    Args:
        start: The raw start date, if any.
        end: The raw end date.
        open_ended: Whether a missing end means "still ongoing". True for a
            role; False for a degree or activity, where a missing end only
            means the end is not stated.

    Returns:
        The range with an en dash; `Present` for a missing end when
        `open_ended`; only the start (or only the end) when the other is
        absent; an empty string when there are no dates.
    """
    if not start and not end:
        return ""
    if end:
        right = _format_date(end)
    else:
        right = "Present" if open_ended else ""
    if not start:
        return right
    if not right:
        return _format_date(start)
    return f"{_format_date(start)} – {right}"


class AcronymExpander:
    """Expand each known acronym once, on its first use in the document.

    Attributes:
        _acronyms: Acronym to expansion.
        _seen: Acronyms already met, expanded or not.
        _patterns: One whole-token regex per acronym.
    """

    def __init__(self, acronyms: dict[str, str] | None = None) -> None:
        """Create an expander with an empty first-use memory.

        Args:
            acronyms: Acronym to expansion; defaults to `ACRONYMS`.
        """
        self._acronyms = dict(ACRONYMS if acronyms is None else acronyms)
        self._seen: set[str] = set()
        self._patterns = {
            acronym: re.compile(rf"(?<![\w/]){re.escape(acronym)}(?![\w/])")
            for acronym in self._acronyms
        }

    def expand(self, text: str) -> str:
        """Expand acronyms in `text` that have not been seen before.

        An acronym already followed by its own expansion is left as written
        and counts as seen. An acronym inside a longer token (`dbt/ELT`,
        `APIs`) is not an acronym use and is ignored.

        Args:
            text: One block of document text, in document order.

        Returns:
            The text with first-use acronyms expanded.
        """
        for acronym, expansion in self._acronyms.items():
            if acronym in self._seen:
                continue
            match = self._patterns[acronym].search(text)
            if match is None:
                continue
            self._seen.add(acronym)
            after = text[match.end() :]
            if re.match(rf"\s*\(\s*{re.escape(expansion)}", after, re.IGNORECASE):
                continue
            text = f"{text[: match.end()]} ({expansion}){after}"
        return text


def _slug(value: str) -> str:
    """Turn free text into a filename-safe `Words_Joined_By_Underscores`.

    Args:
        value: Any text.

    Returns:
        ASCII letters and digits joined by single underscores.
    """
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", folded).strip("_")


def build_filename(
    identity: str, title: str, company: str | None, extension: str
) -> str:
    """Build `<surname>_<title>_<company>.<extension>`.

    Args:
        identity: The candidate's full name; the surname is its last word.
        title: The target job title (`title_for_display`).
        company: The employer, or None to leave it out.
        extension: File extension without the dot.

    Returns:
        A filename safe on any filesystem: empty parts are dropped, the
        name is cut to a safe length, and `CV` stands in when nothing
        Latin is left (so the file is never a hidden `.docx`).
    """
    surname = identity.split()[-1] if identity.split() else ""
    parts = [_slug(part) for part in (surname, title, company or "")]
    base = "_".join(part for part in parts if part)[:_MAX_NAME_CHARS].rstrip("_")
    return f"{base or 'CV'}.{extension}"
