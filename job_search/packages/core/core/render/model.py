"""The rendering model: a TailoredDocument as a flat, ordered list of typed
blocks. This is the one place that fixes section order and heading names, so
the ATS .docx (18a) and the designed PDF (18b) cannot drift apart."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from core.cv.schema import Activity, Education
from core.render.format import AcronymExpander, format_date_range
from core.tailoring.schema import TailoredDocument

BlockKind = Literal[
    "name",
    "headline",
    "contact",
    "heading",
    "role_title",
    "role_meta",
    "project_title",
    "paragraph",
    "bullet",
]

HEADINGS = {
    "SUMMARY": "PROFESSIONAL SUMMARY",
    "SKILLS": "CORE TECHNICAL SKILLS",
    "EXPERIENCE": "WORK EXPERIENCE",
    "PROJECTS": "PERSONAL PROJECTS",
    "PUBLICATIONS": "PUBLICATIONS",
    "EDUCATION": "EDUCATION",
    "QUALIFICATIONS": "PROFESSIONAL QUALIFICATIONS & CONTINUOUS PERSONAL DEVELOPMENT",
    "ACTIVITIES": "ACTIVITIES & INTERESTS",
}
"""Section headings, word for word as in the user's own CV template."""

_INVISIBLE = frozenset("\u200b\ufeff\u2060\u00ad")
"""Zero-width and soft-hyphen characters: invisible, and no font draws them."""

_EXPANDED_KINDS = frozenset({"paragraph", "bullet"})
_TIDIED_KINDS = frozenset({"paragraph", "bullet", "role_meta"})
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?)])")
_SPACE_AFTER_BRACKET = re.compile(r"\(\s+")


@dataclass(frozen=True)
class Block:
    """One line of the rendered document.

    Attributes:
        kind: What the line is, so a renderer can style it.
        text: The line's text, whitespace-collapsed.
        muted: The tail of `text` a designed renderer may show in a muted
            colour (a role's dates); plain-text renderers ignore it.
        link: The tail of `text` that is a link (a project's URL), which a
            designed renderer may colour; plain-text renderers ignore it.
    """

    kind: BlockKind
    text: str
    muted: str = ""
    link: str = ""


@dataclass(frozen=True)
class RenderDoc:
    """A document ready to render.

    Attributes:
        title: The exact target job title (`title_for_display`).
        blocks: Every line, in reading order.
    """

    title: str
    blocks: tuple[Block, ...]

    def headings(self) -> list[str]:
        """List the section headings in order.

        Returns:
            The text of every `heading` block.
        """
        return [block.text for block in self.blocks if block.kind == "heading"]


def _clean(text: str) -> str:
    """Drop control characters and collapse whitespace.

    Args:
        text: Raw text, possibly pasted from a PDF or produced by an LLM.

    Returns:
        The text with control characters and invisible zero-width characters
        removed (Word cannot store the first, no font draws the second) and
        every whitespace run reduced to one space.
    """
    kept = "".join(
        ch
        for ch in text
        if ch not in _INVISIBLE and (ch.isspace() or unicodedata.category(ch) != "Cc")
    )
    return " ".join(kept.split())


def _tidy(text: str) -> str:
    """Remove spaces before closing punctuation and after an opening bracket.

    Args:
        text: Body text, already whitespace-collapsed.

    Returns:
        The text with `word ,` shown as `word,` and `( word )` as `(word)`.
    """
    text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)
    return _SPACE_AFTER_BRACKET.sub("(", text)


def _years(start: str | None, end: str | None) -> str:
    """Render a year range the way the CV template shows it in brackets.

    Args:
        start: The raw start date, if any.
        end: The raw end date, if any (`Present` means ongoing).

    Returns:
        `2016–2017`, `2020–present`, `2018`, or an empty string.
    """
    return (
        format_date_range(start, end, open_ended=False)
        .replace(" – ", "–")
        .replace("Present", "present")
    )


def _education_entry(edu: Education) -> str:
    """Format one education entry as `Qualification - Institution (years)`.

    Args:
        edu: The education entry.

    Returns:
        The entry; a grade the qualification does not already name is added
        to the qualification in brackets.
    """
    qualification = edu.qualification or ""
    if edu.grade and edu.grade.lower() not in qualification.lower():
        qualification = f"{qualification} ({edu.grade})".strip()
    head = " - ".join(part for part in (qualification, edu.institution) if part)
    years = _years(edu.start, edu.end)
    return f"{head} ({years})" if years else head


def _activity_entry(activity: Activity) -> str:
    """Format one activity as `Name at Organisation (years)`.

    Args:
        activity: The activity entry.

    Returns:
        The entry; the organisation and years are left out when missing.
    """
    head = (
        f"{activity.name} at {activity.organisation}"
        if activity.organisation
        else activity.name
    )
    years = _years(activity.start, activity.end)
    return f"{head} ({years})" if years else head


def build_render_doc(doc: TailoredDocument) -> RenderDoc:
    """Lay a tailored document out as ordered blocks.

    Args:
        doc: The approved tailored document.

    Returns:
        The render model. Acronyms are expanded on first use in body
        paragraphs and bullets only; the name, headline, contact line and
        role lines are copied untouched.

    Raises:
        ValueError: If the target title is blank once cleaned.
    """
    title = _clean(doc.target_title)
    if not title:
        raise ValueError("a rendered CV needs a non-blank target title")
    expander = AcronymExpander()
    blocks: list[Block] = []

    def add(
        kind: BlockKind,
        text: str,
        expand: bool = True,
        muted: str = "",
        link: str = "",
    ) -> None:
        """Append one block, cleaning the text and expanding acronyms.

        Args:
            kind: The block kind.
            text: Raw text; empty text adds nothing.
            expand: Whether first-use acronyms may be expanded in this
                block (still limited to paragraph and bullet kinds).
            muted: The tail of `text` to show muted (a role's dates).
            link: The tail of `text` that is a link (a project's URL).
        """
        text = _clean(text)
        if not text:
            return
        if kind in _TIDIED_KINDS:
            text = _tidy(text)
        if expand and kind in _EXPANDED_KINDS:
            text = expander.expand(text)
        muted, link = _clean(muted), _clean(link)
        blocks.append(
            Block(
                kind,
                text,
                muted if text.endswith(muted) else "",
                link if text.endswith(link) else "",
            )
        )

    def join(*parts: str | None, sep: str = ", ") -> str:
        """Join the non-empty parts.

        Args:
            *parts: Candidate parts, some possibly None or empty.
            sep: The separator.

        Returns:
            The non-empty parts joined by `sep`.
        """
        return sep.join(part for part in parts if part)

    add("name", doc.identity)
    add("headline", title)
    add(
        "contact",
        join(
            doc.email,
            doc.phone,
            doc.linkedin_url,
            doc.nationality,
            doc.work_auth,
            sep=" | ",
        ),
    )
    if doc.summary and doc.summary.text.strip():
        add("heading", HEADINGS["SUMMARY"])
        add("paragraph", doc.summary.text)
    if doc.skills:
        add("heading", HEADINGS["SKILLS"])
        # Not expanded: expansions contain commas and would corrupt the list.
        add("paragraph", join(*(skill.name for skill in doc.skills)), expand=False)
    if doc.experience:
        add("heading", HEADINGS["EXPERIENCE"])
        for role in doc.experience:
            add("role_title", role.title)
            dates = format_date_range(role.start, role.end)
            add("role_meta", join(role.company, dates), muted=dates)
            for bullet in role.bullets:
                add("bullet", bullet.text)
    if doc.projects:
        add("heading", HEADINGS["PROJECTS"])
        for project in doc.projects:
            add(
                "project_title",
                join(project.name, project.url, sep=": "),
                link=project.url or "",
            )
            for line in project.description.splitlines():
                add("bullet", line)
    if doc.publications:
        add("heading", HEADINGS["PUBLICATIONS"])
        for publication in doc.publications:
            add("paragraph", publication.citation)
            venue = join(publication.conference, str(publication.year or "") or None)
            add("paragraph", join(join(*publication.authors), venue, sep=" — "))
    if doc.education:
        add("heading", HEADINGS["EDUCATION"])
        add("paragraph", join(*(_education_entry(edu) for edu in doc.education)))
    if doc.qualifications:
        add("heading", HEADINGS["QUALIFICATIONS"])
        add(
            "paragraph",
            join(
                *(
                    f"{cert.name} ({cert.year})" if cert.year else cert.name
                    for cert in doc.qualifications
                )
            ),
        )
    if doc.activities:
        add("heading", HEADINGS["ACTIVITIES"])
        add("paragraph", join(*(_activity_entry(act) for act in doc.activities)))
    return RenderDoc(title=title, blocks=tuple(blocks))
