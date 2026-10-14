"""Tests for resolving which company a job is really for."""

from __future__ import annotations

import json
import unittest

from sqlalchemy import text
from tests.company_intel_fixtures import FakeAdapter, FakeFetcher, make_job, make_source
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.company_intel import store
from core.company_intel.fetch import Page
from core.company_intel.resolve import (
    ManualOverride,
    parse_resolution_reply,
    resolve_company,
    verify_website,
)

_JOB = "zzfixture-intel-job"


def _reply(kind: str, end_client: str | None, website: str | None) -> str:
    """Build a scripted LLM reply.

    Args:
        kind: The poster kind.
        end_client: The end client name or None.
        website: The website or None.

    Returns:
        A JSON string.
    """
    return json.dumps(
        {"poster_kind": kind, "end_client": end_client, "website_url": website}
    )


class TestParseReply(unittest.TestCase):
    """parse_resolution_reply tolerates fences and junk."""

    def test_a_fenced_object_is_read(self) -> None:
        reply = "```json\n" + _reply("agency", "Big Bank", None) + "\n```"
        self.assertEqual(parse_resolution_reply(reply)["end_client"], "Big Bank")

    def test_junk_gives_an_empty_dict(self) -> None:
        for junk in ("", "no idea", "[1, 2]", "{"):
            self.assertEqual(parse_resolution_reply(junk), {}, junk)


class TestVerifyWebsite(unittest.TestCase):
    """A website is accepted only when its homepage names the company."""

    def test_the_company_name_on_the_homepage_accepts_it(self) -> None:
        page = Page("https://acme.example", "ok", "Welcome to ACME, makers of widgets")
        fetcher = FakeFetcher({"https://acme.example": page})
        self.assertEqual(
            verify_website(fetcher, "https://acme.example", "Acme Ltd"), page
        )

    def test_another_company_rejects_it(self) -> None:
        page = Page("https://other.example", "ok", "Welcome to Other Co")
        fetcher = FakeFetcher({"https://other.example": page})
        self.assertIsNone(verify_website(fetcher, "https://other.example", "Acme Ltd"))

    def test_a_failed_page_rejects_it(self) -> None:
        self.assertIsNone(verify_website(FakeFetcher({}), "https://x.example", "Acme"))


class TestResolveCompany(unittest.TestCase):
    """resolve_company decides, stores, and respects manual overrides."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove the fixture resolution."""
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM intel.job_company WHERE job_group_id = :j"),
                {"j": _JOB},
            )

    def test_an_employer_is_researched_under_the_posted_name(self) -> None:
        adapter = FakeAdapter([_reply("employer", None, "https://zz.example")])
        result = resolve_company(
            self.engine, make_job(), make_source(), adapters={"anthropic": adapter}
        )
        res = result.resolution
        self.assertEqual(
            (res.poster_kind, res.end_client, res.website_url, res.method),
            ("employer", "Zzfixture Intel Ltd", "https://zz.example", "employer"),
        )
        self.assertEqual(len(result.calls), 1)
        self.assertEqual(store.read_resolution(self.engine, _JOB), res)

    def test_an_agency_that_names_its_client_uses_the_client(self) -> None:
        adapter = FakeAdapter(
            [_reply("agency", "Northern Bank", "https://bank.example")]
        )
        res = resolve_company(
            self.engine, make_job(), make_source(), adapters={"anthropic": adapter}
        ).resolution
        self.assertEqual(
            (res.poster_kind, res.end_client, res.method),
            ("agency", "Northern Bank", "llm"),
        )

    def test_an_agency_without_a_named_client_has_no_end_client(self) -> None:
        adapter = FakeAdapter([_reply("agency", None, None)])
        res = resolve_company(
            self.engine, make_job(), make_source(), adapters={"anthropic": adapter}
        ).resolution
        self.assertIsNone(res.end_client)

    def test_an_unusable_reply_leaves_the_end_client_unknown(self) -> None:
        adapter = FakeAdapter(["I cannot tell"])
        res = resolve_company(
            self.engine, make_job(), make_source(), adapters={"anthropic": adapter}
        ).resolution
        self.assertEqual((res.poster_kind, res.end_client), ("unknown", None))

    def test_a_stored_resolution_is_reused_without_another_call(self) -> None:
        adapter = FakeAdapter([_reply("employer", None, None)])
        adapters = {"anthropic": adapter}
        resolve_company(self.engine, make_job(), make_source(), adapters=adapters)
        again = resolve_company(
            self.engine, make_job(), make_source(), adapters=adapters
        )
        self.assertEqual(len(adapter.prompts), 1)
        self.assertEqual(again.calls, [])

    def test_a_manual_override_wins_and_survives_a_later_resolution(self) -> None:
        adapter = FakeAdapter([_reply("agency", "Guess Ltd", None)])
        adapters = {"anthropic": adapter}
        manual = resolve_company(
            self.engine,
            make_job(),
            make_source(),
            adapters=adapters,
            override=ManualOverride(
                end_client="Real Bank", website_url="https://bank.example"
            ),
        ).resolution
        self.assertEqual(
            (manual.end_client, manual.website_url, manual.method),
            ("Real Bank", "https://bank.example", "manual"),
        )
        later = resolve_company(
            self.engine, make_job(), make_source(), adapters=adapters
        )
        self.assertEqual(later.resolution, manual)
        self.assertEqual(adapter.prompts, [])

    def test_a_partial_override_keeps_the_other_stored_field(self) -> None:
        adapters = {
            "anthropic": FakeAdapter([_reply("agency", "Bank A", "https://a.example")])
        }
        resolve_company(self.engine, make_job(), make_source(), adapters=adapters)
        res = resolve_company(
            self.engine,
            make_job(),
            make_source(),
            adapters=adapters,
            override=ManualOverride(website_url="https://b.example"),
        ).resolution
        self.assertEqual(
            (res.end_client, res.website_url, res.method),
            ("Bank A", "https://b.example", "manual"),
        )

    def test_prompt_text_with_braces_is_safe(self) -> None:
        adapter = FakeAdapter([_reply("employer", None, None)])
        resolve_company(
            self.engine,
            make_job(description="Use {curly} and {{double}} braces"),
            make_source(),
            adapters={"anthropic": adapter},
        )
        self.assertIn("{curly} and {{double}}", adapter.prompts[0])


if __name__ == "__main__":
    unittest.main()
