"""Unit tests for the Crawl4AI fetcher (no network)."""

from __future__ import annotations

import json
import unittest

import httpx

from core.company_intel.fetch import Crawl4AIFetcher, Page, parse_crawl_payload

_URL = "https://acme.example/"


class TestParseCrawlPayload(unittest.TestCase):
    """parse_crawl_payload turns any Crawl4AI answer into a Page."""

    def test_string_markdown_and_dict_links(self) -> None:
        page = parse_crawl_payload(
            _URL,
            {
                "success": True,
                "results": [
                    {
                        "success": True,
                        "markdown": "# Acme\nWe make widgets.",
                        "links": {
                            "internal": [
                                {"href": "/about"},
                                {"href": "https://acme.example/careers"},
                            ]
                        },
                    }
                ],
            },
        )
        self.assertEqual(page.status, "ok")
        self.assertIn("widgets", page.markdown)
        self.assertEqual(
            page.links, ("https://acme.example/about", "https://acme.example/careers")
        )

    def test_dict_markdown_prefers_raw_markdown_and_string_links(self) -> None:
        page = parse_crawl_payload(
            _URL,
            {
                "results": [
                    {
                        "success": True,
                        "markdown": {"raw_markdown": "Raw text", "fit_markdown": "Fit"},
                        "links": {"internal": ["https://acme.example/blog"]},
                    }
                ]
            },
        )
        self.assertEqual(
            (page.markdown, page.links), ("Raw text", ("https://acme.example/blog",))
        )

    def test_fit_markdown_is_used_when_raw_is_empty(self) -> None:
        page = parse_crawl_payload(
            _URL,
            {
                "results": [
                    {
                        "success": True,
                        "markdown": {"raw_markdown": "", "fit_markdown": "Fit"},
                    }
                ]
            },
        )
        self.assertEqual(page.markdown, "Fit")

    def test_a_failed_result_carries_its_error(self) -> None:
        page = parse_crawl_payload(
            _URL,
            {"results": [{"success": False, "error_message": "blocked by robots.txt"}]},
        )
        self.assertEqual(page.status, "failed")
        self.assertIn("robots", page.error or "")

    def test_odd_payloads_never_raise(self) -> None:
        for payload in (None, [], {}, {"results": []}, {"results": ["x"]}, "oops"):
            page = parse_crawl_payload(_URL, payload)
            self.assertEqual(page.status, "failed", payload)
            self.assertTrue(page.error)

    def test_a_page_with_no_text_is_a_failure(self) -> None:
        page = parse_crawl_payload(
            _URL, {"results": [{"success": True, "markdown": "  "}]}
        )
        self.assertEqual(page.status, "failed")

    def test_a_malformed_link_is_skipped_not_raised(self) -> None:
        page = parse_crawl_payload(
            _URL,
            {
                "results": [
                    {
                        "success": True,
                        "markdown": "Hello",
                        "links": {
                            "internal": [
                                {"href": "http://[::1"},
                                {"href": "/about"},
                                None,
                                42,
                            ]
                        },
                    }
                ]
            },
        )
        self.assertEqual(page.status, "ok")
        self.assertEqual(page.links, ("https://acme.example/about",))


class TestCrawl4AIFetcher(unittest.TestCase):
    """Crawl4AIFetcher posts to /crawl and tolerates transport errors."""

    def _fetcher(self, handler) -> Crawl4AIFetcher:  # type: ignore[no-untyped-def]
        """Build a fetcher over a mock transport.

        Args:
            handler: A request handler for `httpx.MockTransport`.

        Returns:
            The fetcher.
        """
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return Crawl4AIFetcher("http://crawl4ai:11235/", client=client)

    def test_it_posts_the_url_with_robots_checking_on(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"results": [{"success": True, "markdown": "Hello"}]}
            )

        page = self._fetcher(handler).fetch(_URL)
        self.assertEqual(page.markdown, "Hello")
        self.assertEqual(seen["url"], "http://crawl4ai:11235/crawl")
        body = seen["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["urls"], [_URL])
        self.assertTrue(body["crawler_config"]["params"]["check_robots_txt"])

    def test_it_sends_the_bearer_token_when_one_is_configured(self) -> None:
        seen: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200, json={"results": [{"success": True, "markdown": "Hello"}]}
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        fetcher = Crawl4AIFetcher(
            "http://crawl4ai:11235", client=client, api_token="s3cret"
        )
        self.assertEqual(fetcher.fetch(_URL).markdown, "Hello")
        self.assertEqual(seen["auth"], "Bearer s3cret")

    def test_it_sends_no_authorization_header_without_a_token(self) -> None:
        seen: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200, json={"results": [{"success": True, "markdown": "Hello"}]}
            )

        fetcher = self._fetcher(handler)
        fetcher.fetch(_URL)
        self.assertIsNone(seen["auth"])

    def test_a_401_becomes_a_failed_page_that_says_so(self) -> None:
        page = self._fetcher(lambda _r: httpx.Response(401, text="no")).fetch(_URL)
        self.assertEqual(page.status, "failed")
        self.assertIn("401", page.error or "")

    def test_http_errors_and_bad_json_become_failed_pages(self) -> None:
        for response in (
            httpx.Response(500, text="boom"),
            httpx.Response(200, text="not json"),
        ):
            page = self._fetcher(lambda _r, resp=response: resp).fetch(_URL)
            self.assertEqual(page.status, "failed")
            self.assertTrue(page.error)

    def test_a_connection_error_becomes_a_failed_page(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        page = self._fetcher(handler).fetch(_URL)
        self.assertEqual(page, Page(url=_URL, status="failed", error=page.error))
        self.assertIn("down", page.error or "")


if __name__ == "__main__":
    unittest.main()
