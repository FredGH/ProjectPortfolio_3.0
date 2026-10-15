"""Parse the writer's reply and assemble a document (PLAN.md Step 19).

The writer returns sentences with a kind and sources. Code adds everything
that must be exact (the title lines, the date, the sign-off), cleans the text
and marks a claim with no valid source as an orphan.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from pydantic import BaseModel, Field, ValidationError

from core.artefacts.context import EvidencePack
from core.artefacts.schema import (
    ROLES,
    CoverLetter,
    Kind,
    Paragraph,
    Pitch,
    PitchVariant,
    Sentence,
)
from core.llm.json_response import parse_json_response
from core.tailoring.assemble import clean_text


class WriterError(ValueError):
    """The writer's reply could not be used."""


class WriterSentence(BaseModel):
    """One sentence as the writer returns it.

    Attributes:
        text: The sentence.
        kind: `claim` or `voice` (anything else is treated as a claim).
        sources: The ids it cites.
    """

    text: str
    kind: str = "claim"
    sources: list[str] = Field(default_factory=list)


class WriterParagraph(BaseModel):
    """One paragraph as the writer returns it.

    Attributes:
        role: The paragraph's role.
        sentences: Its sentences.
    """

    role: str
    sentences: list[WriterSentence]


class LetterReply(BaseModel):
    """The writer's reply for a letter.

    Attributes:
        paragraphs: The paragraphs.
    """

    paragraphs: list[WriterParagraph] = Field(min_length=1)


class PitchReply(BaseModel):
    """The writer's reply for a pitch.

    Attributes:
        variants: Sentences by `"30"`, `"60"`, `"90"`.
    """

    variants: dict[str, list[WriterSentence]]


def parse_writer_reply(text: str, kind: Kind) -> LetterReply | PitchReply:
    """Read the writer's JSON reply.

    Args:
        text: The raw reply.
        kind: `cover_letter` or `pitch`.

    Returns:
        The validated reply.

    Raises:
        WriterError: If the reply is not JSON of the expected shape.
    """
    try:
        raw = parse_json_response(text.strip())
        if kind == "cover_letter":
            return LetterReply.model_validate(raw)
        reply = PitchReply.model_validate(raw)
    except (json.JSONDecodeError, ValueError, ValidationError, AttributeError) as exc:
        raise WriterError(f"unusable writer reply: {exc}") from exc
    if not any(reply.variants.get(k) for k in ("30", "60", "90")):
        raise WriterError("unusable writer reply: no pitch variants")
    return reply


def sentence_id(section: str, index: int, text: str) -> str:
    """Build a sentence's stable id.

    Args:
        section: The paragraph role or `v<seconds>`.
        index: The sentence's position in its section.
        text: The sentence text.

    Returns:
        A short hash of the three.
    """
    return hashlib.sha1(f"{section}:{index}:{text}".encode()).hexdigest()[:10]


def _sentence(
    raw: WriterSentence, pack: EvidencePack, section: str, index: int
) -> Sentence | None:
    """Turn one writer sentence into a document sentence.

    Args:
        raw: The writer's sentence.
        pack: The evidence pack.
        section: The section it sits in.
        index: Its position in that section.

    Returns:
        The sentence, or None when its text is empty after cleaning.
    """
    text = clean_text(raw.text)
    if not text:
        return None
    voice = raw.kind == "voice"
    sources = (
        []
        if voice
        else list(dict.fromkeys(s.strip() for s in raw.sources if s.strip()))
    )
    orphan = not voice and not any(s in pack.items for s in sources)
    return Sentence(
        id=sentence_id(section, index, text),
        text=text,
        kind="voice" if voice else "claim",
        sources=sources,
        flag="orphan" if orphan else "",
    )


def _composed(section: str, text: str) -> Sentence:
    """Build a code-composed sentence.

    Args:
        section: The section it opens.
        text: Its text.

    Returns:
        A `composed` sentence with a stable id.
    """
    return Sentence(id=sentence_id(section, 0, text), text=text, kind="composed")


def _at_company(pack: EvidencePack) -> str:
    """Return ` at <company>` or an empty string.

    Args:
        pack: The evidence pack.

    Returns:
        The clause naming the employer, if one is known.
    """
    return f" at {pack.company}" if pack.company else ""


def build_letter(pack: EvidencePack, reply: LetterReply, *, today: date) -> CoverLetter:
    """Assemble a cover letter.

    Args:
        pack: The evidence pack.
        reply: The writer's reply.
        today: The date shown on the letter.

    Returns:
        The letter: the model's paragraphs (first occurrence of each role, in
        the fixed order) with the title lines composed by code.
    """
    by_role: dict[str, WriterParagraph] = {}
    for paragraph in reply.paragraphs:
        by_role.setdefault(paragraph.role, paragraph)
    paragraphs: list[Paragraph] = []
    for role in ROLES:
        written = by_role.get(role)
        sentences: list[Sentence] = []
        if role == "opening":
            sentences.append(
                _composed(
                    role,
                    f"I am applying for the {pack.title} role{_at_company(pack)}.",
                )
            )
        if written is not None:
            for index, raw in enumerate(written.sentences, start=len(sentences)):
                built = _sentence(raw, pack, role, index)
                if built is not None:
                    sentences.append(built)
        if written is not None or role == "opening":
            paragraphs.append(Paragraph(role=role, sentences=sentences))  # type: ignore[arg-type]
    return CoverLetter(
        sender=pack.identity,
        contact=pack.contact,
        date=f"{today.day} {today.strftime('%B %Y')}",
        recipient=f"Hiring team, {pack.company}" if pack.company else "Hiring team",
        reference=f"Re: {pack.title}",
        target_title=pack.title,
        company=pack.company,
        paragraphs=paragraphs,
        sign_off=f"Kind regards,\n{pack.identity}",
    )


def build_pitch(pack: EvidencePack, reply: PitchReply) -> Pitch:
    """Assemble an elevator pitch.

    Args:
        pack: The evidence pack.
        reply: The writer's reply.

    Returns:
        The pitch: three variants, each opened by a code-composed sentence.
    """
    variants: list[PitchVariant] = []
    for seconds in (30, 60, 90):
        section = f"v{seconds}"
        sentences = [
            _composed(
                section,
                f"I'm {pack.identity}, and I'm interested in the {pack.title} "
                f"role{_at_company(pack)}.",
            )
        ]
        for raw in reply.variants.get(str(seconds), []):
            built = _sentence(raw, pack, section, len(sentences))
            if built is not None:
                sentences.append(built)
        variants.append(PitchVariant(seconds=seconds, sentences=sentences))  # type: ignore[arg-type]
    return Pitch(
        sender=pack.identity,
        target_title=pack.title,
        company=pack.company,
        variants=variants,
    )
