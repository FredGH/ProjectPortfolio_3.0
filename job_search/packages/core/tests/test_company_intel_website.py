"""Unit tests for website normalising and the homepage check."""

from __future__ import annotations

import unittest

from tests.company_intel_fixtures import FakeFetcher

from core.company_intel.fetch import Page
from core.company_intel.resolve import check_website, normalise_website_url


class TestNormaliseWebsiteUrl(unittest.TestCase):
    """normalise_website_url accepts what people type and rejects junk."""

    def test_a_bare_domain_gets_https(self) -> None:
        self.assertEqual(normalise_website_url("acme.com"), "https://acme.com")
        self.assertEqual(
            normalise_website_url("  www.acme.co.uk/about  "),
            "https://www.acme.co.uk/about",
        )

    def test_http_and_https_urls_are_kept(self) -> None:
        self.assertEqual(normalise_website_url("http://acme.com"), "http://acme.com")
        self.assertEqual(
            normalise_website_url("https://acme.com/x"), "https://acme.com/x"
        )

    def test_junk_is_rejected(self) -> None:
        for junk in (None, "", "   ", "acme", "not a url", "ftp://acme.com", 5):
            self.assertIsNone(normalise_website_url(junk), junk)


class TestCheckWebsite(unittest.TestCase):
    """check_website says whether the homepage loaded and names the company."""

    def test_a_homepage_naming_the_company_is_verified(self) -> None:
        page = Page("https://acme.example", "ok", "Welcome to ACME")
        check = check_website(
            FakeFetcher({"https://acme.example": page}),
            "https://acme.example",
            "Acme Ltd",
        )
        self.assertEqual((check.page, check.verified, check.error), (page, True, None))

    def test_a_homepage_naming_someone_else_loads_but_is_not_verified(self) -> None:
        page = Page("https://other.example", "ok", "Welcome to Other Co")
        check = check_website(
            FakeFetcher({"https://other.example": page}),
            "https://other.example",
            "Acme",
        )
        self.assertEqual((check.page, check.verified, check.error), (page, False, None))

    def test_a_homepage_that_does_not_load_reports_why(self) -> None:
        fetcher = FakeFetcher(
            {
                "https://x.example": Page(
                    "https://x.example", "failed", error="crawler down"
                )
            }
        )
        check = check_website(fetcher, "https://x.example", "Acme")
        self.assertEqual((check.page, check.verified), (None, False))
        self.assertEqual(check.error, "crawler down")


if __name__ == "__main__":
    unittest.main()
