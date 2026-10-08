"""The rendering model: a TailoredDocument as a flat, ordered list of typed
blocks. This is the one place that fixes section order and heading names, so
the ATS .docx (18a) and the designed PDF (18b) cannot drift apart."""

from __future__ import annotations

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


def build_render_doc(doc: TailoredDocument) -> RenderDoc:
    """Lay a tailored document out as ordered blocks.

    Args:
        doc: The approved tailored document.

    Returns:
        The render model. Acronyms are expanded on first use in body
        paragraphs and bullets only; the name, headline, contact line and
        role lines are copied untouched.

    Raises:
        ValueError: If the target title is blank.
    """
    if not doc.target_title.strip():
        raise ValueError("a rendered CV needs a non-blank target title")
    expander = AcronymExpander()
    blocks: list[Block] = []

    def add(kind: BlockKind, text: str) -> None:
        """Append one block, collapsing whitespace and expanding acronyms.

        Args:
            kind: The block kind.
            text: Raw text; empty text adds nothing.
        """
        text = " ".join(text.split())
        if not text:
            return
        if kind in _EXPANDED_KINDS:
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
    add("headline", doc.target_title)
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
        add("heading", "Summary")
        add("paragraph", doc.summary.text)
    if doc.skills:
        add("heading", "Skills")
        add("paragraph", join(*(skill.name for skill in doc.skills)))
    if doc.experience:
        add("heading", "Experience")
        for role in doc.experience:
            add("role_title", role.title)
            add(
                "role_meta", join(role.company, format_date_range(role.start, role.end))
            )
            for bullet in role.bullets:
                add("bullet", bullet.text)
    if doc.education:
        add("heading", "Education")
        for edu in doc.education:
            add(
                "paragraph",
                join(
                    edu.qualification,
                    edu.institution,
                    format_date_range(edu.start, edu.end),
                    edu.grade,
                ),
            )
    if doc.projects:
        add("heading", "Projects")
        for project in doc.projects:
            add("paragraph", join(project.name, project.description, sep=": "))
    if doc.publications:
        add("heading", "Publications")
        for publication in doc.publications:
            add("paragraph", publication.citation)
    if doc.qualifications:
        add("heading", "Certifications")
        for cert in doc.qualifications:
            add("paragraph", f"{cert.name} ({cert.year})" if cert.year else cert.name)
    if doc.activities:
        add("heading", "Activities")
        for activity in doc.activities:
            add(
                "paragraph",
                join(
                    activity.name,
                    activity.organisation,
                    format_date_range(activity.start, activity.end),
                ),
            )
    return RenderDoc(title=doc.target_title, blocks=tuple(blocks))
