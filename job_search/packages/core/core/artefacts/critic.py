"""The prose critic (PLAN.md Step 19).

Like the CV's fabrication critic it runs on Claude from day one: a guard
validated on a weaker model than the one running it gives false confidence.
It judges only the semantic gap; everything checkable in code is in
core.artefacts.checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.artefacts.checks import iter_sentences
from core.artefacts.context import EvidencePack
from core.artefacts.schema import CoverLetter, Pitch
from core.company_intel.llm import LlmTaskError, run_task
from core.llm.json_response import parse_json_response
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.tailoring.critic import Verdict

TASK = "artefact_critic"
REQUIRED_PROVIDER = "anthropic"
PROMPT_VERSION_NUMBER = 1


class CriticConfigError(RuntimeError):
    """The critic is routed to a provider other than Anthropic."""


class CriticError(RuntimeError):
    """The critic's reply could not be used.

    Attributes:
        calls: Usage records of the call(s) already paid for.
    """

    def __init__(self, message: str, calls: list[dict] | None = None) -> None:
        """Describe the failure.

        Args:
            message: What was wrong.
            calls: Usage records of the call(s) already made.
        """
        super().__init__(message)
        self.calls = calls or []


@dataclass(frozen=True)
class CriticItem:
    """One sentence for the critic to judge.

    Attributes:
        item_id: The sentence id.
        text: The sentence.
        kind: `claim` or `voice`.
        sources: The texts of the evidence it cites (empty for voice).
    """

    item_id: str
    text: str
    kind: str
    sources: list[str]


@dataclass(frozen=True)
class CriticResult:
    """One critic call's outcome.

    Attributes:
        verdicts: A verdict per item; unanswered items are unsupported.
        model: The model that answered ("" when no call was made).
        prompt_version: The prompt version used ("" when no call was made).
        calls: Usage records.
    """

    verdicts: dict[str, Verdict]
    model: str
    prompt_version: str
    calls: list[dict]


def assert_critic_provider(config_path: Path | None = None) -> None:
    """Refuse to proceed unless the critic is routed to Anthropic.

    Args:
        config_path: Task-config override (tests).

    Raises:
        CriticConfigError: If `artefact_critic` resolves elsewhere.
    """
    provider = load_task_config(TASK, config_path).provider
    if provider != REQUIRED_PROVIDER:
        raise CriticConfigError(
            f"{TASK} must run on {REQUIRED_PROVIDER!r} (a guard validated on a "
            f"weaker model gives false confidence), but is routed to {provider!r}"
        )


def critic_items(doc: CoverLetter | Pitch, pack: EvidencePack) -> list[CriticItem]:
    """List the sentences the critic must judge.

    Args:
        doc: The assembled document.
        pack: The evidence pack.

    Returns:
        Every claim and voice sentence (composed ones are the code's own
        words and are not judged), with the texts of the valid sources a
        claim cites.
    """
    return [
        CriticItem(
            item_id=sentence.id,
            text=sentence.text,
            kind=sentence.kind,
            sources=pack.texts(sentence.sources) if sentence.kind == "claim" else [],
        )
        for _section, _index, sentence in iter_sentences(doc)
        if sentence.kind in ("claim", "voice")
    ]


def _verdict(entry: dict) -> Verdict:
    """Parse one verdict entry, failing closed on anything but a clean approval.

    Args:
        entry: One element of the reply's `verdicts` list.

    Returns:
        A supported verdict only if `supported` is the JSON boolean true and
        no issue text is attached.

    Raises:
        KeyError: If `id` or `supported` is missing.
        TypeError: If `entry` is not a mapping.
    """
    item_id = str(entry["id"])
    supported = entry["supported"]
    raw_issue = entry.get("issue")
    issue = "" if raw_issue is None else str(raw_issue)
    if supported is not True and supported is not False:
        return Verdict(
            item_id, False, issue or "the critic's verdict was not a boolean"
        )
    if supported and issue.strip():
        return Verdict(item_id, False, issue)
    return Verdict(item_id, supported, issue)


def run_critic(
    items: list[CriticItem],
    *,
    company: str,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> CriticResult:
    """Judge a document's sentences.

    Args:
        items: The sentences to judge (see `critic_items`).
        company: The target company's name.
        adapters: LLM adapters by provider; must include `anthropic`.
        config_path: Task-config override (tests).

    Returns:
        The verdicts. An item the critic did not answer is unsupported (fail
        closed). With no items, no call is made.

    Raises:
        CriticConfigError: If the critic is not routed to Anthropic.
        CriticError: If the reply is unusable or cut off (its `calls` carry
            the cost already incurred).
    """
    assert_critic_provider(config_path)
    if not items:
        return CriticResult({}, "", "", [])
    payload = json.dumps(
        [
            {"id": i.item_id, "kind": i.kind, "text": i.text, "sources": i.sources}
            for i in items
        ],
        indent=2,
    )
    try:
        run = run_task(
            TASK,
            PROMPT_VERSION_NUMBER,
            {"company": json.dumps(company), "items": payload},
            adapters,
            config_path=config_path,
            max_tokens=4096,
        )
    except LlmTaskError as exc:
        raise CriticError(f"the critic's reply was cut off: {exc}") from exc
    try:
        data = parse_json_response(run.text)
        answered: dict[str, Verdict] = {}
        for entry in data.get("verdicts", []):  # type: ignore[attr-defined]
            verdict = _verdict(entry)
            previous = answered.get(verdict.item_id)
            if previous is None or (previous.supported and not verdict.supported):
                answered[verdict.item_id] = verdict
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        ValueError,
    ) as exc:
        raise CriticError(f"unusable critic reply: {exc}", [run.record]) from exc
    verdicts = {
        item.item_id: answered.get(
            item.item_id, Verdict(item.item_id, False, "the critic gave no verdict")
        )
        for item in items
    }
    return CriticResult(verdicts, run.model, run.prompt_version, [run.record])
