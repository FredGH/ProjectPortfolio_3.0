"""Unit tests for extracting cited company facts."""

from __future__ import annotations

import json
import unittest

from tests.company_intel_fixtures import FakeAdapter

from core.company_intel.extract import ExtractError, extract_intel, guard_content
from core.company_intel.fetch import Page
from core.company_intel.schema import IntelContent, IntelFact

_ABOUT = "https://acme.example/about"
_JOBS = "https://acme.example/careers"


def _raw(**fields: object) -> dict:
    """Build a raw extraction object.

    Args:
        **fields: Field values.

    Returns:
        The dict.
    """
    return dict(fields)


class TestGuard(unittest.TestCase):
    """guard_content drops anything the crawl does not support."""

    def test_facts_citing_uncrawled_urls_are_dropped(self) -> None:
        content = guard_content(
            _raw(
                business_model=[
                    {"text": "Sells widgets.", "source_url": _ABOUT},
                    {"text": "Raised $9bn.", "source_url": "https://news.example/x"},
                    {"text": "   ", "source_url": _ABOUT},
                    {"text": "No url."},
                ]
            ),
            {_ABOUT},
        )
        self.assertEqual(
            content.business_model,
            [IntelFact(text="Sells widgets.", source_url=_ABOUT)],
        )

    def test_more_than_five_competitors_keeps_the_first_five(self) -> None:
        competitors = [
            {"name": f"Rival {i}", "basis": "model_knowledge"} for i in range(8)
        ]
        content = guard_content(_raw(competitors=competitors), {_ABOUT})
        self.assertEqual(
            [c.name for c in content.competitors], [f"Rival {i}" for i in range(5)]
        )

    def test_model_knowledge_competitors_lose_any_url(self) -> None:
        content = guard_content(
            _raw(
                competitors=[
                    {"name": "Rival", "basis": "model_knowledge", "source_url": _ABOUT}
                ]
            ),
            {_ABOUT},
        )
        self.assertEqual(content.competitors[0].source_url, None)
        self.assertEqual(content.competitors[0].basis, "model_knowledge")

    def test_a_site_competitor_needs_a_crawled_url(self) -> None:
        content = guard_content(
            _raw(
                competitors=[
                    {"name": "Good", "basis": "site", "source_url": _ABOUT},
                    {"name": "Bad", "basis": "site", "source_url": "https://x.example"},
                    {"name": "NoUrl", "basis": "site"},
                ]
            ),
            {_ABOUT},
        )
        self.assertEqual([c.name for c in content.competitors], ["Good"])

    def test_competitors_are_deduplicated_and_unknown_basis_is_unverified(self) -> None:
        content = guard_content(
            _raw(
                competitors=[
                    {"name": "Rival", "basis": "weird"},
                    {"name": "rival", "basis": "model_knowledge"},
                    {"name": "", "basis": "model_knowledge"},
                ]
            ),
            set(),
        )
        self.assertEqual(
            [(c.name, c.basis) for c in content.competitors],
            [("Rival", "model_knowledge")],
        )

    def test_bad_shapes_give_empty_fields_not_errors(self) -> None:
        content = guard_content(
            _raw(business_model="oops", tech_stack=[1, None, "x"], extra=[1]), {_ABOUT}
        )
        self.assertTrue(content.is_empty())
        self.assertTrue(guard_content("not a dict", set()).is_empty())


class TestExtractIntel(unittest.TestCase):
    """extract_intel builds the prompt, calls the model and guards the reply."""

    def _pages(self) -> list[Page]:
        """Build two crawled pages, one containing braces.

        Returns:
            Two `ok` pages.
        """
        return [
            Page(_ABOUT, "ok", "Acme makes widgets. {not a placeholder}"),
            Page(_JOBS, "ok", "We use dbt and Snowflake."),
        ]

    def test_it_returns_guarded_content_and_usage(self) -> None:
        reply = json.dumps(
            {
                "business_model": [{"text": "Makes widgets.", "source_url": _ABOUT}],
                "tech_stack": [{"text": "dbt, Snowflake.", "source_url": _JOBS}],
                "competitors": [
                    {
                        "name": "Rival",
                        "why": "Same market",
                        "basis": "model_knowledge",
                        "source_url": None,
                    }
                ],
            }
        )
        adapter = FakeAdapter([reply])
        result = extract_intel(
            "Acme", self._pages(), None, adapters={"anthropic": adapter}
        )
        self.assertEqual(result.content.tech_stack[0].source_url, _JOBS)
        self.assertEqual(result.content.competitors[0].name, "Rival")
        self.assertEqual(result.prompt_version, "claude.v1")
        self.assertEqual(result.calls[0]["task"], "company_intel_extraction")

    def test_page_text_with_braces_reaches_the_prompt_intact(self) -> None:
        adapter = FakeAdapter(["{}"])
        extract_intel("Acme", self._pages(), None, adapters={"anthropic": adapter})
        prompt = adapter.prompts[0]
        self.assertIn("{not a placeholder}", prompt)
        self.assertIn(_ABOUT, prompt)
        self.assertIn(_JOBS, prompt)

    def test_the_previous_profile_is_offered_on_a_refresh(self) -> None:
        previous = IntelContent(
            business_model=[IntelFact(text="Old claim.", source_url=_ABOUT)]
        )
        adapter = FakeAdapter(["{}"])
        extract_intel("Acme", self._pages(), previous, adapters={"anthropic": adapter})
        self.assertIn("Old claim.", adapter.prompts[0])
        adapter2 = FakeAdapter(["{}"])
        extract_intel("Acme", self._pages(), None, adapters={"anthropic": adapter2})
        self.assertNotIn("Previous profile", adapter2.prompts[0])

    def test_an_unreadable_reply_is_an_error(self) -> None:
        with self.assertRaises(ExtractError):
            extract_intel(
                "Acme",
                self._pages(),
                None,
                adapters={"anthropic": FakeAdapter(["no json"])},
            )

    def test_a_long_page_is_cut_before_it_is_sent(self) -> None:
        adapter = FakeAdapter(["{}"])
        long_page = Page(_ABOUT, "ok", "x" * 50_000)
        extract_intel("Acme", [long_page], None, adapters={"anthropic": adapter})
        self.assertLess(len(adapter.prompts[0]), 20_000)


if __name__ == "__main__":
    unittest.main()
