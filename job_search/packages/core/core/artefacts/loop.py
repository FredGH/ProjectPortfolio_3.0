"""The generate loop (PLAN.md Step 19): write, assemble, check, judge, retry.

Nothing unchecked is ever approved: what is still untraced after the retries
becomes an open sentence for the user, and a run whose critic cannot be read
is `failed`, not approved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from core.artefacts.assemble import (
    WriterError,
    build_letter,
    build_pitch,
    parse_writer_reply,
)
from core.artefacts.checks import SENTENCE_CODES, Problem, iter_sentences, run_checks
from core.artefacts.context import EvidencePack
from core.artefacts.critic import (
    CriticError,
    assert_critic_provider,
    critic_items,
    run_critic,
)
from core.artefacts.schema import CoverLetter, Kind, Pitch
from core.artefacts.store import OpenDraft
from core.artefacts.write import write
from core.company_intel.llm import LlmTaskError
from core.llm.types import LLMAdapter

MAX_RETRIES = 2


@dataclass
class Generation:
    """The outcome of one generation.

    Attributes:
        document: The final document, or None when the run failed before one
            could be assembled.
        status: `approved`, `needs_review` or `failed`.
        open_sentences: Sentences awaiting the user's decision.
        attempts: Writer attempts made.
        calls: Usage records of every LLM call.
        writer_model: The writer model that answered last.
        writer_prompt_version: Its prompt version.
        critic_model: The critic model that answered last ("" if never run).
        critic_prompt_version: Its prompt version.
        error: Why the run failed, if it did.
    """

    document: CoverLetter | Pitch | None = None
    status: str = "failed"
    open_sentences: list[OpenDraft] = field(default_factory=list)
    attempts: int = 0
    calls: list[dict] = field(default_factory=list)
    writer_model: str = ""
    writer_prompt_version: str = ""
    critic_model: str = ""
    critic_prompt_version: str = ""
    error: str | None = None


def _feedback(problems: list[Problem]) -> str:
    """Turn problems into feedback for the writer's next attempt.

    Args:
        problems: What was wrong with the last attempt.

    Returns:
        A block to splice into the prompt (data, not template).
    """
    lines = "\n".join(f"- {p.message}" for p in problems)
    return (
        "\nYour previous attempt was rejected for these reasons:\n"
        f"{lines}\n"
        "Fix each one by removing the unsupported claim, or by citing a listed id "
        "that really supports it. Do not rephrase around a problem.\n"
    )


def _open_drafts(
    document: CoverLetter | Pitch, problems: list[Problem]
) -> list[OpenDraft]:
    """Turn the remaining sentence-level problems into open sentences.

    Args:
        document: The final document; flagged sentences are updated in place.
        problems: The remaining problems.

    Returns:
        One draft per sentence with problems, in document order.
    """
    by_sentence: dict[str, list[Problem]] = {}
    for problem in problems:
        if problem.sentence_id:
            by_sentence.setdefault(problem.sentence_id, []).append(problem)
    drafts: list[OpenDraft] = []
    for section, index, sentence in iter_sentences(document):
        found = by_sentence.get(sentence.id)
        if not found:
            continue
        kind = "orphan" if sentence.flag == "orphan" else "unsupported"
        sentence.flag = kind  # type: ignore[assignment]
        drafts.append(
            OpenDraft(
                sentence_id=sentence.id,
                location=f"{section}:{index + 1}",
                kind=kind,
                text=sentence.text,
                claimed_sources=list(sentence.sources),
                issue="; ".join(p.message for p in found),
            )
        )
    return drafts


def generate(
    kind: Kind,
    pack: EvidencePack,
    *,
    adapters: dict[str, LLMAdapter],
    today: date,
    config_path: Path | None = None,
) -> Generation:
    """Generate a letter or a pitch, checked end to end.

    The critic runs once the code checks find nothing at all, and always on
    the last attempt. Length problems are fed back to the writer but never
    block the end result.

    Args:
        kind: `cover_letter` or `pitch`.
        pack: The evidence pack.
        adapters: LLM adapters by provider; must include `anthropic`.
        today: The date shown on a letter.
        config_path: Task-config override (tests).

    Returns:
        The outcome: `approved` when every sentence is traced and judged
        supported; `needs_review` with open sentences otherwise; `failed`
        when no usable document came out or the critic could not run.

    Raises:
        core.artefacts.critic.CriticConfigError: If the critic is not routed
            to Anthropic (checked before anything is spent).
    """
    assert_critic_provider(config_path)
    result = Generation()
    feedback = ""
    blocking: list[Problem] = []
    for attempt in range(1, MAX_RETRIES + 2):
        last = attempt == MAX_RETRIES + 1
        result.attempts = attempt
        try:
            run = write(
                kind,
                pack,
                feedback=feedback,
                adapters=adapters,
                config_path=config_path,
            )
        except LlmTaskError as exc:
            result.error = f"the writer's reply was cut off: {exc}"
            if last:
                return result
            feedback = _feedback(
                [Problem("writer", "your reply was cut off; write less")]
            )
            continue
        result.calls.append(run.record)
        result.writer_model, result.writer_prompt_version = (
            run.model,
            run.prompt_version,
        )
        try:
            reply = parse_writer_reply(run.text, kind)
        except WriterError as exc:
            result.error = str(exc)
            if last:
                return result
            feedback = _feedback(
                [Problem("writer", f"your last reply was unusable: {exc}")]
            )
            continue
        document = (
            build_letter(pack, reply, today=today)  # type: ignore[arg-type]
            if kind == "cover_letter"
            else build_pitch(pack, reply)  # type: ignore[arg-type]
        )
        problems = run_checks(document, pack)
        if not problems or last:
            try:
                judged = run_critic(
                    critic_items(document, pack),
                    company=pack.company,
                    adapters=adapters,
                    config_path=config_path,
                )
            except CriticError as exc:
                result.calls.extend(exc.calls)
                result.document = document
                result.error = f"the fact-checker's reply was unusable: {exc}"
                return result
            result.calls.extend(judged.calls)
            result.critic_model = judged.model or result.critic_model
            result.critic_prompt_version = (
                judged.prompt_version or result.critic_prompt_version
            )
            texts = {s.id: s.text for _sec, _i, s in iter_sentences(document)}
            for verdict in judged.verdicts.values():
                if not verdict.supported:
                    problems.append(
                        Problem(
                            "unsupported",
                            f"{texts[verdict.item_id]!r}: {verdict.issue}",
                            verdict.item_id,
                        )
                    )
        blocking = [p for p in problems if p.code != "length"]
        result.document = document
        if not blocking and (not problems or last):
            result.status = "approved"
            result.error = None
            return result
        if last:
            break
        feedback = _feedback(problems)
    document = result.document
    assert document is not None
    structural = [p for p in blocking if p.code not in SENTENCE_CODES | {"unsupported"}]
    if structural:
        result.status = "failed"
        result.error = "; ".join(p.message for p in structural)
        return result
    result.open_sentences = _open_drafts(document, blocking)
    result.status = "needs_review"
    result.error = None
    return result
