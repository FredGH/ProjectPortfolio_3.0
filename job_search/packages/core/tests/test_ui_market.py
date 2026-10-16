"""Headless render tests for the Market Marts page (Streamlit AppTest)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import httpx
import streamlit as st
from streamlit.testing.v1 import AppTest

_PAGE = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "ui"
    / "app"
    / "pages"
    / "14_Market_Marts.py"
)
_HOSTILE = "[x](http://evil.example) **b**"
_CMD = "cd dbt && dbt build --select int_market_job+"


def _overview(**overrides: object) -> dict:
    """Build the API's overview.

    Args:
        **overrides: Fields to replace.

    Returns:
        An overview dict built just now.
    """
    base = {
        "built_at": datetime.now(UTC).isoformat(),
        "n_jobs": 1738,
        "panel_sources": "adzuna,reed",
        "matrix_version": "v1",
        "categories": ["data_engineer", "software_engineer"],
        "rebuild_command": _CMD,
        "panel_complete": False,
        "trend_break": False,
        "latest_month": "2026-09-01",
    }
    return {**base, **overrides}


_SALARY = [
    {
        "region": "unknown",
        "contract_type": "contract",
        "unit": "GBP per day",
        "n_postings": 29,
        "n_disclosed": 9,
        "disclosure_rate": 0.31,
        "median": 525.0,
        "p25": 488.0,
        "p75": 540.0,
        "min_advertised": 450.0,
        "max_advertised": 600.0,
        "period_start": "2026-09-01",
    },
    {
        "region": "UKI",
        "contract_type": "permanent",
        "unit": "GBP per year",
        "n_postings": 12,
        "n_disclosed": 2,
        "disclosure_rate": 0.17,
        "median": None,
        "p25": None,
        "p75": None,
        "min_advertised": None,
        "max_advertised": None,
        "period_start": "2026-09-01",
    },
]
_SKILLS = [
    {
        "skill_label": "SQL",
        "frequency": 24,
        "share": 0.12,
        "must_have_ratio": 1.0,
        "rank_delta_90d": None,
    },
    {
        "skill_label": _HOSTILE,
        "frequency": 10,
        "share": 0.05,
        "must_have_ratio": 0.5,
        "rank_delta_90d": 2,
    },
]
_MIX = [
    {
        "period_start": "2026-09-01",
        "n_postings": 100,
        "share_remote": 0.1,
        "share_hybrid": 0.2,
        "share_onsite": 0.05,
        "share_work_model_unknown": 0.65,
        "share_permanent": 0.2,
        "share_contract": 0.1,
        "share_contract_type_unknown": 0.7,
        "share_junior": 0.1,
        "share_mid": 0.5,
        "share_senior": 0.2,
        "share_lead": 0.1,
        "share_principal": 0.05,
        "share_seniority_unknown": 0.05,
        "panel_complete": False,
        "matrix_version": "v1",
    }
]


class TestMarketPage(unittest.TestCase):
    """The page shows what dbt built, with denominators and honest gaps."""

    def setUp(self) -> None:
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        self.overview: object = _overview()
        self.lifecycle = {
            "n_jobs": 100,
            "n_live": 100,
            "n_gone": 0,
            "median_days_live": None,
        }
        self.status = 200

    def _get(self, url: str, **_kwargs: object) -> httpx.Response:
        """Serve the fake API.

        Args:
            url: The URL.
            **_kwargs: httpx options (ignored).

        Returns:
            The canned response.
        """
        request = httpx.Request("GET", url)
        if self.status != 200:
            return httpx.Response(
                self.status, json={"detail": f"not built. Run: {_CMD}"}, request=request
            )
        data: object = []
        if url.endswith("/market/overview"):
            data = self.overview
        elif url.endswith("/market/salary"):
            data = _SALARY
        elif url.endswith("/market/skills"):
            data = _SKILLS
        elif url.endswith("/market/composition"):
            data = _MIX
        elif url.endswith("/market/lifecycle"):
            data = self.lifecycle
        return httpx.Response(200, json=data, request=request)

    def _run(self) -> AppTest:
        """Render the page against the fake API.

        Returns:
            The finished `AppTest`.
        """
        with mock.patch("httpx.get", side_effect=self._get):
            return AppTest.from_file(str(_PAGE), default_timeout=20).run()

    @staticmethod
    def _shown(app: AppTest) -> str:
        """Collect the page's text, minus markdown escapes.

        Args:
            app: A finished `AppTest`.

        Returns:
            The text joined by spaces.
        """
        parts = [
            e.value
            for group in (app.markdown, app.caption, app.info, app.warning, app.error)
            for e in group
        ]
        return " ".join(parts).replace("\\", "")

    def test_it_shows_the_build_panel_matrix_and_the_rebuild_command(self) -> None:
        app = self._run()
        self.assertEqual(len(app.exception), 0)
        shown = self._shown(app)
        self.assertIn("1738 jobs", shown)
        self.assertIn("adzuna,reed", shown)
        self.assertIn("query matrix v1", shown)
        self.assertIn(_CMD, shown)

    def test_an_incomplete_panel_says_this_is_a_cross_section_not_a_trend(self) -> None:
        self.assertIn("not a trend", self._shown(self._run()))

    def test_an_old_build_is_flagged_stale(self) -> None:
        self.overview = _overview(
            built_at=(datetime.now(UTC) - timedelta(days=12)).isoformat()
        )
        shown = self._shown(self._run())
        self.assertIn("12 days ago", shown)
        self.assertIn("stale", shown)

    def test_salary_shows_n_and_disclosure_rate_and_blank_thin_medians(self) -> None:
        frame = self._run().dataframe[0].value
        self.assertEqual(list(frame["Postings (n)"]), [29, 12])
        self.assertEqual(list(frame["Disclosed (n)"]), [9, 2])
        self.assertEqual(list(frame["Disclosure rate"]), ["31%", "17%"])
        self.assertEqual(frame["Median"][0], 525)
        self.assertTrue(frame["Median"].isna()[1])

    def test_missing_change_is_labelled_insufficient_history(self) -> None:
        skills = self._run().dataframe[1].value
        self.assertEqual(skills["Rank change (90 days)"][0], "insufficient history")

    def test_hostile_text_is_not_rendered_as_markdown(self) -> None:
        markdown = " ".join(m.value for m in self._run().markdown)
        self.assertNotIn("[x](http://evil.example)", markdown)

    def test_an_empty_premium_explains_why(self) -> None:
        self.assertIn("No premium can be published yet", self._shown(self._run()))

    def test_a_lifecycle_with_nothing_gone_does_not_invent_a_duration(self) -> None:
        shown = self._shown(self._run())
        self.assertIn("not given a fake duration", shown)

    def test_unbuilt_marts_show_the_command_without_crashing(self) -> None:
        self.status = 503
        app = self._run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn(_CMD, self._shown(app))

    def test_an_api_outage_does_not_crash(self) -> None:
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=20).run()
        self.assertEqual((len(app.exception), len(app.error)), (0, 1))


if __name__ == "__main__":
    unittest.main()
