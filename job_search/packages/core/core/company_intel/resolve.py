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


_BARE_DOMAIN_RE = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")


def normalise_website_url(value: object) -> str | None:
    """Turn what a person (or the model) wrote into a usable website URL.

    Args:
        value: A website as typed or returned by the model.

    Returns:
        An http(s) URL: a bare domain such as `acme.com` or `www.acme.co.uk/x`
        gets `https://`; an http(s) URL is kept. Anything else (no dot, spaces,
        another scheme, not text) gives None.
    """
    url = _clean(value)
    if not url:
        return None
    if re.match(r"^https?://\S+$", url):
        return url
    return f"https://{url}" if _BARE_DOMAIN_RE.match(url) else None


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
    calls: list[dict] = []
    # Nothing stored and the override does not name the client: resolve the
    # job automatically first, so a typed website still has a company to
    # belong to.
    if existing is None and (override is None or not _clean(override.end_client)):
        automatic = _resolve_automatically(
            engine, job, source, adapters=adapters, config_path=config_path
        )
        existing, calls = automatic.resolution, automatic.calls
    if override is None:
        assert existing is not None
        return ResolveResult(existing, calls)

    base = existing or Resolution(
        job.job_group_id, job.company, "unknown", None, None, "manual"
    )
    end_client = _clean(override.end_client) or base.end_client
    website = normalise_website_url(override.website_url)
    if website is None and _same_company(end_client, base.end_client):
        # Same client, no website typed: keep the stored one. For a new client
        # the stored website belonged to the old company (often the agency), so
        # it is not carried over.
        website = base.website_url
    manual = Resolution(
        job_group_id=job.job_group_id,
        posted_company=base.posted_company or job.company,
        poster_kind=base.poster_kind,
        end_client=end_client,
        website_url=website,
        method="manual",
    )
    return ResolveResult(store.save_resolution(engine, manual), calls)


def _resolve_automatically(
    engine: Engine,
    job: JobContext,
    source: JobSource,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None,
) -> ResolveResult:
    """Ask the model which company the job is for, and store the answer.

    Args:
        engine: The app-role engine.
        job: The job's title, company and description.
        source: Where the posting came from.
        adapters: Provider name to LLM adapter.
        config_path: Task-config override (tests).

    Returns:
        The stored automatic resolution and its usage record.
    """
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
        website_url=normalise_website_url(data.get("website_url")),
        method=method,
    )
    return ResolveResult(store.save_resolution(engine, resolution), [run.record])


def _same_company(a: str | None, b: str | None) -> bool:
    """Say whether two company names are the same ignoring case and spacing.

    Args:
        a: A company name or None.
        b: A company name or None.

    Returns:
        True when both are present and equal once normalised.
    """

    def key(name: str | None) -> str:
        """Normalise a name for comparison.

        Args:
            name: A company name or None.

        Returns:
            The normalised, case-folded name ("" when missing).
        """
        return re.sub(r"\s+", " ", (normalise_company(name) or "")).casefold()

    return bool(a) and bool(b) and key(a) == key(b)


@dataclass(frozen=True)
class WebsiteCheck:
    """The result of fetching a candidate homepage.

    Attributes:
        page: The homepage when it loaded, else None.
        verified: Whether the homepage text names the company.
        error: Why the homepage did not load, if it did not.
    """

    page: Page | None
    verified: bool
    error: str | None = None


def check_website(
    fetcher: Fetcher, website_url: str, company_name: str
) -> WebsiteCheck:
    """Fetch a candidate homepage; say whether it loaded and names the company.

    Args:
        fetcher: Fetches the homepage.
        website_url: The candidate website.
        company_name: The company the website should belong to.

    Returns:
        A `WebsiteCheck`. `verified` needs the company's normalised name in
        the homepage text (ignoring case and spacing).
    """
    page = fetcher.fetch(website_url)
    if page.status != "ok":
        return WebsiteCheck(None, False, page.error or "the page did not load")
    needle = re.sub(r"\s+", " ", (normalise_company(company_name) or "")).casefold()
    haystack = re.sub(r"\s+", " ", page.markdown).casefold()
    return WebsiteCheck(page, bool(needle) and needle in haystack)


def suggest_website(
    company_name: str,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> tuple[str | None, list[dict]]:
    """Ask the model for a company's official website.

    The answer is only a suggestion: the caller must still fetch it and check
    that it names the company (`check_website`).

    Args:
        company_name: The company to find.
        adapters: Provider name to LLM adapter.
        config_path: Task-config override (tests).

    Returns:
        `(url, calls)`: the normalised website, or None when the model is not
        sure or answers with something unusable; and the usage record.
    """
    run = run_task(
        "company_website",
        _PROMPT_VERSION,
        {"company": company_name},
        adapters,
        config_path=config_path,
        max_tokens=256,
    )
    data = parse_resolution_reply(run.text)
    return normalise_website_url(data.get("website_url")), [run.record]


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
    check = check_website(fetcher, website_url, company_name)
    return check.page if check.verified else None
