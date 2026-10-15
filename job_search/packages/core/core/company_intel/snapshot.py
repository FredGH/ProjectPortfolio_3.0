"""The company snapshot: industry, ranking, market share, 5-year growth,
headcount and employee satisfaction, from the model's general knowledge
(PLAN.md Step 19a).

None of this can be crawled, so it is never cited and always shown as
unverified. `guard_snapshot` keeps the table honest: six rows in a fixed
order, short values, and "not known" instead of anything unusable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.company_intel.llm import run_task
from core.company_intel.schema import (
    NOT_KNOWN,
    SNAPSHOT_KEYS,
    IntelContent,
    SnapshotItem,
)
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter

_PROMPT_VERSION = 1
_MAX_VALUE_CHARS = 200
_MAX_AS_OF_CHARS = 20
_NOT_KNOWN_WORDS = frozenset(
    {"", "-", "n/a", "na", "none", "null", "unknown", "not known", "i don't know"}
)


class SnapshotError(Exception):
    """The model's reply could not be read as a snapshot.

    Attributes:
        calls: The usage record of the call that was paid for, so the cost of
            an unreadable reply is still accounted for.
    """

    def __init__(self, message: str, calls: list[dict] | None = None) -> None:
        """Describe the failure.

        Args:
            message: What was wrong with the reply.
            calls: Usage records of the call(s) already made.
        """
        super().__init__(message)
        self.calls = calls or []


@dataclass(frozen=True)
class SnapshotResult:
    """A guarded snapshot and what producing it cost.

    Attributes:
        items: The six rows, in `SNAPSHOT_KEYS` order.
        model: The model that wrote it.
        prompt_version: The prompt version used.
        calls: LLM usage records.
    """

    items: list[SnapshotItem]
    model: str
    prompt_version: str
    calls: list[dict]


def _row(raw: object) -> SnapshotItem | None:
    """Read one metric's answer; None when it is not a usable answer.

    Args:
        raw: The model's value for one metric: `{value, as_of}` or a bare string.

    Returns:
        A row with a short `value` and an optional short `as_of`, or None
        when the answer is missing, blank or a "not known" in other words.
    """
    value, as_of = raw, None
    if isinstance(raw, dict):
        value, as_of = raw.get("value"), raw.get("as_of")
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if text.casefold() in _NOT_KNOWN_WORDS:
        return None
    if isinstance(as_of, int) and not isinstance(as_of, bool):
        as_of = str(as_of)
    if not isinstance(as_of, str) or not as_of.strip() or len(as_of) > _MAX_AS_OF_CHARS:
        as_of = None
    return SnapshotItem(
        key="", value=text[:_MAX_VALUE_CHARS], as_of=as_of.strip() if as_of else None
    )


def guard_snapshot(raw: object) -> list[SnapshotItem]:
    """Turn a raw model reply into exactly six honest rows.

    Args:
        raw: The decoded JSON reply.

    Returns:
        One row per `SNAPSHOT_KEYS` entry, in order. Anything missing, blank,
        non-text or phrased as "unknown" becomes `not known` with no year;
        unknown keys are ignored; long values are cut.
    """
    data = raw if isinstance(raw, dict) else {}
    items: list[SnapshotItem] = []
    for key in SNAPSHOT_KEYS:
        row = _row(data.get(key))
        if row is None:
            items.append(SnapshotItem(key=key, value=NOT_KNOWN))
        else:
            items.append(SnapshotItem(key=key, value=row.value, as_of=row.as_of))
    return items


def _render_facts(content: IntelContent) -> str:
    """List the crawled business and size facts for the prompt.

    Args:
        content: The crawled profile.

    Returns:
        One bullet per fact, or `(none)`.
    """
    facts = [*content.business_model, *content.size_signals]
    return "\n".join(f"- {fact.text}" for fact in facts) or "(none)"


def build_snapshot(
    display_name: str,
    content: IntelContent,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> SnapshotResult:
    """Ask the model for the snapshot and guard it.

    Args:
        display_name: The company's name.
        content: The crawled profile, offered as context.
        adapters: Provider name to LLM adapter.
        config_path: Task-config override (tests).

    Returns:
        The six guarded rows and their usage.

    Raises:
        SnapshotError: If the reply is not a JSON object.
    """
    run = run_task(
        "company_snapshot",
        _PROMPT_VERSION,
        {"company": display_name, "facts": _render_facts(content)},
        adapters,
        config_path=config_path,
        max_tokens=1024,
    )
    try:
        raw = parse_json_response(run.text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SnapshotError(
            f"the snapshot reply was not JSON: {exc}", [run.record]
        ) from exc
    if not isinstance(raw, dict):
        raise SnapshotError("the snapshot reply was not a JSON object", [run.record])
    return SnapshotResult(
        items=guard_snapshot(raw),
        model=run.model,
        prompt_version=run.prompt_version,
        calls=[run.record],
    )
