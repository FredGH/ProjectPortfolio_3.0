"""JD skill extraction (PLAN.md Step 14): job description -> skills, each
marked must-have or nice-to-have, via one local-LLM call per chunk.

Task `skill_extraction` is routed to Ollama only (DECISIONS.md §1: skill
extraction never migrates to a hosted provider). Descriptions longer than
`max_chunk_chars` are split on paragraph boundaries and the per-chunk
results merged — never silently truncated, because requirements are often
at the end of a posting. Known limitation: a "Nice to have" heading and
its bullets can land in different chunks, in which case the bullets read
as required; the golden set measures how often that costs accuracy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, field_validator

from core.llm.gateway import complete
from core.llm.json_response import parse_json_response
from core.llm.prompts import load_prompt
from core.llm.types import LLMAdapter
from core.skills.normalise import is_plausible_skill, normalise_skill
from core.text import readable_description

_PROMPT_FAMILY = "local"
_PROMPT_VERSION_NUMBER = 1
CURRENT_PROMPT_VERSION = f"{_PROMPT_FAMILY}.v{_PROMPT_VERSION_NUMBER}"

MAX_OUTPUT_TOKENS = 2048
"""Cap on one chunk's reply. A chunk's skill list is well under 1,000 tokens
(about 20 per skill); a reply that reaches this is a model stuck in a loop,
which unchecked ran for over ten minutes on one chunk of a real job. Such a
reply is treated as a failed extraction, not a result."""

DEFAULT_MAX_CHUNK_CHARS = 6000
"""~1.5k tokens of description per call — well inside llama3.1:8b's context,
leaving room for the prompt and the JSON answer."""

_NICE_TO_HAVE_VARIANTS = {"nice_to_have", "preferred", "optional", "bonus", "desirable"}


class ExtractedSkill(BaseModel):
    """One skill the model found in a job description.

    Attributes:
        skill: The skill name as the model wrote it.
        requirement_level: "must_have" or "nice_to_have". Any unrecognised
            value is coerced to "must_have" (the prompt's documented default).
    """

    skill: str
    requirement_level: Literal["must_have", "nice_to_have"] = "must_have"

    @field_validator("requirement_level", mode="before")
    @classmethod
    def _coerce_level(cls, value: object) -> str:
        """Map common model spellings onto the two allowed levels.

        Args:
            value: The raw value from the model's JSON.

        Returns:
            "nice_to_have" for a recognised hedging variant, else "must_have".
        """
        key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        return "nice_to_have" if key in _NICE_TO_HAVE_VARIANTS else "must_have"


class _Response(BaseModel):
    """The model's JSON answer for one chunk."""

    skills: list[ExtractedSkill] = []


@dataclass(frozen=True)
class JdExtraction:
    """The merged result of extracting one job description.

    Attributes:
        skills: De-duplicated skills across all chunks.
        prompt_version: The prompt version used, e.g. "local.v1".
        model: The model identifier reported by the last call ("" if no
            call was made because the description was empty).
    """

    skills: list[ExtractedSkill]
    prompt_version: str
    model: str


def split_into_chunks(text: str, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most `max_chars`, on paragraph boundaries.

    Args:
        text: The description text.
        max_chars: Maximum characters per chunk.

    Returns:
        The chunks in order. A single paragraph longer than `max_chars` is
        hard-split at character boundaries (rare for real postings).
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [
            paragraph[start : start + max_chars]
            for start in range(0, len(paragraph), max_chars)
        ]
        for piece in pieces:
            if current and len(current) + 2 + len(piece) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def merge_skills(skills: Iterable[ExtractedSkill]) -> list[ExtractedSkill]:
    """Collapse duplicate skills, keeping "must_have" if any mention is.

    Args:
        skills: Skills from one or more chunks.

    Returns:
        One entry per normalised name, in first-seen order and spelling.
        Names that normalise to nothing, and ones no skill name could be
        (`core.skills.normalise.is_plausible_skill` — a sentence the model
        answered with instead of a skill), are dropped.
    """
    merged: dict[str, ExtractedSkill] = {}
    for item in skills:
        key = normalise_skill(item.skill)
        if not is_plausible_skill(key):
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
        elif item.requirement_level == "must_have":
            merged[key] = existing.model_copy(update={"requirement_level": "must_have"})
    return list(merged.values())


def extract_jd_skills(
    description: str,
    *,
    adapters: dict[str, LLMAdapter],
    provider: str | None = None,
    model: str | None = None,
    prompt_family: str | None = None,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
) -> JdExtraction:
    """Extract a job description's skills and requirement levels.

    Args:
        description: The job description (may be HTML-escaped HTML, as
            stored by ATS sources).
        adapters: Every available LLM adapter, keyed by provider.
        provider: Overrides the `skill_extraction` routing (eval harness).
        model: The model to use with `provider`.
        prompt_family: Which prompt family to load; defaults to "local".
        max_chunk_chars: Maximum description characters per LLM call.

    Returns:
        The merged `JdExtraction`.

    Raises:
        ValueError: If any chunk's response can't be parsed as the expected
            JSON shape, or was cut off by the `MAX_OUTPUT_TOKENS` cap.
    """
    family = prompt_family or _PROMPT_FAMILY
    template = load_prompt("skill_extraction", family, _PROMPT_VERSION_NUMBER)
    prompt_version = f"{family}.v{_PROMPT_VERSION_NUMBER}"

    collected: list[ExtractedSkill] = []
    model_id = ""
    for chunk in split_into_chunks(readable_description(description), max_chunk_chars):
        response = complete(
            task="skill_extraction",
            prompt=template.format(description=chunk),
            prompt_version=prompt_version,
            adapters=adapters,
            provider=provider,
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        model_id = response.model
        if response.truncated:
            raise ValueError(
                f"skill_extraction response hit the {MAX_OUTPUT_TOKENS}-token cap "
                f"(the model was likely looping); discarded so the job is retried "
                f"(response started with: {response.text.strip()[:200]!r})"
            )
        response_text = response.text.strip()
        try:
            parsed = parse_json_response(response_text)
            collected.extend(_Response.model_validate(parsed).skills)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"could not parse skill_extraction response: {exc} "
                f"(response started with: {response_text[:200]!r})"
            ) from exc
    return JdExtraction(merge_skills(collected), prompt_version, model_id)
