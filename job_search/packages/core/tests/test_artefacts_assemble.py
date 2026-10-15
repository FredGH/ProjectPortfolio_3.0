"""Unit tests for parsing the writer's reply and assembling documents."""

from __future__ import annotations

import json
import unittest

from tests.artefacts_fixtures import (
    TODAY,
    cv_ref,
    letter_reply_json,
    make_pack,
    pitch_reply_json,
    sentence_json,
)

from core.artefacts.assemble import (
    WriterError,
    build_letter,
    build_pitch,
    parse_writer_reply,
)
from core.artefacts.schema import letter_body_text, letter_text, variant_text


class TestParseWriterReply(unittest.TestCase):
    """parse_writer_reply reads tolerant JSON and rejects junk."""

    def test_a_fenced_reply_is_read(self) -> None:
        reply = parse_writer_reply(
            "```json\n" + letter_reply_json() + "\n```", "cover_letter"
        )
        self.assertEqual(
            [p.role for p in reply.paragraphs],
            ["opening", "why_company", "evidence", "close"],
        )

    def test_junk_and_the_wrong_shape_are_writer_errors(self) -> None:
        for junk in ("", "no json", "[]", '{"paragraphs": "x"}', "{}"):
            with self.assertRaises(WriterError, msg=junk):
                parse_writer_reply(junk, "cover_letter")
        with self.assertRaises(WriterError):
            parse_writer_reply(json.dumps({"paragraphs": []}), "pitch")


class TestBuildLetter(unittest.TestCase):
    """build_letter composes the title lines and keeps the model's sentences."""

    def _letter(self, **overrides: object):  # type: ignore[no-untyped-def]
        """Assemble a letter from a scripted reply.

        Args:
            **overrides: Per-role sentence overrides for the reply.

        Returns:
            The assembled `CoverLetter`.
        """
        reply = parse_writer_reply(letter_reply_json(**overrides), "cover_letter")
        return build_letter(make_pack(), reply, today=TODAY)

    def test_the_title_lines_are_composed_by_code(self) -> None:
        letter = self._letter()
        self.assertEqual(letter.reference, "Re: Data Engineer")
        self.assertEqual(letter.recipient, "Hiring team, Zzfixture Intel Ltd")
        self.assertEqual(letter.date, "15 October 2026")
        self.assertEqual(letter.sign_off, "Kind regards,\nZz Fixture")
        first = letter.paragraphs[0].sentences[0]
        self.assertEqual(first.kind, "composed")
        self.assertEqual(
            first.text,
            "I am applying for the Data Engineer role at Zzfixture Intel Ltd.",
        )
        self.assertIn("Re: Data Engineer", letter_text(letter))

    def test_the_models_sentences_follow_in_their_paragraphs(self) -> None:
        letter = self._letter()
        self.assertEqual(
            [p.role for p in letter.paragraphs],
            ["opening", "why_company", "evidence", "close"],
        )
        evidence = letter.paragraphs[2].sentences
        self.assertEqual(evidence[0].sources, [cv_ref(0, 0)])
        self.assertEqual(evidence[0].flag, "")

    def test_voice_sentences_lose_any_sources_the_model_gave_them(self) -> None:
        letter = self._letter(
            close=[sentence_json("Thank you.", "voice", ["job:posting"])]
        )
        self.assertEqual(letter.paragraphs[3].sentences[0].sources, [])

    def test_a_claim_with_no_valid_source_is_flagged_an_orphan(self) -> None:
        letter = self._letter(
            evidence=[sentence_json("I led everything.", sources=["cv:nope"])]
        )
        self.assertEqual(letter.paragraphs[2].sentences[0].flag, "orphan")
        letter = self._letter(evidence=[sentence_json("I led everything.", sources=[])])
        self.assertEqual(letter.paragraphs[2].sentences[0].flag, "orphan")

    def test_an_unknown_kind_is_treated_as_a_claim(self) -> None:
        letter = self._letter(close=[sentence_json("Odd.", "weird", ["job:posting"])])
        self.assertEqual(letter.paragraphs[3].sentences[0].kind, "claim")

    def test_text_is_cleaned_and_empty_sentences_dropped(self) -> None:
        letter = self._letter(
            close=[
                sentence_json("• **Great** news 😀", "voice"),
                sentence_json("   ", "voice"),
            ]
        )
        close = letter.paragraphs[3].sentences
        self.assertEqual([s.text for s in close], ["Great news"])

    def test_ids_are_stable_and_distinct(self) -> None:
        first, second = self._letter(), self._letter()
        ids = [s.id for p in first.paragraphs for s in p.sentences]
        self.assertEqual(ids, [s.id for p in second.paragraphs for s in p.sentences])
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_repeated_role_keeps_the_first_and_a_missing_role_is_absent(
        self,
    ) -> None:
        reply = parse_writer_reply(
            json.dumps(
                {
                    "paragraphs": [
                        {
                            "role": "opening",
                            "sentences": [sentence_json("A.", "voice")],
                        },
                        {
                            "role": "opening",
                            "sentences": [sentence_json("B.", "voice")],
                        },
                    ]
                }
            ),
            "cover_letter",
        )
        letter = build_letter(make_pack(), reply, today=TODAY)
        self.assertEqual([p.role for p in letter.paragraphs], ["opening"])
        self.assertEqual(letter_body_text(letter).count("A."), 1)
        self.assertNotIn("B.", letter_body_text(letter))


class TestBuildPitch(unittest.TestCase):
    """build_pitch composes each variant's first sentence."""

    def test_each_variant_opens_with_the_composed_title_sentence(self) -> None:
        reply = parse_writer_reply(pitch_reply_json(), "pitch")
        pitch = build_pitch(make_pack(), reply)
        self.assertEqual([v.seconds for v in pitch.variants], [30, 60, 90])
        for variant in pitch.variants:
            self.assertEqual(variant.sentences[0].kind, "composed")
            self.assertEqual(
                variant.sentences[0].text,
                "I'm Zz Fixture, and I'm interested in the Data Engineer role at "
                "Zzfixture Intel Ltd.",
            )
            self.assertTrue(variant_text(variant).startswith("I'm Zz Fixture"))

    def test_a_missing_variant_is_built_empty_apart_from_the_composed_sentence(
        self,
    ) -> None:
        reply = parse_writer_reply(
            json.dumps({"variants": {"30": [sentence_json("A.")]}}), "pitch"
        )
        pitch = build_pitch(make_pack(), reply)
        self.assertEqual(len(pitch.variants), 3)
        self.assertEqual(len(pitch.variants[2].sentences), 1)

    def test_an_unknown_company_drops_the_at_clause(self) -> None:
        pack = make_pack()
        object.__setattr__(pack, "company", "")
        reply = parse_writer_reply(pitch_reply_json(), "pitch")
        text = build_pitch(pack, reply).variants[0].sentences[0].text
        self.assertTrue(text.endswith("the Data Engineer role."))


if __name__ == "__main__":
    unittest.main()
