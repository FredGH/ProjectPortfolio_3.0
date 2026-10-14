"""Integration tests for the intel store (real Postgres)."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.company_intel import store
from core.company_intel.schema import (
    AngleAdvantage,
    AngleContent,
    Competitor,
    IntelContent,
    IntelFact,
    Resolution,
)

_KEY = "zzfixture-store-key"
_JOB = "zzfixture-store-job"
_URL = "https://zzfixture-store.example/about"


def _content() -> IntelContent:
    """Build a small profile.

    Returns:
        Intel content with one fact and one competitor.
    """
    return IntelContent(
        business_model=[IntelFact(text="Sells widgets.", source_url=_URL)],
        competitors=[Competitor(name="Rival", basis="model_knowledge")],
    )


class TestIntelStore(unittest.TestCase):
    """Reads and writes against the intel tables."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove every fixture row."""
        with self.owner.begin() as conn:
            for table, column in (
                ("company_intel", "company_key"),
                ("job_competitive_angle", "company_key"),
            ):
                conn.execute(
                    text(f"DELETE FROM intel.{table} WHERE {column} = :k"), {"k": _KEY}
                )
            conn.execute(
                text(
                    "DELETE FROM intel.crawl_cache WHERE url LIKE 'https://zzfixture-store%'"
                )
            )
            conn.execute(
                text("DELETE FROM intel.job_company WHERE job_group_id = :j"),
                {"j": _JOB},
            )

    def _append(self, status: str = "ok") -> int:
        """Append one intel version.

        Args:
            status: The status to store.

        Returns:
            The new version number.
        """
        return store.append_intel(
            self.engine,
            company_key=_KEY,
            display_name="Zz Store Co",
            status=status,  # type: ignore[arg-type]
            content=_content(),
            pages=[{"url": _URL, "status": "ok", "error": None}],
            resolved_from_job=_JOB,
            model="m",
            prompt_version="claude.v1",
            usage={"calls": [], "input_tokens": 1, "output_tokens": 2, "cost_usd": 0.1},
        )

    def test_versions_count_up_and_the_latest_wins(self) -> None:
        self.assertEqual(self._append(), 1)
        self.assertEqual(self._append("no_content"), 2)
        latest = store.read_latest_intel(self.engine, _KEY)
        assert latest is not None
        self.assertEqual((latest.version, latest.status), (2, "no_content"))
        self.assertEqual(latest.content.competitors[0].name, "Rival")
        self.assertEqual(latest.pages[0]["url"], _URL)
        self.assertEqual(latest.usage["output_tokens"], 2)
        first = store.read_intel_version(self.engine, _KEY, 1)
        assert first is not None
        self.assertEqual(first.status, "ok")
        self.assertEqual(
            [v[0] for v in store.list_intel_versions(self.engine, _KEY)], [2, 1]
        )

    def test_an_unknown_company_has_no_intel(self) -> None:
        self.assertIsNone(store.read_latest_intel(self.engine, _KEY))
        self.assertIsNone(store.read_intel_version(self.engine, _KEY, 1))
        self.assertEqual(store.list_intel_versions(self.engine, _KEY), [])

    def test_a_cached_page_is_returned_only_while_fresh(self) -> None:
        today = date(2026, 10, 14)
        store.write_cached_page(
            self.engine,
            "https://zzfixture-store.example/",
            fetched_on=today - timedelta(days=3),
            markdown="# Hi",
            links=["https://zzfixture-store.example/about"],
        )
        fresh = store.read_cached_page(
            self.engine, "https://zzfixture-store.example/", max_age_days=7, today=today
        )
        assert fresh is not None
        self.assertEqual(fresh.markdown, "# Hi")
        self.assertEqual(fresh.links, ("https://zzfixture-store.example/about",))
        self.assertIsNone(
            store.read_cached_page(
                self.engine,
                "https://zzfixture-store.example/",
                max_age_days=2,
                today=today,
            )
        )

    def test_writing_the_same_page_twice_on_a_day_is_harmless(self) -> None:
        for _ in range(2):
            store.write_cached_page(
                self.engine,
                "https://zzfixture-store.example/x",
                fetched_on=date(2026, 10, 14),
                markdown="x",
                links=[],
            )

    def test_a_manual_resolution_survives_an_automatic_one(self) -> None:
        manual = Resolution(
            _JOB, "Agency", "agency", "Real Bank", "https://bank.example", "manual"
        )
        auto = Resolution(_JOB, "Agency", "agency", "Guess Ltd", None, "llm")
        store.save_resolution(self.engine, manual)
        kept = store.save_resolution(self.engine, auto)
        self.assertEqual(kept, manual)
        self.assertEqual(store.read_resolution(self.engine, _JOB), manual)

    def test_an_automatic_resolution_is_replaced_by_a_manual_one(self) -> None:
        auto = Resolution(_JOB, "Agency", "agency", None, None, "llm")
        manual = Resolution(_JOB, "Agency", "agency", "Real Bank", None, "manual")
        store.save_resolution(self.engine, auto)
        self.assertEqual(store.save_resolution(self.engine, manual), manual)
        self.assertIsNone(store.read_resolution(self.engine, "zzfixture-none"))

    def test_angles_are_stored_per_intel_version_and_the_newest_is_read(self) -> None:
        content = AngleContent(
            transformation="Moves to a modern stack.",
            advantages=[AngleAdvantage(competitor="Rival", point="Faster data.")],
            caveats=["Unverified."],
        )
        store.append_angle(
            self.engine,
            job_group_id=_JOB,
            company_key=_KEY,
            intel_version=1,
            content=content,
            model="m",
            prompt_version="claude.v1",
            usage={"calls": []},
        )
        self.assertIsNone(store.read_angle(self.engine, _JOB, _KEY, 2))
        found = store.read_angle(self.engine, _JOB, _KEY, 1)
        assert found is not None
        self.assertEqual(found.content.advantages[0].competitor, "Rival")
        latest = store.read_latest_angle(self.engine, _JOB)
        assert latest is not None
        self.assertEqual(latest.intel_version, 1)


if __name__ == "__main__":
    unittest.main()
