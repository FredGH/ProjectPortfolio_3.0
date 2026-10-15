"""The document model of a cover letter and a pitch (PLAN.md Step 19).

A document is a list of sentences, each with a kind and the sources it rests
on. Sentences written by code (the title lines) are `composed`: they are never
judged and cannot be removed.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["cover_letter", "pitch"]
SentenceKind = Literal["claim", "voice", "composed"]
Flag = Literal["", "orphan", "unsupported", "linked"]
Role = Literal["opening", "why_company", "evidence", "close"]

ROLES: tuple[str, ...] = ("opening", "why_company", "evidence", "close")
LETTER_WORDS = (200, 350)
PITCH_WORDS = {30: (60, 90), 60: (120, 180), 90: (180, 270)}
VOICE_MAX_WORDS = 25

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")


class Sentence(BaseModel):
    """One sentence of a document.

    Attributes:
        id: Stable id (a hash of section, position and text), so a decision
            finds the sentence even after another one is removed.
        text: The sentence.
        kind: `claim`, `voice` or `composed` (written by code).
        sources: The evidence ids a claim rests on.
        flag: `orphan` (no valid source), `unsupported` (the critic or a code
            check rejected it), `linked` (the user attached a source) or "".
    """

    id: str
    text: str
    kind: SentenceKind
    sources: list[str] = Field(default_factory=list)
    flag: Flag = ""


class Paragraph(BaseModel):
    """One paragraph of a letter.

    Attributes:
        role: `opening`, `why_company`, `evidence` or `close`.
        sentences: Its sentences, in order.
    """

    role: Role
    sentences: list[Sentence]


class CoverLetter(BaseModel):
    """A cover letter.

    Attributes:
        sender: The candidate's name.
        contact: The candidate's contact line.
        date: The generation date, as shown.
        recipient: `Hiring team, <company>`.
        reference: `Re: <title_for_display>`.
        target_title: The exact `title_for_display`.
        company: The employer.
        paragraphs: The body.
        sign_off: The closing lines.
    """

    sender: str
    contact: str
    date: str
    recipient: str
    reference: str
    target_title: str
    company: str
    paragraphs: list[Paragraph]
    sign_off: str


class PitchVariant(BaseModel):
    """One spoken length of the pitch.

    Attributes:
        seconds: 30, 60 or 90.
        sentences: Its sentences, the first composed by code.
    """

    seconds: Literal[30, 60, 90]
    sentences: list[Sentence]


class Pitch(BaseModel):
    """An elevator pitch in three lengths.

    Attributes:
        sender: The candidate's name.
        target_title: The exact `title_for_display`.
        company: The employer.
        variants: The 30, 60 and 90 second versions.
    """

    sender: str
    target_title: str
    company: str
    variants: list[PitchVariant]


def word_count(text: str) -> int:
    """Count the words in a text.

    Args:
        text: Any text.

    Returns:
        The number of words (punctuation does not count).
    """
    return len(_WORD.findall(text))


def paragraph_text(paragraph: Paragraph) -> str:
    """Join a paragraph's sentences.

    Args:
        paragraph: The paragraph.

    Returns:
        Its sentences separated by a space.
    """
    return " ".join(s.text for s in paragraph.sentences)


def letter_body_text(letter: CoverLetter) -> str:
    """Render a letter's paragraphs.

    Args:
        letter: The letter.

    Returns:
        The paragraphs separated by a blank line.
    """
    return "\n\n".join(paragraph_text(p) for p in letter.paragraphs)


def letter_text(letter: CoverLetter) -> str:
    """Render the whole letter as plain text.

    Args:
        letter: The letter.

    Returns:
        Header lines, the body and the sign-off, as the `.txt` twin shows it.
    """
    header = [
        letter.sender,
        letter.contact,
        letter.date,
        letter.recipient,
        letter.reference,
    ]
    return (
        "\n".join(header)
        + "\n\n"
        + letter_body_text(letter)
        + "\n\n"
        + letter.sign_off
        + "\n"
    )


def variant_text(variant: PitchVariant) -> str:
    """Join a pitch variant's sentences.

    Args:
        variant: The variant.

    Returns:
        Its sentences separated by a space.
    """
    return " ".join(s.text for s in variant.sentences)


def pitch_text(pitch: Pitch) -> str:
    """Render the pitch as a plain-text script.

    Args:
        pitch: The pitch.

    Returns:
        Each variant under a `<n> seconds` heading, with a trailing newline.
    """
    blocks = [f"{v.seconds} seconds\n{variant_text(v)}" for v in pitch.variants]
    return "\n\n".join(blocks) + "\n"
