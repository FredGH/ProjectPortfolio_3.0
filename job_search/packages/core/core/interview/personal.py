"""CV-grounded personal answer drafts (PLAN.md Step 20).

Built on the Step 19 sentence-evidence engine: every claim cites a CV bullet
or role, code checks the ids and numbers, a Claude critic judges the rest, and
a sentence still untraced after the retries is left for the user to link or
remove. The evidence pack is the truth base only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from core.artefacts.assemble import WriterSentence, build_sentence
from core.artefacts.checks import Problem, sentence_problems
from core.artefacts.context import EvidencePack
from core.artefacts.critic import CriticError, CriticItem, run_critic
from core.artefacts.write import render_evidence
from core.company_intel.llm import LlmTaskError, run_task
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter

TASK = "interview_answer"
PROMPT_VERSION = 1
MAX_RETRIES = 2
MIN_SENTENCES = 3
SECTION = "answer"


class _Reply(BaseModel):
    """The writer's reply.

    Attributes:
        sentences: The answer's sentences.
    """

    sentences: list[WriterSentence] = Field(min_length=1)


@dataclass(frozen=True)
class OpenDraft:
    """A sentence that stayed untraced.

    Attributes:
        sentence_id: The sentence's stable id.
        text: The sentence.
        claimed_sources: The ids it cited.
        issue: Why it was rejected.
    """

    sentence_id: str
    text: str
    claimed_sources: list[str]
    issue: str


@dataclass
class AnswerDraft:
    """The outcome of drafting one answer.

    Attributes:
        text: The drafted answer ("" when the run failed).
        status: `drafted` (every sentence traced and judged supported),
            `needs_review` (some wait for the user) or `failed`.
        open_sentences: Sentences awaiting the user's decision.
        calls: Usage records of every LLM call.
        error: Why the run failed, if it did.
    """

    text: str = ""
    status: str = "failed"
    open_sentences: list[OpenDraft] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    error: str | None = None


def _feedback(problems: list[Problem]) -> str:
    """Turn problems into feedback for the next attempt.

    Args:
        problems: What was wrong with the last attempt.

    Returns:
        A block to splice into the prompt.
    """
    lines = "\n".join(f"- {p.message}" for p in problems)
    return (
        "\nYour previous attempt was rejected for these reasons:\n"
        f"{lines}\nFix each one by removing the unsupported claim or citing a "
        "listed id that really supports it.\n"
    )


def draft_answer(
    question: str,
    scaffold: str,
    pack: EvidencePack,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> AnswerDraft:
    """Draft a personal answer from the user's CV.

    Args:
        question: The interview question.
        scaffold: The bank's model answer, as a guide to what to cover.
        pack: The CV-only evidence pack.
        adapters: LLM adapters by provider; must include `anthropic`.
        config_path: Task-config override (tests).

    Returns:
        The draft. Nothing unchecked is `drafted`: a run whose critic cannot
        be read, or whose reply is unusable, is `failed`.

    Raises:
        core.artefacts.critic.CriticConfigError: If the critic is not Claude.
    """
    from core.artefacts.critic import assert_critic_provider

    assert_critic_provider(config_path)
    result = AnswerDraft()
    feedback = ""
    for attempt in range(1, MAX_RETRIES + 2):
        last = attempt == MAX_RETRIES + 1
        try:
            run = run_task(
                TASK,
                PROMPT_VERSION,
                {
                    "identity": pack.identity,
                    "question": json.dumps(question),
                    "scaffold": json.dumps(scaffold),
                    "evidence": render_evidence(pack),
                    "feedback": feedback,
                },
                adapters,
                config_path=config_path,
                max_tokens=4096,
            )
        except LlmTaskError as exc:
            result.error = f"the answer was cut off: {exc}"
            if last:
                return result
            feedback = _feedback(
                [Problem("writer", "your reply was cut off; be shorter")]
            )
            continue
        result.calls.append(run.record)
        try:
            reply = _Reply.model_validate(parse_json_response(run.text.strip()))
        except (ValueError, json.JSONDecodeError, AttributeError) as exc:
            result.error = f"unusable writer reply: {exc}"
            if last:
                return result
            feedback = _feedback(
                [Problem("writer", f"your last reply was unusable: {exc}")]
            )
            continue
        sentences = []
        for index, raw in enumerate(reply.sentences):
            built = build_sentence(raw, pack, SECTION, index)
            if built is not None:
                sentences.append(built)
        problems = sentence_problems(
            [(SECTION, i, s) for i, s in enumerate(sentences)], pack
        )
        if len(sentences) < MIN_SENTENCES:
            problems.append(
                Problem("structure", f"write at least {MIN_SENTENCES} sentences")
            )
        structural = [p for p in problems if not p.sentence_id]
        if not problems or last:
            items = [
                CriticItem(
                    s.id,
                    s.text,
                    s.kind,
                    pack.texts(s.sources) if s.kind == "claim" else [],
                )
                for s in sentences
            ]
            try:
                judged = run_critic(
                    items, company="", adapters=adapters, config_path=config_path
                )
            except CriticError as exc:
                result.calls.extend(exc.calls)
                result.error = f"the fact-checker's reply was unusable: {exc}"
                return result
            result.calls.extend(judged.calls)
            texts = {s.id: s.text for s in sentences}
            for verdict in judged.verdicts.values():
                if not verdict.supported:
                    problems.append(
                        Problem(
                            "unsupported",
                            f"{texts[verdict.item_id]!r}: {verdict.issue}",
                            verdict.item_id,
                        )
                    )
        if not problems:
            result.text = " ".join(s.text for s in sentences)
            result.status = "drafted"
            result.error = None
            return result
        if last:
            if structural:
                result.error = "; ".join(p.message for p in structural)
                return result
            by_id: dict[str, list[str]] = {}
            for p in problems:
                by_id.setdefault(p.sentence_id, []).append(p.message)
            result.open_sentences = [
                OpenDraft(s.id, s.text, list(s.sources), "; ".join(by_id[s.id]))
                for s in sentences
                if s.id in by_id
            ]
            result.text = " ".join(s.text for s in sentences)
            result.status = "needs_review"
            result.error = None
            return result
        feedback = _feedback(problems)
    return result
