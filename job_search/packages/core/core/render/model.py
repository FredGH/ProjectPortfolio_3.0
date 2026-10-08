"""The rendering model: a TailoredDocument as a flat, ordered list of typed
blocks. This is the one place that fixes section order and heading names, so
the ATS .docx (18a) and the designed PDF (18b) cannot drift apart."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from core.render.format import AcronymExpander, format_date_range
from core.tailoring.schema import TailoredDocument

BlockKind = Literal[
    "name",
    "headline",
    "contact",
    "heading",
    "role_title",
    "role_meta",
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


@dataclass(frozen=True)
class Block:
    """One line of the rendered document.

    Attributes:
        kind: What the line is, so a renderer can style it.
        text: The line's text, whitespace-collapsed.
    """

    kind: BlockKind
    text: str


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

    def add(kind: BlockKind, text: str, expand: bool = True) -> None:
        """Append one block, cleaning the text and expanding acronyms.

        Args:
            kind: The block kind.
            text: Raw text; empty text adds nothing.
            expand: Whether first-use acronyms may be expanded in this
                block (still limited to paragraph and bullet kinds).
        """
        text = _clean(text)
        if not text:
            return
        if expand and kind in _EXPANDED_KINDS:
            text = expander.expand(text)
        blocks.append(Block(kind, text))

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
            join(*doc.locations),
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
            add(
                "role_meta", join(role.company, format_date_range(role.start, role.end))
            )
            for bullet in role.bullets:
                add("bullet", bullet.text)
    if doc.projects:
        add("heading", HEADINGS["PROJECTS"])
        for project in doc.projects:
            add("paragraph", join(project.name, project.description, sep=": "))
    if doc.publications:
        add("heading", HEADINGS["PUBLICATIONS"])
        for publication in doc.publications:
            add("paragraph", publication.citation)
    if doc.education:
        add("heading", HEADINGS["EDUCATION"])
        for edu in doc.education:
            add(
                "paragraph",
                join(
                    edu.qualification,
                    edu.institution,
                    format_date_range(edu.start, edu.end, open_ended=False),
                    edu.grade,
                ),
            )
    if doc.qualifications:
        add("heading", HEADINGS["QUALIFICATIONS"])
        for cert in doc.qualifications:
            add("paragraph", f"{cert.name} ({cert.year})" if cert.year else cert.name)
    if doc.activities:
        add("heading", HEADINGS["ACTIVITIES"])
        for activity in doc.activities:
            add(
                "paragraph",
                join(
                    activity.name,
                    activity.organisation,
                    format_date_range(activity.start, activity.end, open_ended=False),
                ),
            )
    return RenderDoc(title=title, blocks=tuple(blocks))
