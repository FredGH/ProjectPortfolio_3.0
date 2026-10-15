"""Unit tests for website normalising and the homepage check."""

from __future__ import annotations

import unittest

from tests.company_intel_fixtures import FakeAdapter, FakeFetcher

from core.company_intel.fetch import Page
from core.company_intel.resolve import (
    check_website,
    normalise_website_url,
    suggest_website,
)


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


class TestSuggestWebsite(unittest.TestCase):
    """suggest_website asks the model for a company's official website."""

    def test_it_returns_a_normalised_url_and_the_usage_record(self) -> None:
        adapter = FakeAdapter(['{"website_url": "www.synapri.com"}'])
        url, calls = suggest_website("Synapri Ltd", adapters={"anthropic": adapter})
        self.assertEqual(url, "https://www.synapri.com")
        self.assertEqual(calls[0]["task"], "company_website")
        self.assertIn("Synapri Ltd", adapter.prompts[0])

    def test_no_answer_junk_and_prose_give_no_url(self) -> None:
        for reply in (
            '{"website_url": null}',
            '{"website_url": "not a url"}',
            "I am not sure",
            "{}",
        ):
            url, calls = suggest_website(
                "Synapri", adapters={"anthropic": FakeAdapter([reply])}
            )
            self.assertIsNone(url, reply)
            self.assertEqual(len(calls), 1, reply)

    def test_a_company_name_with_braces_cannot_break_the_prompt(self) -> None:
        adapter = FakeAdapter(['{"website_url": null}'])
        suggest_website("Odd {Name} Ltd", adapters={"anthropic": adapter})
        self.assertIn("Odd {Name} Ltd", adapter.prompts[0])


if __name__ == "__main__":
    unittest.main()
