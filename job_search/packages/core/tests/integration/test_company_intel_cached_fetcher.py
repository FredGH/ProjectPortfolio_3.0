"""Integration test for the caching fetcher (real Postgres)."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from sqlalchemy import text
from tests.company_intel_fixtures import FakeFetcher
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.company_intel.fetch import CachedFetcher, Page

_URL = "https://zzfixture-fetch.example/about"


class TestCachedFetcher(unittest.TestCase):
    """The cache stops a second crawl within the window."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove the fixture cache rows."""
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM intel.crawl_cache WHERE url LIKE 'https://zzfixture-fetch%'"
                )
            )

    def test_a_second_fetch_the_same_week_does_not_crawl_again(self) -> None:
        inner = FakeFetcher(
            {
                _URL: Page(
                    _URL, "ok", "About us", ("https://zzfixture-fetch.example/jobs",)
                )
            }
        )
        today = date(2026, 10, 14)
        fetcher = CachedFetcher(inner, self.engine, cache_days=7, today=lambda: today)
        first = fetcher.fetch(_URL)
        second = CachedFetcher(
            inner, self.engine, cache_days=7, today=lambda: today + timedelta(days=3)
        ).fetch(_URL)
        self.assertEqual(inner.requested, [_URL])
        self.assertEqual(second.markdown, first.markdown)
        self.assertEqual(second.links, first.links)

    def test_an_old_copy_is_crawled_again(self) -> None:
        inner = FakeFetcher({_URL: Page(_URL, "ok", "About us")})
        today = date(2026, 10, 14)
        CachedFetcher(inner, self.engine, cache_days=7, today=lambda: today).fetch(_URL)
        CachedFetcher(
            inner, self.engine, cache_days=7, today=lambda: today + timedelta(days=30)
        ).fetch(_URL)
        self.assertEqual(inner.requested, [_URL, _URL])

    def test_failed_pages_are_not_cached(self) -> None:
        inner = FakeFetcher({})
        today = date(2026, 10, 14)
        fetcher = CachedFetcher(inner, self.engine, cache_days=7, today=lambda: today)
        self.assertEqual(fetcher.fetch(_URL).status, "failed")
        fetcher.fetch(_URL)
        self.assertEqual(inner.requested, [_URL, _URL])


if __name__ == "__main__":
    unittest.main()
