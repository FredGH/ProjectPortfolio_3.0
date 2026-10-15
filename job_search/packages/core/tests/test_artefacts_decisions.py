"""Unit tests for applying a link / remove decision to a document."""

from __future__ import annotations

import unittest

from tests.artefacts_fixtures import (
    TODAY,
    cv_ref,
    letter_reply_json,
    make_pack,
    pitch_reply_json,
    sentence_json,
)

from core.artefacts.assemble import build_letter, build_pitch, parse_writer_reply
from core.artefacts.decisions import DecisionError, apply_decision


def _letter(**overrides: object):  # type: ignore[no-untyped-def]
    """Assemble a letter from a scripted reply.

    Args:
        **overrides: Per-role sentence overrides.

    Returns:
        The assembled `CoverLetter`.
    """
    return build_letter(
        make_pack(),
        parse_writer_reply(letter_reply_json(**overrides), "cover_letter"),
        today=TODAY,
    )


class TestLink(unittest.TestCase):
    """Linking attaches a source and makes the sentence a traced claim."""

    def test_link_replaces_the_sources_and_marks_it_linked(self) -> None:
        letter = _letter(
            evidence=[sentence_json("I led everything.", sources=["cv:nope"])]
        )
        target = letter.paragraphs[2].sentences[0]
        out = apply_decision(
            letter,
            target.id,
            action="link",
            evidence_ref=cv_ref(0, 1),
            pack=make_pack(),
        )
        done = out.paragraphs[2].sentences[0]
        self.assertEqual(
            (done.sources, done.flag, done.kind), ([cv_ref(0, 1)], "linked", "claim")
        )
        self.assertEqual(letter.paragraphs[2].sentences[0].flag, "orphan")

    def test_a_voice_sentence_linked_becomes_a_claim(self) -> None:
        letter = _letter(close=[sentence_json("I have run it for years.", "voice")])
        target = letter.paragraphs[3].sentences[0]
        out = apply_decision(
            letter,
            target.id,
            action="link",
            evidence_ref="job:posting",
            pack=make_pack(),
        )
        self.assertEqual(out.paragraphs[3].sentences[0].kind, "claim")

    def test_linking_to_an_id_outside_the_pack_is_refused(self) -> None:
        letter = _letter()
        target = letter.paragraphs[2].sentences[0]
        with self.assertRaises(DecisionError):
            apply_decision(
                letter,
                target.id,
                action="link",
                evidence_ref="cv:made-up",
                pack=make_pack(),
            )
        with self.assertRaises(DecisionError):
            apply_decision(
                letter, target.id, action="link", evidence_ref=None, pack=make_pack()
            )


class TestRemove(unittest.TestCase):
    """Removing drops the sentence and, in a letter, an emptied paragraph."""

    def test_removing_a_sentence_drops_only_that_sentence(self) -> None:
        letter = _letter()
        target = letter.paragraphs[2].sentences[0]
        out = apply_decision(
            letter, target.id, action="remove", evidence_ref=None, pack=make_pack()
        )
        self.assertEqual(
            len(out.paragraphs[2].sentences),
            len(letter.paragraphs[2].sentences) - 1,
        )

    def test_removing_the_last_sentence_drops_the_paragraph(self) -> None:
        letter = _letter(
            why_company=[sentence_json("One fact.", sources=["job:posting"])]
        )
        target = letter.paragraphs[1].sentences[0]
        out = apply_decision(
            letter, target.id, action="remove", evidence_ref=None, pack=make_pack()
        )
        self.assertEqual(
            [p.role for p in out.paragraphs], ["opening", "evidence", "close"]
        )

    def test_composed_sentences_cannot_be_removed_or_linked(self) -> None:
        letter = _letter()
        composed = letter.paragraphs[0].sentences[0]
        for action in ("remove", "link"):
            with self.assertRaises(DecisionError):
                apply_decision(
                    letter,
                    composed.id,
                    action=action,
                    evidence_ref=cv_ref(0, 0),
                    pack=make_pack(),
                )

    def test_a_pitch_variant_keeps_its_composed_sentence(self) -> None:
        pitch = build_pitch(
            make_pack(), parse_writer_reply(pitch_reply_json(), "pitch")
        )
        target = pitch.variants[0].sentences[1]
        out = apply_decision(
            pitch, target.id, action="remove", evidence_ref=None, pack=make_pack()
        )
        self.assertEqual(out.variants[0].sentences[0].kind, "composed")
        self.assertEqual(
            len(out.variants[0].sentences), len(pitch.variants[0].sentences) - 1
        )

    def test_an_unknown_sentence_or_action_is_refused(self) -> None:
        letter = _letter()
        with self.assertRaises(DecisionError):
            apply_decision(
                letter, "nope", action="remove", evidence_ref=None, pack=make_pack()
            )
        with self.assertRaises(DecisionError):
            apply_decision(
                letter,
                letter.paragraphs[2].sentences[0].id,
                action="dance",
                evidence_ref=None,
                pack=make_pack(),
            )


if __name__ == "__main__":
    unittest.main()
