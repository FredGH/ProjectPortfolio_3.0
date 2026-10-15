"""The company-intel service: reuse stored intel or research and store a new
version (PLAN.md Step 19a). Steps 19 and 20a call `get_or_refresh_intel`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine

from core.company_intel import store
from core.company_intel.config import CompanyIntelConfig
from core.company_intel.discover import pick_pages
from core.company_intel.extract import ExtractError, extract_intel
from core.company_intel.fetch import Fetcher, Page
from core.company_intel.keys import company_key
from core.company_intel.llm import LlmTaskError, total_usage
from core.company_intel.resolve import ManualOverride, check_website, resolve_company
from core.company_intel.schema import Resolution, StoredIntel
from core.company_intel.snapshot import SnapshotError, build_snapshot
from core.llm.types import LLMAdapter
from core.tailoring.schema import JobContext, JobSource

IntelOutcome = Literal[
    "reused",
    "refreshed",
    "no_content",
    "failed",
    "end_client_unknown",
    "website_unverified",
]


@dataclass(frozen=True)
class IntelResult:
    """What a research call did.

    Attributes:
        outcome: One of `IntelOutcome`.
        resolution: Which company was (or could not be) researched.
        intel: The current stored intel, or None when there is none.
        message: A one-line explanation for the user.
    """

    outcome: IntelOutcome
    resolution: Resolution
    intel: StoredIntel | None
    message: str


def is_fresh(researched_at: datetime, now: datetime, refresh_after_days: int) -> bool:
    """Say whether intel is still inside its refresh window.

    Args:
        researched_at: When the version was written.
        now: The current time.
        refresh_after_days: The window in days.

    Returns:
        True when the version is younger than the window.
    """
    return now - researched_at < timedelta(days=refresh_after_days)


def _page_record(page: Page) -> dict:
    """Describe a crawled page for the stored provenance.

    Args:
        page: A fetched page.

    Returns:
        `{url, status, error}`.
    """
    return {"url": page.url, "status": page.status, "error": page.error}


def get_or_refresh_intel(
    engine: Engine,
    job: JobContext,
    source: JobSource,
    *,
    adapters: dict[str, LLMAdapter],
    fetcher: Fetcher,
    config: CompanyIntelConfig,
    force: bool = False,
    override: ManualOverride | None = None,
    config_path: Path | None = None,
) -> IntelResult:
    """Return a job's company intel, researching only when asked or when none is saved.

    Args:
        engine: The app-role engine.
        job: The job being applied to.
        source: Where the posting came from.
        adapters: Provider name to LLM adapter.
        fetcher: Fetches web pages (wrap it in `CachedFetcher` to cache).
        config: Crawl limits.
        force: Research again even though research is already saved.
        override: A manual end client / website to store first.
        config_path: Task-config override (tests).

    Returns:
        The outcome, the resolution and the current intel. If every page
        fails nothing is written and the previous version stays current.
    """
    resolved = resolve_company(
        engine,
        job,
        source,
        adapters=adapters,
        override=override,
        config_path=config_path,
    )
    resolution, calls = resolved.resolution, list(resolved.calls)
    if resolution.end_client is None:
        return IntelResult(
            "end_client_unknown",
            resolution,
            None,
            "The posting does not name the end client; set it to research it.",
        )
    key = company_key(resolution.end_client)
    if key is None:
        return IntelResult(
            "end_client_unknown", resolution, None, "The company name is unusable."
        )
    latest = store.read_latest_intel(engine, key)
    if latest is not None and not force:
        # Saved research is always reused: age never triggers a paid re-research,
        # only the user's explicit request (`force`) does.
        return IntelResult(
            "reused",
            resolution,
            latest,
            f"Using the saved research (v{latest.version}); "
            "research again to update it.",
        )

    if not resolution.website_url:
        return IntelResult(
            "website_unverified",
            resolution,
            latest,
            "No website is known for this company; enter its website to research it.",
        )
    check = check_website(fetcher, resolution.website_url, resolution.end_client)
    if check.page is None:
        return IntelResult(
            "failed",
            resolution,
            latest,
            f"Could not load {resolution.website_url}: {check.error} "
            "(is the crawler running?)",
        )
    # A manual resolution's website was typed by the user, so it is trusted;
    # a guessed one must name the company on its homepage.
    if not check.verified and resolution.method != "manual":
        return IntelResult(
            "website_unverified",
            resolution,
            latest,
            "The website's homepage does not name this company; enter the "
            "correct website to research it.",
        )
    home = check.page
    pages: list[Page] = [home]
    for url in pick_pages(home.url, home.links, config.max_pages)[1:]:
        pages.append(fetcher.fetch(url))
    good = [page for page in pages if page.status == "ok"]

    try:
        extracted = extract_intel(
            resolution.end_client,
            good,
            latest.content if latest else None,
            adapters=adapters,
            config_path=config_path,
        )
    except (ExtractError, LlmTaskError) as exc:
        return IntelResult("failed", resolution, latest, f"Research failed: {exc}")
    calls.extend(extracted.calls)
    # Judged on the crawled content alone: the snapshot is the model's own
    # knowledge and must not turn an empty crawl into an "ok" version.
    status = "no_content" if extracted.content.is_empty() else "ok"
    try:
        snapshot = build_snapshot(
            resolution.end_client,
            extracted.content,
            adapters=adapters,
            config_path=config_path,
        )
    except SnapshotError as exc:
        calls.extend(exc.calls)  # paid for, even though the reply was unusable
    except LlmTaskError:
        pass  # the table is a bonus; research still succeeds without it
    else:
        extracted.content.snapshot = snapshot.items
        calls.extend(snapshot.calls)
    store.append_intel(
        engine,
        company_key=key,
        display_name=resolution.end_client,
        status=status,
        content=extracted.content,
        pages=[_page_record(page) for page in pages],
        resolved_from_job=job.job_group_id,
        model=extracted.model,
        prompt_version=extracted.prompt_version,
        usage=total_usage(calls),
    )
    stored = store.read_latest_intel(engine, key)
    outcome: IntelOutcome = "no_content" if status == "no_content" else "refreshed"
    return IntelResult(
        outcome, resolution, stored, "Researched and stored a new version."
    )
