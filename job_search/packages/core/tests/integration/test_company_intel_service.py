"""Integration tests for get_or_refresh_intel (real Postgres, fake web + LLM)."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from tests.company_intel_fixtures import FakeAdapter, FakeFetcher, make_job, make_source
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.company_intel import store
from core.company_intel.config import CompanyIntelConfig
from core.company_intel.fetch import Page
from core.company_intel.keys import company_key
from core.company_intel.resolve import ManualOverride
from core.company_intel.service import get_or_refresh_intel, is_fresh

_JOB = "zzfixture-intel-job"
_SITE = "https://zzfixture-intel.example/"
_ABOUT = "https://zzfixture-intel.example/about"
_NAME = "Zzfixture Intel Ltd"
_CONFIG = CompanyIntelConfig(refresh_after_days=90, crawl_cache_days=7, max_pages=4)
_NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def _resolution_reply(kind: str = "employer", client: str | None = None) -> str:
    """Build a resolution reply.

    Args:
        kind: The poster kind.
        client: The end client for an agency.

    Returns:
        A JSON string naming the fixture website.
    """
    return json.dumps({"poster_kind": kind, "end_client": client, "website_url": _SITE})


def _profile_reply(fact: str = "Makes widgets.") -> str:
    """Build an extraction reply.

    Args:
        fact: The business-model fact text.

    Returns:
        A JSON string citing the about page.
    """
    return json.dumps(
        {
            "business_model": [{"text": fact, "source_url": _ABOUT}],
            "competitors": [{"name": "Rival", "basis": "model_knowledge"}],
        }
    )


def _snapshot_reply() -> str:
    """Build a snapshot reply.

    Returns:
        A JSON string answering industry and employees, nothing else.
    """
    return json.dumps(
        {
            "industry": {"value": "Industrial widgets", "as_of": "2024"},
            "employees": {"value": "about 9,000", "as_of": "2024"},
        }
    )


def _web() -> FakeFetcher:
    """Build a fake site: a homepage naming the company and an about page.

    Returns:
        The fake fetcher.
    """
    return FakeFetcher(
        {
            _SITE: Page(_SITE, "ok", f"Welcome to {_NAME}", (_ABOUT,)),
            _ABOUT: Page(_ABOUT, "ok", "We make widgets."),
        }
    )


class TestIsFresh(unittest.TestCase):
    """is_fresh compares age with the refresh threshold."""

    def test_inside_and_outside_the_window(self) -> None:
        self.assertTrue(is_fresh(_NOW - timedelta(days=89), _NOW, 90))
        self.assertFalse(is_fresh(_NOW - timedelta(days=91), _NOW, 90))


class TestGetOrRefreshIntel(unittest.TestCase):
    """The research flow, end to end, with fakes at the edges."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()
        cls.key = company_key(_NAME)

    def tearDown(self) -> None:
        """Remove every fixture row."""
        with self.owner.begin() as conn:
            for table, col in (
                ("company_intel", "company_key"),
                ("job_company", "job_group_id"),
                ("crawl_cache", "url"),
            ):
                value = (
                    self.key
                    if col == "company_key"
                    else (
                        _JOB
                        if col == "job_group_id"
                        else "https://zzfixture-intel.example%"
                    )
                )
                op = "LIKE" if col == "url" else "="
                conn.execute(
                    text(f"DELETE FROM intel.{table} WHERE {col} {op} :v"), {"v": value}
                )

    def _run(self, adapter: FakeAdapter, web: FakeFetcher, **kwargs):  # type: ignore[no-untyped-def]
        """Call the service with the standard fixtures.

        Args:
            adapter: The scripted LLM.
            web: The fake web.
            **kwargs: Extra arguments for the service.

        Returns:
            The `IntelResult`.
        """
        return get_or_refresh_intel(
            self.engine,
            make_job(),
            make_source(),
            adapters={"anthropic": adapter},
            fetcher=web,
            config=_CONFIG,
            now=lambda: _NOW,
            **kwargs,
        )

    def test_the_first_research_crawls_extracts_and_stores_version_one(self) -> None:
        adapter = FakeAdapter([_resolution_reply(), _profile_reply()])
        result = self._run(adapter, _web())
        self.assertEqual(result.outcome, "refreshed")
        assert result.intel is not None
        self.assertEqual((result.intel.version, result.intel.status), (1, "ok"))
        self.assertEqual(result.intel.content.business_model[0].source_url, _ABOUT)
        self.assertEqual([p["url"] for p in result.intel.pages], [_SITE, _ABOUT])
        self.assertEqual(len(result.intel.usage["calls"]), 3)
        self.assertEqual(result.intel.resolved_from_job, _JOB)

    def test_the_first_research_also_stores_the_six_row_snapshot(self) -> None:
        adapter = FakeAdapter(
            [_resolution_reply(), _profile_reply(), _snapshot_reply()]
        )
        result = self._run(adapter, _web())
        assert result.intel is not None
        rows = {i.key: i for i in result.intel.content.snapshot}
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows["industry"].value, "Industrial widgets")
        self.assertEqual(rows["employees"].as_of, "2024")
        self.assertEqual(rows["market_share"].value, "not known")
        self.assertEqual(
            [c["task"] for c in result.intel.usage["calls"]],
            ["company_resolution", "company_intel_extraction", "company_snapshot"],
        )
        self.assertIn("Makes widgets.", adapter.prompts[2])

    def test_a_snapshot_failure_does_not_fail_the_research(self) -> None:
        adapter = FakeAdapter([_resolution_reply(), _profile_reply(), "not json"])
        result = self._run(adapter, _web())
        self.assertEqual(result.outcome, "refreshed")
        assert result.intel is not None
        self.assertEqual(result.intel.status, "ok")
        self.assertEqual(result.intel.content.snapshot, [])
        self.assertEqual(len(result.intel.usage["calls"]), 3)

    def test_a_snapshot_alone_does_not_turn_no_content_into_ok(self) -> None:
        adapter = FakeAdapter([_resolution_reply(), "{}", _snapshot_reply()])
        result = self._run(adapter, _web())
        self.assertEqual(result.outcome, "no_content")
        assert result.intel is not None
        self.assertEqual(result.intel.status, "no_content")
        self.assertEqual(len(result.intel.content.snapshot), 6)

    def test_a_second_research_reuses_the_stored_version(self) -> None:
        adapter = FakeAdapter([_resolution_reply(), _profile_reply()])
        web = _web()
        self._run(adapter, web)
        calls_before, fetched_before = len(adapter.prompts), len(web.requested)
        result = self._run(adapter, web)
        self.assertEqual(result.outcome, "reused")
        assert result.intel is not None
        self.assertEqual(result.intel.version, 1)
        self.assertEqual(
            (len(adapter.prompts), len(web.requested)), (calls_before, fetched_before)
        )

    def test_a_stale_version_is_refreshed_with_the_previous_one_as_context(
        self,
    ) -> None:
        adapter = FakeAdapter([_resolution_reply(), _profile_reply("Old claim.")])
        self._run(adapter, _web())
        later = _NOW + timedelta(days=100)
        adapter2 = FakeAdapter([_profile_reply("New claim.")])
        result = get_or_refresh_intel(
            self.engine,
            make_job(),
            make_source(),
            adapters={"anthropic": adapter2},
            fetcher=_web(),
            config=_CONFIG,
            now=lambda: later,
        )
        self.assertEqual(result.outcome, "refreshed")
        assert result.intel is not None
        self.assertEqual(result.intel.version, 2)
        self.assertIn("Old claim.", adapter2.prompts[0])
        old = store.read_intel_version(self.engine, self.key, 1)
        assert old is not None
        self.assertEqual(old.content.business_model[0].text, "Old claim.")

    def test_force_refreshes_a_fresh_version(self) -> None:
        adapter = FakeAdapter([_resolution_reply(), _profile_reply()])
        self._run(adapter, _web())
        result = self._run(FakeAdapter([_profile_reply("Again.")]), _web(), force=True)
        self.assertEqual(result.outcome, "refreshed")
        assert result.intel is not None
        self.assertEqual(result.intel.version, 2)

    def test_an_agency_with_no_named_client_is_not_crawled(self) -> None:
        adapter = FakeAdapter([_resolution_reply("agency", None)])
        web = _web()
        result = self._run(adapter, web)
        self.assertEqual(result.outcome, "end_client_unknown")
        self.assertIsNone(result.intel)
        self.assertEqual(web.requested, [])
        self.assertEqual(len(adapter.prompts), 1)

    def test_a_website_that_does_not_name_the_company_is_rejected(self) -> None:
        web = FakeFetcher({_SITE: Page(_SITE, "ok", "Welcome to Somebody Else")})
        result = self._run(FakeAdapter([_resolution_reply()]), web)
        self.assertEqual(result.outcome, "website_unverified")
        self.assertIsNone(store.read_latest_intel(self.engine, self.key))

    def test_no_website_at_all_is_unverified(self) -> None:
        reply = json.dumps(
            {"poster_kind": "employer", "end_client": None, "website_url": None}
        )
        result = self._run(FakeAdapter([reply]), _web())
        self.assertEqual(result.outcome, "website_unverified")

    def test_a_manual_website_fixes_a_rejected_one(self) -> None:
        self._run(
            FakeAdapter([_resolution_reply()]),
            FakeFetcher({_SITE: Page(_SITE, "ok", "Nope")}),
        )
        result = self._run(
            FakeAdapter([_profile_reply()]),
            _web(),
            override=ManualOverride(website_url=_SITE),
        )
        self.assertEqual(result.outcome, "refreshed")
        self.assertEqual(result.resolution.method, "manual")

    def test_if_the_crawler_is_down_nothing_is_written_and_the_error_is_reported(
        self,
    ) -> None:
        self._run(FakeAdapter([_resolution_reply(), _profile_reply()]), _web())
        later = _NOW + timedelta(days=100)
        down = FakeFetcher({})
        result = get_or_refresh_intel(
            self.engine,
            make_job(),
            make_source(),
            adapters={"anthropic": FakeAdapter(["{}"])},
            fetcher=down,
            config=_CONFIG,
            now=lambda: later,
        )
        self.assertEqual(result.outcome, "failed")
        self.assertIn("not in the fake", result.message)
        latest = store.read_latest_intel(self.engine, self.key)
        assert latest is not None
        self.assertEqual(latest.version, 1)

    def test_a_typed_website_is_trusted_even_if_the_homepage_does_not_name_the_company(
        self,
    ) -> None:
        web = FakeFetcher(
            {
                _SITE: Page(_SITE, "ok", "Welcome to the Zz Group", (_ABOUT,)),
                _ABOUT: Page(_ABOUT, "ok", "We make widgets."),
            }
        )
        result = self._run(
            FakeAdapter([_resolution_reply(), _profile_reply()]),
            web,
            override=ManualOverride(website_url=_SITE),
        )
        self.assertEqual(result.outcome, "refreshed")

    def test_a_guessed_website_that_does_not_name_the_company_is_still_rejected(
        self,
    ) -> None:
        web = FakeFetcher({_SITE: Page(_SITE, "ok", "Welcome to the Zz Group")})
        result = self._run(FakeAdapter([_resolution_reply()]), web)
        self.assertEqual(result.outcome, "website_unverified")

    def test_pages_that_yield_nothing_are_stored_as_no_content(self) -> None:
        result = self._run(FakeAdapter([_resolution_reply(), "{}"]), _web())
        self.assertEqual(result.outcome, "no_content")
        assert result.intel is not None
        self.assertEqual(result.intel.status, "no_content")

    def test_an_unreadable_extraction_is_a_failure_and_writes_nothing(self) -> None:
        result = self._run(FakeAdapter([_resolution_reply(), "not json"]), _web())
        self.assertEqual(result.outcome, "failed")
        self.assertIsNone(store.read_latest_intel(self.engine, self.key))

    def test_at_most_max_pages_are_crawled(self) -> None:
        links = tuple(f"https://zzfixture-intel.example/blog/p{i}" for i in range(10))
        pages = {_SITE: Page(_SITE, "ok", f"Welcome to {_NAME}", links)}
        pages.update({u: Page(u, "ok", "post") for u in links})
        web = FakeFetcher(pages)
        self._run(FakeAdapter([_resolution_reply(), "{}"]), web)
        self.assertLessEqual(len(web.requested), _CONFIG.max_pages)


if __name__ == "__main__":
    unittest.main()
