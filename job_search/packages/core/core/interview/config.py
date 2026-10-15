"""Interview-bank settings and the work still to do (PLAN.md Step 20).

The active categories are read from `config/category_map.yml`; the topics per
category from `config/interview_topics.yml`. Nothing is hardcoded here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.classification.classify import load_qa_category_map

_DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "interview_topics.yml"
BEHAVIOURAL_TOPIC = "behavioural"
"""The topic name under which a category's behavioural questions are stored."""


@dataclass(frozen=True)
class TopicsConfig:
    """The bank's size targets and topics.

    Attributes:
        target_per_topic: Approved technical questions wanted per topic.
        behavioural_per_category: Approved behavioural questions per category.
        topics: Topics by qa_category.
    """

    target_per_topic: int
    behavioural_per_category: int
    topics: dict[str, list[str]]


@dataclass(frozen=True)
class WorkItem:
    """A topic that still needs questions.

    Attributes:
        category: The qa_category.
        topic: The topic (`BEHAVIOURAL_TOPIC` for behavioural questions).
        kind: `technical` or `behavioural`.
        missing: How many more approved questions are wanted.
    """

    category: str
    topic: str
    kind: str
    missing: int


def load_topics_config(path: Path | None = None) -> TopicsConfig:
    """Read the topics config.

    Args:
        path: Override for tests; defaults to `config/interview_topics.yml`.

    Returns:
        The parsed config.

    Raises:
        ValueError: If a target is not a positive integer.
    """
    raw = yaml.safe_load((path or _DEFAULT_PATH).read_text()) or {}
    values = {}
    for key in ("target_per_topic", "behavioural_per_category"):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{key} must be a positive integer, got {value!r}")
        values[key] = value
    topics = {
        category: [str(t) for t in (block or {}).get("topics", [])]
        for category, block in (raw.get("categories") or {}).items()
    }
    return TopicsConfig(
        values["target_per_topic"], values["behavioural_per_category"], topics
    )


def active_categories(category_map_path: Path | None = None) -> list[str]:
    """List the active qa_categories.

    Args:
        category_map_path: Override for tests; defaults to
            `config/category_map.yml`.

    Returns:
        The distinct non-null values of `qa_category_map`, sorted.
    """
    return sorted({v for v in load_qa_category_map(category_map_path).values() if v})


def plan_work(
    config: TopicsConfig,
    categories: list[str],
    approved_counts: Mapping[tuple[str, str], int],
    *,
    only_category: str | None = None,
    only_topic: str | None = None,
) -> tuple[list[WorkItem], list[str]]:
    """Work out what still needs generating.

    Args:
        config: The topics config.
        categories: The active categories.
        approved_counts: Approved questions per `(category, topic)`; behavioural
            ones are counted under `BEHAVIOURAL_TOPIC`.
        only_category: Restrict to one category.
        only_topic: Restrict to one topic of that category (`BEHAVIOURAL_TOPIC`
            for the behavioural set).

    Returns:
        `(work, unconfigured)`: the incomplete topics (complete ones are
        skipped, which is what makes generation idempotent) and the active
        categories that have no topics block.
    """
    work: list[WorkItem] = []
    unconfigured: list[str] = []
    for category in categories:
        if only_category is not None and category != only_category:
            continue
        topics = config.topics.get(category)
        if not topics:
            unconfigured.append(category)
            continue
        wanted = [(t, "technical", config.target_per_topic) for t in topics]
        wanted.append(
            (BEHAVIOURAL_TOPIC, "behavioural", config.behavioural_per_category)
        )
        for topic, kind, target in wanted:
            if only_topic is not None and topic != only_topic:
                continue
            have = approved_counts.get((category, topic), 0)
            if have < target:
                work.append(WorkItem(category, topic, kind, target - have))
    return work, unconfigured
