"""The fabrication critic (Step 17).

Runs on the target provider (Claude) from day one even while the Tailor
runs local: a safety guard validated on a weaker model than the one
running it is worse than no guard (DECISIONS.md §1). This module refuses
to run if `fabrication_critic` is routed anywhere else, and the test
suite asserts the same against the real config.

The critic covers only the semantic gap — whether a reworded line's
claims follow from its cited sources. Everything checkable in code is in
core.tailoring.checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from core.cv.schema import CVTruthBase
from core.llm import gateway
from core.llm.json_response import parse_json_response
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.tailoring.assemble import bullet_index
from core.tailoring.schema import JobContext, StretchAssessment, TailoredDocument

TASK = "fabrication_critic"
REQUIRED_PROVIDER = "anthropic"
PROMPT_VERSION_NUMBER = 1


class CriticConfigError(RuntimeError):
    """The critic is routed to a provider other than Anthropic."""


class CriticError(RuntimeError):
    """The critic's reply could not be used."""


@dataclass(frozen=True)
class CriticItem:
    """One generated line for the critic to judge.

    Attributes:
        item_id: `summary` or `e{role}b{bullet}`.
        text: The generated text.
        sources: The truth-base bullet text(s) it was derived from.
    """

    item_id: str
    text: str
    sources: list[str]


@dataclass(frozen=True)
class Verdict:
    """The critic's judgement of one item.

    Attributes:
        item_id: Which item.
        supported: Whether everything it claims follows from its sources.
        issue: What is unsupported; empty when supported.
    """

    item_id: str
    supported: bool
    issue: str


@dataclass(frozen=True)
class CriticResult:
    """One critic call's outcome.

    Attributes:
        verdicts: A verdict for every judged item (missing answers are
            filled in as unsupported).
        stretch: The seniority/scope judgement of the target title.
        model: The model that answered.
        prompt_version: The prompt file version used, e.g. `claude.v1`.
    """

    verdicts: dict[str, Verdict]
    stretch: StretchAssessment
    model: str
    prompt_version: str


def assert_critic_provider(config_path: Path | None = None) -> None:
    """Refuse to proceed unless the critic is routed to Anthropic.

    Args:
        config_path: Task-config override (tests).

    Raises:
        CriticConfigError: If `fabrication_critic` resolves elsewhere.
    """
    provider = load_task_config(TASK, config_path).provider
    if provider != REQUIRED_PROVIDER:
        raise CriticConfigError(
            f"{TASK} must run on {REQUIRED_PROVIDER!r} (a guard validated on a "
            f"weaker model gives false confidence), but is routed to {provider!r}"
        )


def critic_items(
    document: TailoredDocument, truth_base: CVTruthBase
) -> list[CriticItem]:
    """List the lines the critic must judge: every `reworded` line.

    `original` lines are the user's own text; `orphan` lines are already
    surfaced; `linked` lines were decided by the user.

    Args:
        document: The assembled document.
        truth_base: The truth base (the source of each line's evidence).

    Returns:
        The items, summary first, then bullets in document order.
    """
    known = bullet_index(truth_base)

    def sources(refs: list[str]) -> list[str]:
        return [known[ref][1] for ref in refs if ref in known]

    items: list[CriticItem] = []
    if document.summary is not None and document.summary.origin == "reworded":
        items.append(
            CriticItem(
                "summary",
                document.summary.text,
                sources(document.summary.evidence_refs),
            )
        )
    for role in document.experience:
        for position, bullet in enumerate(role.bullets):
            if bullet.origin == "reworded":
                items.append(
                    CriticItem(
                        f"e{role.truth_index}b{position}",
                        bullet.text,
                        sources(bullet.evidence_refs),
                    )
                )
    return items


def _render_role_history(truth_base: CVTruthBase) -> str:
    """Render the roles (no bullets) the critic compares the title against.

    Args:
        truth_base: The truth base.

    Returns:
        One line per role.
    """
    return "\n".join(
        f"[{index}] {role.title} at {role.company} "
        f"({role.start or '?'} – {role.end or 'present'})"
        for index, role in enumerate(truth_base.experience)
    )


def _parse_verdict(entry: dict) -> Verdict:
    """Parse one verdict entry, failing closed on anything but a clean approval.

    Args:
        entry: One element of the reply's `verdicts` list.

    Returns:
        A supported verdict only if `supported` is the JSON boolean true and
        no issue text is attached; otherwise an unsupported verdict.

    Raises:
        KeyError: If `id` or `supported` is missing.
        TypeError: If `entry` is not a mapping.
    """
    item_id = str(entry["id"])
    supported = entry["supported"]
    raw_issue = entry.get("issue")
    issue = "" if raw_issue is None else str(raw_issue)
    if supported is not True and supported is not False:
        issue = issue or "the critic's verdict was not a boolean"
        return Verdict(item_id, False, issue)
    if supported and issue.strip():
        return Verdict(item_id, False, issue)
    return Verdict(item_id, supported, issue)


def _parse_stretch(raw: object) -> StretchAssessment:
    """Parse the advisory stretch judgement, never failing the run on it.

    Args:
        raw: The reply's `stretch` value.

    Returns:
        The parsed assessment, or the default (not a stretch) when the
        value is missing or malformed — it is advice, unlike the verdicts.
    """
    try:
        return StretchAssessment.model_validate(raw or {})
    except ValidationError:
        return StretchAssessment()


def run_critic(
    document: TailoredDocument,
    truth_base: CVTruthBase,
    job: JobContext,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> CriticResult:
    """Judge a tailored document's reworded lines and the title's stretch.

    Args:
        document: The assembled document.
        truth_base: The truth base.
        job: The target job.
        adapters: LLM adapters keyed by provider; must include `anthropic`.
        config_path: Task-config override (tests).

    Returns:
        The critic's verdicts. An item the critic did not answer is
        recorded as unsupported (fail closed).

    Raises:
        CriticConfigError: If the critic is not routed to Anthropic.
        CriticError: If the reply is not usable JSON of the expected shape.
    """
    assert_critic_provider(config_path)
    config = load_task_config(TASK, config_path)
    template = load_prompt(TASK, config.prompt_family, PROMPT_VERSION_NUMBER)
    prompt_version = f"{config.prompt_family}.v{PROMPT_VERSION_NUMBER}"
    items = critic_items(document, truth_base)
    prompt = template.format(
        role_history=_render_role_history(truth_base),
        # Quoted so a hostile title cannot break out of its line.
        job_title=json.dumps(document.target_title),
        items=json.dumps(
            [{"id": i.item_id, "text": i.text, "sources": i.sources} for i in items],
            indent=2,
        ),
    )
    response = gateway.complete(
        TASK,
        prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        config_path=config_path,
    )
    try:
        data = parse_json_response(response.text.strip())
        answered: dict[str, Verdict] = {}
        for entry in data.get("verdicts", []):
            verdict = _parse_verdict(entry)
            previous = answered.get(verdict.item_id)
            # Fail closed: any rejection of an id outranks an approval.
            if previous is None or (previous.supported and not verdict.supported):
                answered[verdict.item_id] = verdict
        stretch = _parse_stretch(data.get("stretch"))
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        ValidationError,
    ) as exc:
        raise CriticError(f"unusable critic reply: {exc}") from exc

    verdicts = {
        item.item_id: answered.get(
            item.item_id,
            Verdict(item.item_id, False, "the critic gave no verdict for this line"),
        )
        for item in items
    }
    return CriticResult(
        verdicts=verdicts,
        stretch=stretch,
        model=response.model,
        prompt_version=prompt_version,
    )
