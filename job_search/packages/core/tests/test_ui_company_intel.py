"""Headless render tests for the Company Intel page (Streamlit AppTest)."""

from __future__ import annotations

import unittest
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
    / "11_Company_Intel.py"
)
_HOSTILE = "[x](http://evil.example) **b**"
_CANDIDATE = {
    "job_group_id": "zzfixture-job",
    "title_for_display": "Data Engineer",
    "company": "Gamma",
    "final_score": 0.8,
    "latest_run_id": None,
    "latest_status": None,
}
_VIEW = {
    "job_group_id": "zzfixture-job",
    "resolution": {
        "poster_kind": "employer",
        "posted_company": "Gamma",
        "end_client": "Gamma",
        "website_url": "https://gamma.example",
        "method": "employer",
    },
    "intel": {
        "version": 2,
        "status": "ok",
        "researched_at": "2026-10-14T09:00:00+00:00",
        "display_name": "Gamma",
        "fresh": True,
        "content": {
            "business_model": [
                {"text": "Sells widgets.", "source_url": "https://gamma.example/about"}
            ],
            "size_signals": [],
            "tech_stack": [
                {"text": _HOSTILE, "source_url": "https://gamma.example/jobs"}
            ],
            "recent_announcements": [],
            "notes": [],
            "snapshot": [
                {"key": "industry", "value": "Industrial widgets", "as_of": "2024"},
                {
                    "key": "ranking",
                    "value": "One of the top 5 by revenue",
                    "as_of": None,
                },
                {"key": "market_share", "value": "not known", "as_of": None},
                {
                    "key": "growth_5y",
                    "value": "Revenue up roughly 40%",
                    "as_of": "2024",
                },
                {"key": "employees", "value": "about 9,000", "as_of": "2024"},
                {"key": "satisfaction", "value": "not known", "as_of": None},
            ],
            "competitors": [
                {
                    "name": "Rival",
                    "why": "Same market",
                    "basis": "model_knowledge",
                    "source_url": None,
                },
                {
                    "name": "Sited",
                    "why": "Named on site",
                    "basis": "site",
                    "source_url": "https://gamma.example/about",
                },
            ],
        },
        "pages": [{"url": "https://gamma.example", "status": "ok", "error": None}],
    },
    "versions": [
        {"version": 2, "researched_at": "2026-10-14T09:00:00+00:00", "status": "ok"},
        {"version": 1, "researched_at": "2026-07-01T09:00:00+00:00", "status": "ok"},
    ],
    "angle": {
        "intel_version": 2,
        "created_at": "2026-10-14T09:01:00+00:00",
        "content": {
            "transformation": "Moves to a modern data stack.",
            "advantages": [{"competitor": "Rival", "point": "Fresher data."}],
            "caveats": ["One job description only."],
        },
    },
}
_EMPTY = {
    "job_group_id": "zzfixture-job",
    "resolution": None,
    "intel": None,
    "versions": [],
    "angle": None,
}


def _shown(app: AppTest) -> str:
    """Collect everything the page shows as text, with markdown escapes removed.

    Args:
        app: A finished `AppTest`.

    Returns:
        The markdown, text, caption, info and warning values joined, minus the
        backslashes the page adds to keep API text literal.
    """
    parts = [
        element.value
        for group in (
            app.markdown,
            app.text,
            app.caption,
            app.info,
            app.warning,
            app.subheader,
        )
        for element in group
    ]
    return " ".join(parts).replace("\\", "")


def _fake_get(candidates, view):
    """Build a fake `httpx.get` for the page.

    Args:
        candidates: The `/tailoring/candidates` answer.
        view: The `/intel/jobs/...` answer, or None for a 404.

    Returns:
        A callable for `mock.patch("httpx.get", ...)`.
    """

    def _fake(url: str, **_kwargs) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "/tailoring/candidates" in url:
            return httpx.Response(200, json=candidates, request=request)
        if "/intel/jobs/" in url:
            if view is None:
                return httpx.Response(
                    404, json={"detail": "unknown job"}, request=request
                )
            return httpx.Response(200, json=view, request=request)
        return httpx.Response(200, json=[], request=request)

    return _fake


class TestCompanyIntelPage(unittest.TestCase):
    """The page renders intel, competitors and the labelled angle."""

    def setUp(self) -> None:
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)

    def _run(self, candidates, view) -> AppTest:  # type: ignore[no-untyped-def]
        """Render the page against a fake API.

        Args:
            candidates: The candidates answer.
            view: The intel view answer.

        Returns:
            The finished `AppTest`.
        """
        with mock.patch("httpx.get", side_effect=_fake_get(candidates, view)):
            return AppTest.from_file(str(_PAGE), default_timeout=10).run()

    def test_it_renders_intel_with_sources_and_a_fresh_badge(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        self.assertEqual(len(app.exception), 0)
        shown = _shown(app)
        self.assertIn("Sells widgets.", shown)
        self.assertIn("https://gamma.example/about", shown)
        self.assertIn("Fresh", shown)

    def test_competitors_show_their_basis_badge(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        shown = _shown(app)
        self.assertIn("model knowledge — unverified", shown)
        self.assertIn("Rival", shown)
        self.assertIn("Sited", shown)

    def test_the_snapshot_is_a_six_row_table_with_labels_values_and_years(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        self.assertEqual(len(app.dataframe), 1)
        frame = app.dataframe[0].value
        self.assertEqual(list(frame.columns), ["Metric", "Value", "As of"])
        self.assertEqual(
            list(frame["Metric"]),
            [
                "Industry / sector",
                "Position / ranking in the industry",
                "Market share",
                "Growth over the past 5 years",
                "Number of employees",
                "Employee satisfaction",
            ],
        )
        self.assertEqual(frame["Value"][0], "Industrial widgets")
        self.assertEqual(frame["Value"][2], "not known")
        self.assertEqual(frame["As of"][4], "2024")
        self.assertEqual(frame["As of"][1], "")

    def test_the_snapshot_says_it_is_unverified_model_knowledge(self) -> None:
        shown = _shown(self._run([_CANDIDATE], _VIEW))
        self.assertIn("Company snapshot", shown)
        self.assertIn("model's general knowledge", shown)
        self.assertIn("unverified", shown)

    def test_intel_stored_before_the_snapshot_existed_shows_no_table(self) -> None:
        content = {
            k: v for k, v in _VIEW["intel"]["content"].items() if k != "snapshot"
        }
        view = {**_VIEW, "intel": {**_VIEW["intel"], "content": content}}
        app = self._run([_CANDIDATE], view)
        self.assertEqual(len(app.dataframe), 0)
        self.assertEqual(len(app.exception), 0)

    def test_the_angle_is_labelled_as_analysis(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        shown = _shown(app)
        self.assertIn("analysis — not verified", shown)
        self.assertIn("Moves to a modern data stack.", shown)
        self.assertIn("Fresher data.", shown)

    def test_hostile_text_is_not_rendered_as_markdown(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        markdown = " ".join(m.value for m in app.markdown)
        self.assertNotIn("[x](http://evil.example)", markdown)

    def test_with_no_intel_it_offers_to_research(self) -> None:
        app = self._run([_CANDIDATE], _EMPTY)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("intel-research", [b.key for b in app.button])
        self.assertIn("No intel yet", " ".join(i.value for i in app.info))

    def test_an_unknown_end_client_asks_for_one(self) -> None:
        view = {
            **_EMPTY,
            "resolution": {
                "poster_kind": "agency",
                "posted_company": "Hays",
                "end_client": None,
                "website_url": None,
                "method": "llm",
            },
        }
        app = self._run([_CANDIDATE], view)
        self.assertIn("end client", _shown(app).lower())
        self.assertIn("intel-save-resolution", [b.key for b in app.button])

    def test_a_stale_version_says_so(self) -> None:
        view = {**_VIEW, "intel": {**_VIEW["intel"], "fresh": False}}
        self.assertIn("Stale", _shown(self._run([_CANDIDATE], view)))

    def test_a_job_link_opens_the_page_on_that_job(self) -> None:
        other = {**_CANDIDATE, "job_group_id": "zzfixture-other", "company": "Delta"}
        with mock.patch(
            "httpx.get", side_effect=_fake_get([_CANDIDATE, other], _EMPTY)
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10)
            app.query_params["job"] = "zzfixture-other"
            app.run()
        self.assertEqual(app.selectbox[0].value, "zzfixture-other")

    def test_no_candidates_and_an_api_error_do_not_crash(self) -> None:
        self.assertEqual(len(self._run([], None).exception), 0)
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)

    def test_clicking_research_posts_to_the_research_endpoint(self) -> None:
        posted = {}

        def fake_post(url: str, **kwargs) -> httpx.Response:
            posted["url"], posted["json"] = url, kwargs.get("json")
            return httpx.Response(
                200,
                json={"outcome": "refreshed", "message": "Researched.", "view": _VIEW},
                request=httpx.Request("POST", url),
            )

        with (
            mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _EMPTY)),
            mock.patch("httpx.post", side_effect=fake_post),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == "intel-research").click().run()
        self.assertTrue(posted["url"].endswith("/intel/jobs/zzfixture-job/research"))
        self.assertEqual(posted["json"], {"force": False})


if __name__ == "__main__":
    unittest.main()
