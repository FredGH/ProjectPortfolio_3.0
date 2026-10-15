"""Generate the shared question bank (PLAN.md Step 20).

On demand and idempotent: a topic that already has its approved questions is
skipped, so promoting a new category generates only that category. A question
is `approved` only when the Claude critic judged it supported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine

from core.company_intel.llm import LlmTaskError
from core.interview import store
from core.interview.config import (
    TopicsConfig,
    WorkItem,
    active_categories,
    load_topics_config,
    plan_work,
)
from core.interview.critic import CriticError, assert_critic_provider, judge
from core.interview.write import (
    BATCH_SIZE,
    PROMPT_VERSION,
    WriterError,
    parse_batch,
    question_hash,
    write_batch,
)
from core.llm.types import LLMAdapter

COST_PER_BATCH_USD = 0.08
"""Rough cost of one writer batch plus its critic call (measured on real runs)."""
EXTRA_ROUNDS = 2


@dataclass
class TopicResult:
    """What generating one topic achieved.

    Attributes:
        category: The qa_category.
        topic: The topic.
        kind: `technical` or `behavioural`.
        approved: Questions approved this run.
        needs_review: Questions the critic rejected, stored for review.
        rounds: Writer batches asked for.
        error: Why the topic stopped early, if it did.
        calls: Usage records of every LLM call.
    """

    category: str
    topic: str
    kind: str
    approved: int = 0
    needs_review: int = 0
    rounds: int = 0
    error: str | None = None
    calls: list[dict] = field(default_factory=list)


def estimate_cost(work: list[WorkItem]) -> float:
    """Estimate what generating the work will cost.

    Args:
        work: The incomplete topics.

    Returns:
        A rough dollar figure: one writer batch plus critic call per ten
        missing questions.
    """
    batches = sum(math.ceil(item.missing / BATCH_SIZE) for item in work)
    return round(batches * COST_PER_BATCH_USD, 2)


def generate_topic(
    engine: Engine,
    item: WorkItem,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> TopicResult:
    """Generate the missing questions of one topic.

    Args:
        engine: The app-role engine.
        item: The topic and how many approved questions it still needs.
        adapters: LLM adapters by provider; must include `anthropic`.
        config_path: Task-config override (tests).

    Returns:
        The result. Critic-rejected questions are stored `needs_review`,
        never `approved`; an unusable critic or writer reply stops the topic
        with `error` set and stores nothing from that batch.

    Raises:
        core.interview.critic.CriticConfigError: If the critic is not Claude.
    """
    assert_critic_provider(config_path)
    result = TopicResult(item.category, item.topic, item.kind)
    remaining = item.missing
    max_rounds = math.ceil(item.missing / BATCH_SIZE) + EXTRA_ROUNDS
    while remaining > 0 and result.rounds < max_rounds:
        result.rounds += 1
        existing = store.topic_questions(engine, item.category, item.topic)
        try:
            run = write_batch(
                item.category,
                item.topic,
                item.kind,
                min(BATCH_SIZE, remaining),
                existing,
                adapters=adapters,
                config_path=config_path,
            )
        except LlmTaskError as exc:
            result.error = f"the writer's reply was cut off: {exc}"
            return result
        result.calls.append(run.record)
        try:
            candidates = parse_batch(run.text)
        except WriterError as exc:
            result.error = str(exc)
            continue
        seen = {question_hash(q) for q in existing}
        fresh = []
        for candidate in candidates:
            h = question_hash(candidate.question)
            if h not in seen:
                seen.add(h)
                fresh.append((h, candidate))
        if not fresh:
            continue
        items = [
            {
                "id": h,
                "kind": item.kind,
                "question": c.question,
                "model_answer": c.model_answer,
            }
            for h, c in fresh
        ]
        try:
            judged = judge(items, adapters=adapters, config_path=config_path)
        except CriticError as exc:
            result.calls.extend(exc.calls)
            result.error = f"the reviewer's reply was unusable: {exc}"
            return result
        result.calls.append(judged.record)
        rows = []
        for h, c in fresh:
            verdict = judged.verdicts[h]
            rows.append(
                store.NewQuestion(
                    qa_category=item.category,
                    topic=item.topic,
                    kind=item.kind,
                    difficulty=c.difficulty,
                    question=c.question,
                    question_hash=h,
                    model_answer=c.model_answer,
                    tags=c.tags,
                    status="approved" if verdict.supported else "needs_review",
                    critic_issue=None if verdict.supported else verdict.issue,
                    writer_model=run.model,
                    writer_prompt_version=run.prompt_version,
                    critic_model=judged.model,
                    critic_prompt_version=judged.prompt_version,
                )
            )
        store.insert_questions(engine, rows)
        approved = sum(1 for r in rows if r.status == "approved")
        result.approved += approved
        result.needs_review += len(rows) - approved
        remaining -= approved
        result.error = None
    if remaining > 0 and result.error is None:
        result.error = (
            f"{remaining} question(s) still missing after {result.rounds} round(s)"
        )
    return result


def pending_work(
    engine: Engine,
    *,
    category: str | None = None,
    topic: str | None = None,
    config: TopicsConfig | None = None,
    categories: list[str] | None = None,
) -> tuple[list[WorkItem], list[str]]:
    """Work out what still needs generating, without spending anything.

    Args:
        engine: The app-role engine.
        category: Restrict to one category.
        topic: Restrict to one topic of that category.
        config: Topics config override (tests).
        categories: Active categories override (tests).

    Returns:
        `(work, unconfigured)` as `plan_work` returns them.
    """
    return plan_work(
        config or load_topics_config(),
        categories if categories is not None else active_categories(),
        store.approved_counts(engine),
        only_category=category,
        only_topic=topic,
    )


def generate_work(
    engine: Engine,
    work: list[WorkItem],
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> list[TopicResult]:
    """Generate every incomplete topic in `work`.

    Args:
        engine: The app-role engine.
        work: What `pending_work` returned.
        adapters: LLM adapters by provider.
        config_path: Task-config override (tests).

    Returns:
        One result per topic, in order. A topic that errors does not stop the
        others.
    """
    assert_critic_provider(config_path)
    return [
        generate_topic(engine, item, adapters=adapters, config_path=config_path)
        for item in work
    ]


__all__ = [
    "PROMPT_VERSION",
    "TopicResult",
    "estimate_cost",
    "generate_topic",
    "generate_work",
    "pending_work",
]
