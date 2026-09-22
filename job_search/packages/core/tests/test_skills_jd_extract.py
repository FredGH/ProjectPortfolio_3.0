"""Unit tests for core.skills.jd_extract (fake adapter, no network)."""

from __future__ import annotations

import json
import unittest

from tests.skills_fakes import FakeAdapter

from core.skills.jd_extract import (
    CURRENT_PROMPT_VERSION,
    MAX_OUTPUT_TOKENS,
    ExtractedSkill,
    extract_jd_skills,
    merge_skills,
    split_into_chunks,
)


def _reply(*skills: tuple[str, str]) -> str:
    return json.dumps(
        {"skills": [{"skill": s, "requirement_level": lvl} for s, lvl in skills]}
    )


def _extract(adapter: FakeAdapter, description: str, **kwargs):
    return extract_jd_skills(
        description,
        adapters={"ollama": adapter},
        provider="ollama",
        model="test-model",
        **kwargs,
    )


class TestExtractJdSkills(unittest.TestCase):
    def test_returns_skills_levels_and_provenance(self) -> None:
        adapter = FakeAdapter(_reply(("Python", "must_have"), ("dbt", "nice_to_have")))
        result = _extract(adapter, "Python required. dbt is a plus.")
        self.assertEqual(
            [(s.skill, s.requirement_level) for s in result.skills],
            [("Python", "must_have"), ("dbt", "nice_to_have")],
        )
        self.assertEqual(result.prompt_version, CURRENT_PROMPT_VERSION)
        self.assertEqual(result.prompt_version, "local.v1")
        self.assertEqual(result.model, "test-model")

    def test_tolerates_a_fenced_response(self) -> None:
        adapter = FakeAdapter("```json\n" + _reply(("SQL", "must_have")) + "\n```")
        self.assertEqual(_extract(adapter, "SQL").skills[0].skill, "SQL")

    def test_level_variants_are_coerced_and_unknown_defaults_to_must_have(self) -> None:
        adapter = FakeAdapter(
            _reply(
                ("A", "preferred"),
                ("B", "Nice-to-have"),
                ("C", "required"),
                ("D", "???"),
            )
        )
        levels = {s.skill: s.requirement_level for s in _extract(adapter, "x").skills}
        self.assertEqual(
            levels,
            {
                "A": "nice_to_have",
                "B": "nice_to_have",
                "C": "must_have",
                "D": "must_have",
            },
        )

    def test_html_escaped_descriptions_are_cleaned_before_prompting(self) -> None:
        adapter = FakeAdapter(_reply())
        _extract(adapter, "&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;")
        prompt = adapter.calls[0][1]
        self.assertIn("Python", prompt)
        self.assertNotIn("<li>", prompt)

    def test_an_empty_description_makes_no_llm_call(self) -> None:
        adapter = FakeAdapter(_reply())
        result = _extract(adapter, "   ")
        self.assertEqual(result.skills, [])
        self.assertEqual(adapter.calls, [])

    def test_every_chunk_is_sent_with_the_output_token_cap(self) -> None:
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        _extract(adapter, "Python required.")
        self.assertEqual(adapter.max_tokens_seen, [MAX_OUTPUT_TOKENS])

    def test_a_reply_cut_off_by_the_cap_is_an_error_even_if_it_parses(self) -> None:
        # A model stuck in a loop hit the cap: whatever it produced is not a
        # trustworthy skill list, so the job must fail and be retried.
        adapter = FakeAdapter(_reply(("Python", "must_have")), truncated=True)
        with self.assertRaises(ValueError) as ctx:
            _extract(adapter, "Python required.")
        self.assertIn("token cap", str(ctx.exception))

    def test_a_malformed_response_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _extract(FakeAdapter("I could not find any skills."), "Some job text")

    def test_long_descriptions_are_chunked_without_dropping_text(self) -> None:
        paragraphs = [f"Paragraph {i} " + "x" * 30 for i in range(3)]
        adapter = FakeAdapter(
            _reply(("Python", "must_have")),
            _reply(("SQL", "must_have")),
            _reply(("Go", "nice_to_have")),
        )
        result = _extract(adapter, "\n\n".join(paragraphs), max_chunk_chars=50)
        self.assertEqual(len(adapter.calls), 3)
        all_prompts = " ".join(prompt for _model, prompt in adapter.calls)
        for paragraph in paragraphs:
            self.assertIn(paragraph, all_prompts)
        self.assertEqual({s.skill for s in result.skills}, {"Python", "SQL", "Go"})

    def test_must_have_wins_when_chunks_disagree(self) -> None:
        adapter = FakeAdapter(
            _reply(("Python", "nice_to_have")), _reply(("python", "must_have"))
        )
        result = _extract(adapter, "aaaa\n\nbbbb", max_chunk_chars=5)
        self.assertEqual(
            [(s.skill, s.requirement_level) for s in result.skills],
            [("Python", "must_have")],
        )


class TestSplitIntoChunks(unittest.TestCase):
    def test_packs_paragraphs_up_to_the_limit(self) -> None:
        self.assertEqual(
            split_into_chunks("aa\n\nbb\n\ncc", max_chars=6), ["aa\n\nbb", "cc"]
        )

    def test_splits_a_paragraph_longer_than_the_limit(self) -> None:
        chunks = split_into_chunks("x" * 25, max_chars=10)
        self.assertEqual(chunks, ["x" * 10, "x" * 10, "x" * 5])

    def test_blank_text_yields_no_chunks(self) -> None:
        self.assertEqual(split_into_chunks(" \n\n "), [])


class TestMergeSkills(unittest.TestCase):
    def test_dedupes_case_insensitively_keeping_the_first_spelling(self) -> None:
        merged = merge_skills(
            [
                ExtractedSkill(skill="Kubernetes", requirement_level="must_have"),
                ExtractedSkill(skill="kubernetes", requirement_level="must_have"),
            ]
        )
        self.assertEqual([m.skill for m in merged], ["Kubernetes"])

    def test_drops_names_that_normalise_to_nothing(self) -> None:
        merged = merge_skills(
            [ExtractedSkill(skill=" - ", requirement_level="must_have")]
        )
        self.assertEqual(merged, [])

    def test_drops_a_sentence_the_model_returned_as_a_skill(self) -> None:
        merged = merge_skills(
            [
                ExtractedSkill(skill="Python", requirement_level="must_have"),
                ExtractedSkill(
                    skill=(
                        "Experience working with distributed systems at scale in "
                        "a fast-paced agile environment alongside product teams"
                    ),
                    requirement_level="must_have",
                ),
                ExtractedSkill(skill="Kubernetes", requirement_level="nice_to_have"),
            ]
        )
        self.assertEqual([m.skill for m in merged], ["Python", "Kubernetes"])


if __name__ == "__main__":
    unittest.main()
