"""Decide which company a job is really for (PLAN.md Step 19a).

A manual row wins. Otherwise an LLM reads the posting: is the poster the
employer or an agency, which client (if any) does the description name, and
what is the official website. A website guess is only trusted after its
homepage is fetched and names the company (`verify_website`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from core.company_intel import store
from core.company_intel.fetch import Fetcher, Page
from core.company_intel.llm import run_task
from core.company_intel.schema import Resolution
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter
from core.normalisation.company import normalise_company
from core.tailoring.schema import JobContext, JobSource

_DESCRIPTION_CHARS = 6000
_PROMPT_VERSION = 1


@dataclass(frozen=True)
class ManualOverride:
    """What the user typed to correct a job's company.

    Attributes:
        end_client: The company to research, or None to keep the stored one.
        website_url: Its official website, or None to keep the stored one.
    """

    end_client: str | None = None
    website_url: str | None = None


@dataclass(frozen=True)
class ResolveResult:
    """The outcome of resolving a job's company.

    Attributes:
        resolution: The stored resolution.
        calls: LLM usage records made now (empty when nothing was asked).
    """

    resolution: Resolution
    calls: list[dict]


def parse_resolution_reply(text: str) -> dict:
    """Read the resolution JSON object from an LLM reply.

    Args:
        text: The reply text.

    Returns:
        The decoded object, or an empty dict when it is unusable.
    """
    try:
        parsed = parse_json_response(text.strip())
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean(value: object) -> str | None:
    """Return a stripped string, or None for anything empty or non-text.

    Args:
        value: A JSON value.

    Returns:
        The text, or None for `null`, blanks and the word "null".
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return None if not text or text.lower() in {"null", "none", "unknown"} else text


def _https(value: object) -> str | None:
    """Accept only an http(s) URL.

    Args:
        value: A JSON value.

    Returns:
        The URL, or None.
    """
    url = _clean(value)
    return url if url and re.match(r"^https?://\S+$", url) else None


def resolve_company(
    engine: Engine,
    job: JobContext,
    source: JobSource,
    *,
    adapters: dict[str, LLMAdapter],
    override: ManualOverride | None = None,
    config_path: Path | None = None,
) -> ResolveResult:
    """Resolve (and store) the company a job should be researched under.

    Args:
        engine: The app-role engine.
        job: The job's title, company and description.
        source: Where the posting came from.
        adapters: Provider name to LLM adapter.
        override: A manual correction, which is stored and wins from then on.
        config_path: Task-config override (tests).

    Returns:
        The stored resolution and any LLM usage records made now.
    """
    existing = store.read_resolution(engine, job.job_group_id)
    if override is not None:
        base = existing or Resolution(
            job.job_group_id, job.company, "unknown", None, None, "manual"
        )
        manual = Resolution(
            job_group_id=job.job_group_id,
            posted_company=base.posted_company or job.company,
            poster_kind=base.poster_kind,
            end_client=_clean(override.end_client) or base.end_client,
            website_url=_https(override.website_url) or base.website_url,
            method="manual",
        )
        return ResolveResult(store.save_resolution(engine, manual), [])
    if existing is not None:
        return ResolveResult(existing, [])

    run = run_task(
        "company_resolution",
        _PROMPT_VERSION,
        {
            "title": job.title_for_display or "",
            "company": job.company or "",
            "location": "",
            "urls": ", ".join(source.source_urls) or "none",
            "description": job.description[:_DESCRIPTION_CHARS],
        },
        adapters,
        config_path=config_path,
        max_tokens=512,
    )
    data = parse_resolution_reply(run.text)
    kind = data.get("poster_kind")
    if kind not in ("employer", "agency", "unknown"):
        kind = "unknown"
    if kind == "employer":
        end_client = _clean(job.company)
        method = "employer"
    else:
        end_client = _clean(data.get("end_client"))
        method = "llm"
    resolution = Resolution(
        job_group_id=job.job_group_id,
        posted_company=job.company,
        poster_kind=kind,
        end_client=end_client,
        website_url=_https(data.get("website_url")),
        method=method,
    )
    return ResolveResult(store.save_resolution(engine, resolution), [run.record])


def verify_website(
    fetcher: Fetcher, website_url: str, company_name: str
) -> Page | None:
    """Fetch a candidate homepage and check it names the company.

    Args:
        fetcher: Fetches the homepage.
        website_url: The candidate website.
        company_name: The company the website should belong to.

    Returns:
        The homepage when it loads and its text contains the company's
        normalised name (ignoring case and spacing), else None.
    """
    page = fetcher.fetch(website_url)
    if page.status != "ok":
        return None
    needle = re.sub(r"\s+", " ", (normalise_company(company_name) or "")).casefold()
    haystack = re.sub(r"\s+", " ", page.markdown).casefold()
    return page if needle and needle in haystack else None
