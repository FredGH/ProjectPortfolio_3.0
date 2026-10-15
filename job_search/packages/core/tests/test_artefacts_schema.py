"""Unit tests for the artefact document model."""

from __future__ import annotations

import unittest

from core.artefacts.schema import (
    CoverLetter,
    Paragraph,
    Pitch,
    PitchVariant,
    Sentence,
    letter_body_text,
    letter_text,
    pitch_text,
    variant_text,
    word_count,
)


def _s(text: str, kind: str = "claim") -> Sentence:
    """Build a sentence.

    Args:
        text: The sentence text.
        kind: The sentence kind.

    Returns:
        A `Sentence` with a fixed id.
    """
    return Sentence(id="id1", text=text, kind=kind)  # type: ignore[arg-type]


def _letter() -> CoverLetter:
    """Build a tiny letter.

    Returns:
        A two-paragraph `CoverLetter`.
    """
    return CoverLetter(
        sender="Zz Fixture",
        contact="zz@example.com",
        date="15 October 2026",
        recipient="Hiring team, Acme",
        reference="Re: Data Engineer",
        target_title="Data Engineer",
        company="Acme",
        paragraphs=[
            Paragraph(
                role="opening", sentences=[_s("One two.", "composed"), _s("Three.")]
            ),
            Paragraph(role="close", sentences=[_s("Four five six.", "voice")]),
        ],
        sign_off="Kind regards,\nZz Fixture",
    )


class TestRendering(unittest.TestCase):
    """The plain-text views of a document."""

    def test_the_body_joins_sentences_and_separates_paragraphs(self) -> None:
        self.assertEqual(
            letter_body_text(_letter()), "One two. Three.\n\nFour five six."
        )

    def test_the_letter_text_has_the_header_body_and_sign_off(self) -> None:
        text = letter_text(_letter())
        lines = text.split("\n")
        self.assertEqual(
            lines[:5],
            [
                "Zz Fixture",
                "zz@example.com",
                "15 October 2026",
                "Hiring team, Acme",
                "Re: Data Engineer",
            ],
        )
        self.assertIn("One two. Three.", text)
        self.assertTrue(text.rstrip().endswith("Zz Fixture"))

    def test_a_pitch_variant_is_its_sentences_joined(self) -> None:
        variant = PitchVariant(
            seconds=30, sentences=[_s("A b.", "composed"), _s("C d.")]
        )
        self.assertEqual(variant_text(variant), "A b. C d.")
        pitch = Pitch(sender="Zz", target_title="T", company="C", variants=[variant])
        self.assertIn("30 seconds", pitch_text(pitch))
        self.assertIn("A b. C d.", pitch_text(pitch))

    def test_word_count_counts_words_not_punctuation(self) -> None:
        self.assertEqual(word_count("It's  a test, really."), 4)
        self.assertEqual(word_count(""), 0)


if __name__ == "__main__":
    unittest.main()
