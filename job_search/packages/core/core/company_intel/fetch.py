"""Fetch web pages as Markdown through Crawl4AI, with a day-level cache."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol
from urllib.parse import urljoin

import httpx
from sqlalchemy import Engine

from core.company_intel import store


@dataclass(frozen=True)
class Page:
    """One fetched page.

    Attributes:
        url: The URL asked for.
        status: `ok` or `failed`.
        markdown: The page text as Markdown (empty when failed).
        links: The page's internal links, absolute.
        error: Why it failed, if it did.
    """

    url: str
    status: Literal["ok", "failed"]
    markdown: str = ""
    links: tuple[str, ...] = ()
    error: str | None = None


class Fetcher(Protocol):
    """Anything that can fetch a page."""

    def fetch(self, url: str) -> Page:
        """Fetch one page.

        Args:
            url: The page URL.

        Returns:
            The page; a failure is a `failed` page, never an exception.
        """
        ...


def _failed(url: str, error: str) -> Page:
    """Build a failed page.

    Args:
        url: The URL asked for.
        error: Why it failed.

    Returns:
        A `failed` page.
    """
    return Page(url=url, status="failed", error=error)


def parse_crawl_payload(url: str, payload: object) -> Page:
    """Turn a Crawl4AI `/crawl` answer into a page.

    Tolerates `markdown` as a string or as `{raw_markdown, fit_markdown}`
    and links as `{href}` dicts or plain strings.

    Args:
        url: The URL that was asked for.
        payload: The decoded JSON answer.

    Returns:
        The page; anything unusable is a `failed` page with a reason.
    """
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        return _failed(url, "the crawler returned no results")
    result = results[0]
    if not isinstance(result, dict):
        return _failed(url, "the crawler returned an unreadable result")
    if result.get("success") is False:
        return _failed(url, str(result.get("error_message") or "the crawl failed"))
    markdown = result.get("markdown")
    if isinstance(markdown, dict):
        markdown = markdown.get("raw_markdown") or markdown.get("fit_markdown") or ""
    if not isinstance(markdown, str) or not markdown.strip():
        return _failed(url, "the page had no text")
    links_block = result.get("links")
    internal = links_block.get("internal") if isinstance(links_block, dict) else None
    links: list[str] = []
    for item in internal or []:
        href = item.get("href") if isinstance(item, dict) else item
        if isinstance(href, str) and href.strip():
            absolute = urljoin(url, href.strip())
            if absolute not in links:
                links.append(absolute)
    return Page(url=url, status="ok", markdown=markdown, links=tuple(links))


class Crawl4AIFetcher:
    """Fetch pages from a running Crawl4AI service (`POST /crawl`)."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 90.0,
    ) -> None:
        """Point at the service.

        Args:
            base_url: The service URL, e.g. `http://crawl4ai:11235`.
            client: An HTTP client (tests pass one with a mock transport).
            timeout: Seconds to wait for one crawl.
        """
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def fetch(self, url: str) -> Page:
        """Crawl one page, respecting robots.txt.

        Args:
            url: The page URL.

        Returns:
            The page; transport and parsing failures become `failed` pages.
        """
        body = {
            "urls": [url],
            "browser_config": {"type": "BrowserConfig", "params": {"headless": True}},
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {"cache_mode": "bypass", "check_robots_txt": True},
            },
        }
        try:
            response = self._client.post(f"{self._base}/crawl", json=body)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return _failed(url, f"the crawler request failed: {exc}")
        return parse_crawl_payload(url, payload)


class CachedFetcher:
    """Wrap a fetcher with the `intel.crawl_cache` day-level cache."""

    def __init__(
        self,
        inner: Fetcher,
        engine: Engine,
        *,
        cache_days: int,
        today: Callable[[], date] = date.today,
    ) -> None:
        """Wrap a fetcher.

        Args:
            inner: The fetcher used on a cache miss.
            engine: The app-role engine.
            cache_days: A cached copy younger than this is reused.
            today: Clock for tests.
        """
        self._inner = inner
        self._engine = engine
        self._days = cache_days
        self._today = today

    def fetch(self, url: str) -> Page:
        """Return a fresh cached copy or crawl and cache the page.

        Args:
            url: The page URL.

        Returns:
            The page. Only successful pages are cached.
        """
        today = self._today()
        cached = store.read_cached_page(
            self._engine, url, max_age_days=self._days, today=today
        )
        if cached is not None:
            return Page(
                url=url, status="ok", markdown=cached.markdown, links=cached.links
            )
        page = self._inner.fetch(url)
        if page.status == "ok":
            store.write_cached_page(
                self._engine,
                url,
                fetched_on=today,
                markdown=page.markdown,
                links=page.links,
            )
        return page
