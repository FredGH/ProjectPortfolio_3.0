"""Extract cited company facts from crawled pages (PLAN.md Step 19a).

The model reads only the crawled text. Everything it returns passes through
`guard_content`: a fact must cite a page that was really crawled, competitors
are capped and tagged, and anything malformed is dropped rather than
repaired.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.company_intel.fetch import Page
from core.company_intel.llm import run_task
from core.company_intel.schema import (
    MAX_COMPETITORS,
    Competitor,
    IntelContent,
    IntelFact,
)
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter

_PROMPT_VERSION = 1
_PAGE_CHARS = 6000
_FACT_FIELDS = (
    "business_model",
    "size_signals",
    "tech_stack",
    "recent_announcements",
    "notes",
)


class ExtractError(Exception):
    """The model's reply could not be read as a profile."""


@dataclass(frozen=True)
class ExtractResult:
    """A guarded profile and what producing it cost.

    Attributes:
        content: The profile after the fabrication guard.
        model: The model that wrote it.
        prompt_version: The prompt version used.
        calls: LLM usage records.
    """

    content: IntelContent
    model: str
    prompt_version: str
    calls: list[dict]


def _facts(raw: object, crawled: set[str]) -> list[IntelFact]:
    """Keep the well-formed, properly cited facts of one list.

    Args:
        raw: The model's value for one fact field.
        crawled: The URLs that were actually crawled.

    Returns:
        The facts whose text is non-blank and whose `source_url` was crawled.
    """
    if not isinstance(raw, list):
        return []
    facts: list[IntelFact] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text, url = item.get("text"), item.get("source_url")
        if (
            isinstance(text, str)
            and text.strip()
            and isinstance(url, str)
            and url in crawled
        ):
            facts.append(IntelFact(text=text.strip(), source_url=url))
    return facts


def _competitors(raw: object, crawled: set[str]) -> list[Competitor]:
    """Apply the competitor rules.

    Args:
        raw: The model's `competitors` value.
        crawled: The URLs that were actually crawled.

    Returns:
        At most `MAX_COMPETITORS` named, de-duplicated competitors. A `site`
        competitor needs a crawled URL; anything else is `model_knowledge`
        with no URL.
    """
    if not isinstance(raw, list):
        return []
    kept: list[Competitor] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        if name.strip().casefold() in seen:
            continue
        why = item.get("why") if isinstance(item.get("why"), str) else ""
        url = (
            item.get("source_url") if isinstance(item.get("source_url"), str) else None
        )
        if item.get("basis") == "site":
            if url not in crawled:
                continue
            competitor = Competitor(
                name=name.strip(), why=why, basis="site", source_url=url
            )
        else:
            competitor = Competitor(name=name.strip(), why=why, basis="model_knowledge")
        seen.add(name.strip().casefold())
        kept.append(competitor)
        if len(kept) == MAX_COMPETITORS:
            break
    return kept


def guard_content(raw: object, crawled: set[str]) -> IntelContent:
    """Apply the fabrication guard to a raw model reply.

    Args:
        raw: The decoded JSON reply.
        crawled: The URLs that were actually crawled.

    Returns:
        The profile with every unsupported or malformed item removed.
    """
    if not isinstance(raw, dict):
        return IntelContent()
    fields = {name: _facts(raw.get(name), crawled) for name in _FACT_FIELDS}
    return IntelContent(
        competitors=_competitors(raw.get("competitors"), crawled), **fields
    )


def _render_pages(pages: list[Page]) -> str:
    """Lay the pages out for the prompt, each cut to a safe length.

    Args:
        pages: The crawled pages.

    Returns:
        The labelled page texts.
    """
    return "\n\n".join(
        f"=== {page.url}\n{page.markdown[:_PAGE_CHARS]}" for page in pages
    )


def extract_intel(
    display_name: str,
    pages: list[Page],
    previous: IntelContent | None,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> ExtractResult:
    """Ask the model for a profile and guard it.

    Args:
        display_name: The company's name.
        pages: The successfully crawled pages.
        previous: The last stored profile, offered as context on a refresh.
        adapters: Provider name to LLM adapter.
        config_path: Task-config override (tests).

    Returns:
        The guarded profile and its usage.

    Raises:
        ExtractError: If the reply is not a JSON object.
    """
    previous_block = ""
    if previous is not None and not previous.is_empty():
        previous_block = (
            "Previous profile (update it: keep what the pages still support, drop "
            "what they contradict, add what is new):\n" + previous.model_dump_json()
        )
    run = run_task(
        "company_intel_extraction",
        _PROMPT_VERSION,
        {
            "company": display_name,
            "previous": previous_block,
            "pages": _render_pages(pages),
        },
        adapters,
        config_path=config_path,
        max_tokens=4096,
    )
    try:
        raw = parse_json_response(run.text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ExtractError(f"the profile reply was not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExtractError("the profile reply was not a JSON object")
    crawled = {page.url for page in pages}
    return ExtractResult(
        content=guard_content(raw, crawled),
        model=run.model,
        prompt_version=run.prompt_version,
        calls=[run.record],
    )
