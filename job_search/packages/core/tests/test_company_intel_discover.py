"""Unit tests for choosing which pages to crawl."""

from __future__ import annotations

import unittest

from core.company_intel.discover import pick_pages, registered_domain

_HOME = "https://www.acme.com/"


class TestRegisteredDomain(unittest.TestCase):
    """registered_domain strips subdomains, including two-part UK suffixes."""

    def test_plain_and_subdomain_hosts(self) -> None:
        self.assertEqual(registered_domain("https://www.acme.com/x"), "acme.com")
        self.assertEqual(registered_domain("https://blog.eng.acme.com"), "acme.com")

    def test_two_part_suffixes(self) -> None:
        self.assertEqual(registered_domain("https://www.acme.co.uk/"), "acme.co.uk")

    def test_garbage_gives_an_empty_domain(self) -> None:
        self.assertEqual(registered_domain("not a url"), "")


class TestPickPages(unittest.TestCase):
    """pick_pages keeps relevant same-site pages, best first."""

    def test_home_comes_first_then_relevant_pages(self) -> None:
        links = [
            "https://www.acme.com/pricing",
            "https://www.acme.com/about",
            "https://www.acme.com/careers",
            "https://www.acme.com/blog/how-we-use-dbt",
            "https://www.acme.com/engineering",
        ]
        picked = pick_pages(_HOME, links, 8)
        self.assertEqual(picked[0], _HOME)
        self.assertEqual(
            set(picked[1:]),
            {
                "https://www.acme.com/about",
                "https://www.acme.com/careers",
                "https://www.acme.com/blog/how-we-use-dbt",
                "https://www.acme.com/engineering",
            },
        )

    def test_other_sites_files_and_duplicates_are_dropped(self) -> None:
        links = [
            "https://twitter.com/acme/about",
            "https://www.acme.com/about#team",
            "https://www.acme.com/about?utm=1",
            "https://www.acme.com/about",
            "https://www.acme.com/about/report.pdf",
            "mailto:hi@acme.com",
            "https://www.acme.com/logo.png",
        ]
        self.assertEqual(
            pick_pages(_HOME, links, 8), [_HOME, "https://www.acme.com/about"]
        )

    def test_the_cap_includes_the_home_page(self) -> None:
        links = [f"https://www.acme.com/blog/post-{i}" for i in range(20)]
        self.assertEqual(len(pick_pages(_HOME, links, 4)), 4)
        self.assertEqual(pick_pages(_HOME, links, 1), [_HOME])

    def test_the_order_is_stable(self) -> None:
        links = ["https://www.acme.com/team", "https://www.acme.com/about"]
        self.assertEqual(
            pick_pages(_HOME, links, 8), pick_pages(_HOME, reversed(links), 8)
        )

    def test_subdomains_of_the_same_site_are_allowed(self) -> None:
        picked = pick_pages(_HOME, ["https://engineering.acme.com/blog"], 8)
        self.assertEqual(picked, [_HOME, "https://engineering.acme.com/blog"])


if __name__ == "__main__":
    unittest.main()
