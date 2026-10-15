"""Shared fakes and builders for the company-intel tests."""

from __future__ import annotations

from collections.abc import Sequence

from core.company_intel.fetch import Page
from core.llm.types import LLMResponse
from core.tailoring.schema import JobContext, JobSkill, JobSource


class FakeAdapter:
    """An LLM adapter that returns scripted replies and records prompts.

    Attributes:
        prompts: Every prompt received, in order.
    """

    def __init__(self, replies: Sequence[str]) -> None:
        """Script the replies.

        Args:
            replies: The reply texts, returned in order (the last repeats).
        """
        self._replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, *, model: str, prompt: str, **_kwargs: object) -> LLMResponse:
        """Return the next scripted reply.

        Args:
            model: The requested model (echoed back).
            prompt: The prompt (recorded).
            **_kwargs: Ignored sampling options.

        Returns:
            A response with fixed token counts.
        """
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self._replies) - 1)
        return LLMResponse(
            text=self._replies[index],
            provider="anthropic",
            model=model,
            input_tokens=100,
            output_tokens=50,
        )


class FakeFetcher:
    """A fetcher serving canned pages; unknown URLs fail.

    Attributes:
        requested: Every URL asked for, in order.
    """

    def __init__(self, pages: dict[str, Page]) -> None:
        """Hold the canned pages.

        Args:
            pages: URL to page.
        """
        self._pages = pages
        self.requested: list[str] = []

    def fetch(self, url: str) -> Page:
        """Return the canned page or a failed one.

        Args:
            url: The URL asked for.

        Returns:
            The canned page, or a `failed` page for an unknown URL.
        """
        self.requested.append(url)
        return self._pages.get(
            url, Page(url=url, status="failed", error="not in the fake")
        )


def make_job(**overrides: object) -> JobContext:
    """Build an invented job.

    Args:
        **overrides: Fields to replace.

    Returns:
        A `JobContext` for a fixture employer.
    """
    base: dict[str, object] = {
        "job_group_id": "zzfixture-intel-job",
        "title_for_display": "Data Engineer",
        "company": "Zzfixture Intel Ltd",
        "description": "Build dbt and Snowflake pipelines to replace our batch ETL.",
        "skills": [JobSkill("s1", "dbt", "must_have")],
    }
    return JobContext(**{**base, **overrides})  # type: ignore[arg-type]


def make_source(**overrides: object) -> JobSource:
    """Build an invented job source.

    Args:
        **overrides: Fields to replace.

    Returns:
        A `JobSource` with one URL.
    """
    base: dict[str, object] = {
        "apply_url": "https://jobs.example/zz/1",
        "source_urls": ("https://jobs.example/zz/1",),
        "source_names": ("fixture",),
    }
    return JobSource(**{**base, **overrides})  # type: ignore[arg-type]
