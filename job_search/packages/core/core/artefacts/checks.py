"""Code-only checks of a letter or pitch (PLAN.md Step 19).

Everything checkable without a model lives here; the critic covers only the
semantic gap (does a claim follow from its sources).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from core.artefacts.context import EvidencePack
from core.artefacts.schema import (
    LETTER_WORDS,
    PITCH_WORDS,
    ROLES,
    VOICE_MAX_WORDS,
    CoverLetter,
    Pitch,
    Sentence,
    letter_body_text,
    letter_text,
    variant_text,
    word_count,
)

SENTENCE_CODES = frozenset(
    {
        "unknown_source",
        "claim_without_source",
        "number_not_in_sources",
        "voice_with_digits",
        "voice_too_long",
    }
)
"""Problems that belong to one sentence (`Problem.sentence_id` is set)."""

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Problem:
    """One problem found in a document.

    Attributes:
        code: What kind of problem it is.
        message: A human sentence, also used as feedback to the writer.
        sentence_id: The sentence concerned, or "" for a whole-document problem.
    """

    code: str
    message: str
    sentence_id: str = ""


def iter_sentences(doc: CoverLetter | Pitch) -> Iterator[tuple[str, int, Sentence]]:
    """Walk a document's sentences.

    Args:
        doc: A letter or a pitch.

    Yields:
        `(section, index, sentence)`; the section is a paragraph role for a
        letter and `v30`/`v60`/`v90` for a pitch.
    """
    if isinstance(doc, CoverLetter):
        for paragraph in doc.paragraphs:
            for index, sentence in enumerate(paragraph.sentences):
                yield paragraph.role, index, sentence
    else:
        for variant in doc.variants:
            for index, sentence in enumerate(variant.sentences):
                yield f"v{variant.seconds}", index, sentence


def _numbers(text: str) -> set[str]:
    """Extract the numbers of a text in a comparable form.

    Args:
        text: Any text.

    Returns:
        Number tokens without thousands commas ("1,200" becomes "1200").
    """
    return {match.replace(",", "").rstrip(".") for match in _NUMBER.findall(text)}


def _sentence_problems(doc: CoverLetter | Pitch, pack: EvidencePack) -> list[Problem]:
    """Check each sentence on its own.

    Args:
        doc: The document.
        pack: The evidence pack.

    Returns:
        The sentence-level problems.
    """
    problems: list[Problem] = []
    for _section, _index, sentence in iter_sentences(doc):
        if sentence.kind == "composed":
            continue
        if sentence.kind == "voice":
            if _numbers(sentence.text):
                problems.append(
                    Problem(
                        "voice_with_digits",
                        f"A voice sentence states a number: {sentence.text!r}",
                        sentence.id,
                    )
                )
            if word_count(sentence.text) > VOICE_MAX_WORDS:
                problems.append(
                    Problem(
                        "voice_too_long",
                        f"A voice sentence is over {VOICE_MAX_WORDS} words: "
                        f"{sentence.text!r}",
                        sentence.id,
                    )
                )
            continue
        unknown = [s for s in sentence.sources if s not in pack.items]
        if unknown:
            problems.append(
                Problem(
                    "unknown_source",
                    f"{sentence.text!r} cites ids that do not exist: {unknown}",
                    sentence.id,
                )
            )
        valid = [s for s in sentence.sources if s in pack.items]
        if not valid:
            problems.append(
                Problem(
                    "claim_without_source",
                    f"{sentence.text!r} cites no valid source",
                    sentence.id,
                )
            )
            continue
        known = set().union(*(_numbers(t) for t in pack.texts(valid)))
        invented = sorted(_numbers(sentence.text) - known)
        if invented:
            problems.append(
                Problem(
                    "number_not_in_sources",
                    f"{sentence.text!r} states {invented} which its cited "
                    "sources do not",
                    sentence.id,
                )
            )
    return problems


def _structure_problems(doc: CoverLetter | Pitch) -> list[Problem]:
    """Check the document's overall structure.

    Args:
        doc: The document.

    Returns:
        A problem when paragraphs or variants are missing or out of order.
    """
    if isinstance(doc, CoverLetter):
        roles = [p.role for p in doc.paragraphs]
        if roles != list(ROLES):
            return [
                Problem(
                    "structure",
                    f"the letter needs the paragraphs {list(ROLES)} in order, "
                    f"got {roles}",
                )
            ]
    elif [v.seconds for v in doc.variants] != [30, 60, 90] or any(
        len(v.sentences) < 2 for v in doc.variants
    ):
        return [
            Problem(
                "structure",
                "the pitch needs the 30, 60 and 90 second variants, each with "
                "sentences",
            )
        ]
    return []


def _title_problems(doc: CoverLetter | Pitch) -> list[Problem]:
    """Assert the exact title in the rendered text.

    Args:
        doc: The document.

    Returns:
        A problem for each place the exact title is missing.
    """
    title = doc.target_title
    problems: list[Problem] = []
    if isinstance(doc, CoverLetter):
        first = (
            doc.paragraphs[0].sentences[0].text
            if doc.paragraphs and doc.paragraphs[0].sentences
            else ""
        )
        if (
            title not in doc.reference
            or title not in first
            or title not in letter_text(doc)
        ):
            problems.append(
                Problem(
                    "title_missing",
                    f"the exact title {title!r} must be in the reference line "
                    "and the opening sentence",
                )
            )
    else:
        for variant in doc.variants:
            if title not in variant_text(variant):
                problems.append(
                    Problem(
                        "title_missing",
                        f"the {variant.seconds}s pitch must name the exact "
                        f"title {title!r}",
                    )
                )
    return problems


def _length_problems(doc: CoverLetter | Pitch) -> list[Problem]:
    """Check the word budgets.

    Args:
        doc: The document.

    Returns:
        A problem for each text outside its accepted range.
    """
    problems: list[Problem] = []
    if isinstance(doc, CoverLetter):
        low, high = LETTER_WORDS
        words = word_count(letter_body_text(doc))
        if not low <= words <= high:
            problems.append(
                Problem(
                    "length", f"the letter body is {words} words; aim for {low}-{high}"
                )
            )
    else:
        for variant in doc.variants:
            low, high = PITCH_WORDS[variant.seconds]
            words = word_count(variant_text(variant))
            if not low <= words <= high:
                problems.append(
                    Problem(
                        "length",
                        f"the {variant.seconds}s pitch is {words} words; "
                        f"aim for {low}-{high}",
                    )
                )
    return problems


def run_checks(doc: CoverLetter | Pitch, pack: EvidencePack) -> list[Problem]:
    """Run every code check.

    Args:
        doc: The assembled document.
        pack: The evidence pack.

    Returns:
        All problems: structure, title, sentence-level and length.
    """
    return (
        _structure_problems(doc)
        + _title_problems(doc)
        + _sentence_problems(doc, pack)
        + _length_problems(doc)
    )
