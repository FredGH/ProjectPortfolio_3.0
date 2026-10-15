"""The bank writer (PLAN.md Step 20): ask Claude for a batch of Q&As."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from core.company_intel.llm import TaskRun, run_task
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter

TASK = "interview_questions"
PROMPT_VERSION = 1
BATCH_SIZE = 10
_EXISTING_SHOWN = 80

_KIND_RULES = {
    "technical": (
        "Technical questions an interviewer for this role family would really "
        "ask, from fundamentals to advanced."
    ),
    "behavioural": (
        "Behavioural questions (past experience: conflict, failure, ownership, "
        "influence, ambiguity). They are generic prompts; the candidate's own "
        "answer is written separately."
    ),
}
_ANSWER_RULES = {
    "technical": "a correct, concise model answer a strong candidate would give "
    "(3-8 sentences; include a short example or code idea where it helps).",
    "behavioural": "a short STAR scaffold: what a strong answer covers in "
    "Situation, Task, Action and Result for this question (no invented story).",
}


class Candidate(BaseModel):
    """One generated Q&A before the critic.

    Attributes:
        question: The question.
        model_answer: The model answer.
        difficulty: 1-5.
        tags: Skills the question tests.
    """

    question: str = Field(min_length=5)
    model_answer: str = Field(min_length=1)
    difficulty: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list)


class WriterError(ValueError):
    """The writer's reply could not be used."""


def question_hash(question: str) -> str:
    """Hash a question so repeats collapse.

    Args:
        question: The question text.

    Returns:
        A hash of its lowercased alphanumeric words.
    """
    words = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
    return hashlib.sha1(words.encode()).hexdigest()[:20]


def parse_batch(text: str) -> list[Candidate]:
    """Read the writer's JSON reply.

    Args:
        text: The raw reply.

    Returns:
        The valid candidates; entries that do not validate are dropped.

    Raises:
        WriterError: If the reply is not JSON with a `questions` list, or no
            entry is usable.
    """
    try:
        data = parse_json_response(text.strip())
        raw = data["questions"]
        if not isinstance(raw, list):
            raise TypeError("questions is not a list")
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise WriterError(f"unusable writer reply: {exc}") from exc
    found = []
    for entry in raw:
        try:
            found.append(Candidate.model_validate(entry))
        except ValidationError:
            continue
    if not found:
        raise WriterError("unusable writer reply: no valid question")
    return found


def write_batch(
    category: str,
    topic: str,
    kind: str,
    count: int,
    existing: list[str],
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> TaskRun:
    """Ask the writer for a batch.

    Args:
        category: The qa_category.
        topic: The topic (or `behavioural`).
        kind: `technical` or `behavioural`.
        count: How many new questions to ask for.
        existing: Questions already in the bank for this topic.
        adapters: Provider name to LLM adapter.
        config_path: Task-config override (tests).

    Returns:
        The reply and its usage record.

    Raises:
        core.company_intel.llm.LlmTaskError: If the reply was cut off.
    """
    shown = existing[-_EXISTING_SHOWN:]
    return run_task(
        TASK,
        PROMPT_VERSION,
        {
            "category": category.replace("_", " "),
            "topic": topic,
            "kind": kind,
            "kind_rules": _KIND_RULES[kind],
            "answer_rules": _ANSWER_RULES[kind],
            "count": str(count),
            "existing": "\n".join(f"- {q}" for q in shown) or "(none yet)",
            "difficulty_mix": "roughly a third easy (1-2), a third medium (3), "
            "a third hard (4-5).",
        },
        adapters,
        config_path=config_path,
        max_tokens=8192,
    )
