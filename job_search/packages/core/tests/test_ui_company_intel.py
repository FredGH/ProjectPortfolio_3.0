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
        "refresh_after_days": 90,
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
                {
                    "key": "profit",
                    "value": "about USD 1.2bn net profit",
                    "as_of": "2023",
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

    def test_the_snapshot_is_a_seven_row_table_with_labels_values_and_years(
        self,
    ) -> None:
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
                "Profit (last financial year)",
                "Number of employees",
                "Employee satisfaction",
            ],
        )
        self.assertEqual(frame["Value"][0], "Industrial widgets")
        self.assertEqual(frame["Value"][2], "not known")
        self.assertEqual(frame["Value"][4], "about USD 1.2bn net profit")
        self.assertEqual(frame["As of"][4], "2023")
        self.assertEqual(frame["As of"][5], "2024")
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

    def _resolution(self, **fields):  # type: ignore[no-untyped-def]
        """Build an intel-less view with a stored resolution.

        Args:
            **fields: Resolution fields to override.

        Returns:
            A view whose resolution is an agency posting by default.
        """
        resolution = {
            "poster_kind": "agency",
            "posted_company": "Synapri",
            "end_client": None,
            "website_url": None,
            "method": "llm",
            **fields,
        }
        return {**_EMPTY, "resolution": resolution}

    def _form_state(self, app: AppTest) -> str:
        """Say where the company form is: inline, in a collapsed expander, or absent.

        Args:
            app: A finished `AppTest`.

        Returns:
            `inline` when the form sits on the page itself (no expander),
            `expander` when it is inside the collapsible "Correct the company",
            `absent` when the inputs are not there.
        """
        has_inputs = {i.key for i in app.text_input} >= {
            "intel-end-client",
            "intel-website",
        }
        if not has_inputs:
            return "absent"
        in_expander = any(e.label == "Correct the company" for e in app.expander)
        return "expander" if in_expander else "inline"

    def test_an_agency_with_no_end_client_cannot_be_researched_until_one_is_given(
        self,
    ) -> None:
        app = self._run([_CANDIDATE], self._resolution())
        button = next(b for b in app.button if b.key == "intel-research")
        self.assertTrue(button.disabled)
        shown = _shown(app).lower()
        self.assertIn("does not name the end client", shown)
        self.assertIn("research the posting company itself", shown)
        self.assertNotIn("click research", shown)
        self.assertEqual(self._form_state(app), "inline")

    def test_an_end_client_without_a_website_can_still_be_researched(self) -> None:
        app = self._run(
            [_CANDIDATE], self._resolution(end_client="Real Bank", method="manual")
        )
        self.assertFalse(
            next(b for b in app.button if b.key == "intel-research").disabled
        )
        shown = _shown(app).lower()
        self.assertIn("look for it", shown)
        self.assertEqual(self._form_state(app), "expander")

    def test_an_agency_offers_to_research_the_agency_itself_in_one_click(self) -> None:
        app = self._run([_CANDIDATE], self._resolution())
        button = next(b for b in app.button if b.key == "intel-agency-itself")
        self.assertIn("Synapri", button.label)
        self.assertIn("itself", button.label)

    def test_a_job_with_a_known_end_client_does_not_offer_the_agency_button(
        self,
    ) -> None:
        app = self._run(
            [_CANDIDATE], self._resolution(end_client="Real Bank", method="manual")
        )
        self.assertNotIn("intel-agency-itself", [b.key for b in app.button])

    def test_the_agency_button_saves_the_agency_as_the_company_and_asks_to_confirm(
        self,
    ) -> None:
        puts: list[tuple[str, object]] = []
        saved = {"done": False}

        def fake_put(url: str, **kwargs) -> httpx.Response:
            puts.append((url, kwargs.get("json")))
            saved["done"] = True
            return httpx.Response(200, json={}, request=httpx.Request("PUT", url))

        def fake_get(url: str, **_kwargs) -> httpx.Response:
            view = (
                self._resolution(end_client="Synapri", method="manual")
                if saved["done"]
                else self._resolution()
            )
            return _fake_get([_CANDIDATE], view)(url)

        with (
            mock.patch("httpx.get", side_effect=fake_get),
            mock.patch("httpx.put", side_effect=fake_put),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == "intel-agency-itself").click().run()
        self.assertEqual(
            puts,
            [
                (
                    puts[0][0],
                    {"end_client": "Synapri", "website_url": None},
                )
            ],
        )
        self.assertTrue(puts[0][0].endswith("/intel/jobs/zzfixture-job/resolution"))
        keys = [b.key for b in app.button]
        self.assertIn("intel-confirm", keys)
        self.assertFalse(
            next(b for b in app.button if b.key == "intel-research").disabled
        )
        self.assertIn("Claude", _shown(app))

    def test_a_company_with_an_end_client_and_website_can_be_researched(self) -> None:
        app = self._run(
            [_CANDIDATE],
            self._resolution(
                poster_kind="employer",
                end_client="Gamma",
                website_url="https://gamma.example",
                method="employer",
            ),
        )
        button = next(b for b in app.button if b.key == "intel-research")
        self.assertFalse(button.disabled)
        self.assertEqual(self._form_state(app), "expander")

    def test_saving_the_typed_company_puts_it_to_the_resolution_endpoint(self) -> None:
        puts: list[tuple[str, object]] = []

        def fake_put(url: str, **kwargs) -> httpx.Response:
            puts.append((url, kwargs.get("json")))
            return httpx.Response(200, json={}, request=httpx.Request("PUT", url))

        with (
            mock.patch(
                "httpx.get", side_effect=_fake_get([_CANDIDATE], self._resolution())
            ),
            mock.patch("httpx.put", side_effect=fake_put),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            app.text_input(key="intel-end-client").input("Real Bank")
            app.text_input(key="intel-website").input("bank.example")
            app.run()
            next(
                b for b in app.button if b.key == "intel-save-resolution"
            ).click().run()
        self.assertEqual(len(puts), 1)
        self.assertTrue(puts[0][0].endswith("/intel/jobs/zzfixture-job/resolution"))
        self.assertEqual(
            puts[0][1], {"end_client": "Real Bank", "website_url": "bank.example"}
        )

    def test_a_job_never_looked_at_can_still_be_researched(self) -> None:
        app = self._run([_CANDIDATE], _EMPTY)
        self.assertFalse(
            next(b for b in app.button if b.key == "intel-research").disabled
        )
        self.assertEqual(self._form_state(app), "expander")

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

    def _click_flow(self, view, button_key, then):  # type: ignore[no-untyped-def]
        """Click a research button, then (optionally) the next button.

        Args:
            view: The intel view the fake API returns.
            button_key: The research button to click first.
            then: Key of the button to click next, or None to stop.

        Returns:
            `(app, posts)` where `posts` lists the `(url, json)` of every POST.
        """
        posts: list[tuple[str, object]] = []

        def fake_post(url: str, **kwargs) -> httpx.Response:
            posts.append((url, kwargs.get("json")))
            return httpx.Response(
                200,
                json={"outcome": "refreshed", "message": "Researched.", "view": view},
                request=httpx.Request("POST", url),
            )

        with (
            mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], view)),
            mock.patch("httpx.post", side_effect=fake_post),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == button_key).click().run()
            if then is not None:
                next(b for b in app.button if b.key == then).click().run()
        return app, posts

    def test_research_asks_for_confirmation_and_makes_no_call_yet(self) -> None:
        app, posts = self._click_flow(_EMPTY, "intel-research", None)
        self.assertEqual(posts, [])
        self.assertIn("intel-confirm", [b.key for b in app.button])
        self.assertIn("intel-cancel", [b.key for b in app.button])
        shown = _shown(app)
        self.assertIn("Claude", shown)
        self.assertIn("$0.10", shown)

    def test_confirming_a_first_research_posts_without_force(self) -> None:
        _app, posts = self._click_flow(_EMPTY, "intel-research", "intel-confirm")
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0][0].endswith("/intel/jobs/zzfixture-job/research"))
        self.assertEqual(posts[0][1], {"force": False})

    def test_refresh_names_its_cost_and_posts_with_force_once_confirmed(self) -> None:
        app, posts = self._click_flow(_VIEW, "intel-refresh", None)
        self.assertEqual(posts, [])
        self.assertIn("$0.08", _shown(app))
        _app, posts = self._click_flow(_VIEW, "intel-refresh", "intel-confirm")
        self.assertEqual([p[1] for p in posts], [{"force": True}])

    def test_cancel_makes_no_call_and_closes_the_confirmation(self) -> None:
        app, posts = self._click_flow(_EMPTY, "intel-research", "intel-cancel")
        self.assertEqual(posts, [])
        keys = [b.key for b in app.button]
        self.assertNotIn("intel-confirm", keys)
        self.assertIn("intel-research", keys)

    def test_a_stale_version_explains_that_nothing_reruns_by_itself(self) -> None:
        view = {**_VIEW, "intel": {**_VIEW["intel"], "fresh": False}}
        shown = _shown(self._run([_CANDIDATE], view))
        self.assertIn("older than 90 days", shown)
        self.assertIn("Refresh", shown)

    def test_saved_research_shows_for_a_job_that_was_never_researched(self) -> None:
        view = {**_VIEW, "resolution": None, "angle": None}
        app = self._run([_CANDIDATE], view)
        shown = _shown(app)
        self.assertIn("saved research", shown)
        self.assertIn("Gamma", shown)
        self.assertIn("Sells widgets.", shown)
        self.assertIn("intel-refresh", [b.key for b in app.button])
        self.assertNotIn("intel-research", [b.key for b in app.button])

    def test_a_job_with_saved_research_but_no_angle_offers_to_write_it(self) -> None:
        view = {**_VIEW, "angle": None}
        app = self._run([_CANDIDATE], view)
        self.assertIn("intel-angle", [b.key for b in app.button])
        self.assertIn("no competitive angle for this job yet", _shown(app).lower())

    def test_a_job_that_has_an_angle_does_not_offer_to_write_one(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        self.assertNotIn("intel-angle", [b.key for b in app.button])

    def test_the_angle_button_confirms_the_small_cost_then_posts_to_the_angle_endpoint(
        self,
    ) -> None:
        view = {**_VIEW, "angle": None}
        app, posts = self._click_flow(view, "intel-angle", None)
        self.assertEqual(posts, [])
        self.assertIn("$0.01", _shown(app))
        self.assertIn("intel-angle-confirm", [b.key for b in app.button])
        _app, posts = self._click_flow(view, "intel-angle", "intel-angle-confirm")
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0][0].endswith("/intel/jobs/zzfixture-job/angle"))
        self.assertEqual(posts[0][1], {"force": False})

    def test_cancelling_the_angle_makes_no_call(self) -> None:
        view = {**_VIEW, "angle": None}
        app, posts = self._click_flow(view, "intel-angle", "intel-angle-cancel")
        self.assertEqual(posts, [])
        self.assertNotIn("intel-angle-confirm", [b.key for b in app.button])

    def test_the_confirmation_does_not_follow_you_to_another_job(self) -> None:
        other = {**_CANDIDATE, "job_group_id": "zzfixture-other", "company": "Delta"}
        with mock.patch(
            "httpx.get", side_effect=_fake_get([_CANDIDATE, other], _EMPTY)
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == "intel-research").click().run()
            app.selectbox[0].select("zzfixture-other").run()
        self.assertNotIn("intel-confirm", [b.key for b in app.button])


if __name__ == "__main__":
    unittest.main()
