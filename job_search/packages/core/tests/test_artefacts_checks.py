"""Unit tests for the code-only artefact checks."""

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

from core.artefacts.assemble import build_letter, build_pitch, parse_writer_reply
from core.artefacts.checks import iter_sentences, run_checks
from core.artefacts.context import Evidence

_LONG = " ".join(["word"] * 80)


def _letter(**overrides: object):  # type: ignore[no-untyped-def]
    """Assemble a letter from a scripted reply.

    Args:
        **overrides: Per-role sentence overrides.

    Returns:
        The assembled `CoverLetter`.
    """
    reply = parse_writer_reply(letter_reply_json(**overrides), "cover_letter")
    return build_letter(make_pack(), reply, today=TODAY)


def _codes(problems) -> set[str]:  # type: ignore[no-untyped-def]
    """Collect the problem codes.

    Args:
        problems: A list of `Problem`s.

    Returns:
        The set of codes.
    """
    return {p.code for p in problems}


class TestLetterChecks(unittest.TestCase):
    """run_checks over a letter."""

    def test_a_well_formed_letter_has_only_a_length_problem_when_short(self) -> None:
        codes = _codes(run_checks(_letter(), make_pack()))
        self.assertEqual(codes - {"length"}, set())

    def test_an_unknown_source_is_flagged_on_the_sentence(self) -> None:
        letter = _letter(
            evidence=[
                sentence_json("I built things.", sources=["cv:nope", cv_ref(0, 0)])
            ]
        )
        problems = [
            p for p in run_checks(letter, make_pack()) if p.code == "unknown_source"
        ]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].sentence_id, letter.paragraphs[2].sentences[0].id)

    def test_a_claim_without_any_source_is_flagged(self) -> None:
        letter = _letter(evidence=[sentence_json("I built things.", sources=[])])
        self.assertIn("claim_without_source", _codes(run_checks(letter, make_pack())))

    def test_an_invented_number_is_caught_before_the_critic(self) -> None:
        letter = _letter(
            evidence=[
                sentence_json("I led a team of 40 engineers.", sources=[cv_ref(0, 0)])
            ]
        )
        problems = [
            p
            for p in run_checks(letter, make_pack())
            if p.code == "number_not_in_sources"
        ]
        self.assertEqual(len(problems), 1)
        self.assertIn("40", problems[0].message)

    def test_a_number_that_a_cited_source_states_is_fine(self) -> None:
        pack = make_pack()
        pack.items["cv:zz2"] = Evidence("cv:zz2", "x", "Ran 40 pipelines")
        reply = parse_writer_reply(
            letter_reply_json(
                evidence=[sentence_json("I ran 40 pipelines.", sources=["cv:zz2"])]
            ),
            "cover_letter",
        )
        self.assertNotIn(
            "number_not_in_sources",
            _codes(run_checks(build_letter(pack, reply, today=TODAY), pack)),
        )

    def test_numbers_ignore_commas_percent_and_currency(self) -> None:
        pack = make_pack()
        pack.items["cv:zz1"] = Evidence(
            "cv:zz1", "x", "Cut costs by 1,200 pounds, a 15% saving"
        )
        reply = parse_writer_reply(
            letter_reply_json(
                evidence=[
                    sentence_json("I saved £1200, a 15 % cut.", sources=["cv:zz1"])
                ]
            ),
            "cover_letter",
        )
        self.assertNotIn(
            "number_not_in_sources",
            _codes(run_checks(build_letter(pack, reply, today=TODAY), pack)),
        )

    def test_a_voice_sentence_with_a_digit_or_too_long_is_flagged(self) -> None:
        letter = _letter(
            close=[
                sentence_json("I have 10 years of experience.", "voice"),
                sentence_json(" ".join(["very"] * 30) + " much.", "voice"),
            ]
        )
        codes = _codes(run_checks(letter, make_pack()))
        self.assertIn("voice_with_digits", codes)
        self.assertIn("voice_too_long", codes)

    def test_missing_roles_are_a_structure_problem(self) -> None:
        reply = parse_writer_reply(
            json.dumps(
                {
                    "paragraphs": [
                        {"role": "opening", "sentences": [sentence_json("A.", "voice")]}
                    ]
                }
            ),
            "cover_letter",
        )
        letter = build_letter(make_pack(), reply, today=TODAY)
        self.assertIn("structure", _codes(run_checks(letter, make_pack())))

    def test_the_exact_title_must_be_in_the_rendered_text(self) -> None:
        letter = _letter()
        self.assertNotIn("title_missing", _codes(run_checks(letter, make_pack())))
        letter.reference = "Re: Something Else"
        letter.paragraphs[0].sentences[0].text = "I am applying."
        self.assertIn("title_missing", _codes(run_checks(letter, make_pack())))

    def test_the_letter_length_is_checked_in_both_directions(self) -> None:
        self.assertIn("length", _codes(run_checks(_letter(), make_pack())))
        long = _letter(
            evidence=[sentence_json(_LONG + ".", sources=[cv_ref(0, 0)])] * 5
        )
        self.assertIn("length", _codes(run_checks(long, make_pack())))


class TestPitchChecks(unittest.TestCase):
    """run_checks over a pitch."""

    def _pitch(self, **overrides: object):  # type: ignore[no-untyped-def]
        """Assemble a pitch from a scripted reply.

        Args:
            **overrides: Per-variant sentence overrides.

        Returns:
            The assembled `Pitch`.
        """
        return build_pitch(
            make_pack(), parse_writer_reply(pitch_reply_json(**overrides), "pitch")
        )

    def test_all_three_variants_must_be_present_and_start_with_the_title(self) -> None:
        pitch = self._pitch()
        self.assertNotIn("structure", _codes(run_checks(pitch, make_pack())))
        pitch.variants.pop()
        self.assertIn("structure", _codes(run_checks(pitch, make_pack())))

    def test_a_variant_that_loses_its_title_sentence_is_flagged(self) -> None:
        pitch = self._pitch()
        pitch.variants[0].sentences[0].text = "Hello there."
        self.assertIn("title_missing", _codes(run_checks(pitch, make_pack())))

    def test_each_variant_is_held_to_its_own_word_budget(self) -> None:
        problems = [
            p for p in run_checks(self._pitch(), make_pack()) if p.code == "length"
        ]
        self.assertEqual(len(problems), 3)

    def test_an_invented_number_in_a_pitch_is_caught(self) -> None:
        pitch = self._pitch(
            **{"30": [sentence_json("I run 12 pipelines.", sources=[cv_ref(0, 0)])]}
        )
        self.assertIn("number_not_in_sources", _codes(run_checks(pitch, make_pack())))


class TestIterSentences(unittest.TestCase):
    """iter_sentences names each sentence's section and position."""

    def test_letter_sections_are_roles_and_pitch_sections_are_seconds(self) -> None:
        sections = {s for s, _i, _x in iter_sentences(_letter())}
        self.assertEqual(sections, {"opening", "why_company", "evidence", "close"})
        pitch = build_pitch(
            make_pack(), parse_writer_reply(pitch_reply_json(), "pitch")
        )
        self.assertEqual(
            {s for s, _i, _x in iter_sentences(pitch)}, {"v30", "v60", "v90"}
        )


if __name__ == "__main__":
    unittest.main()
