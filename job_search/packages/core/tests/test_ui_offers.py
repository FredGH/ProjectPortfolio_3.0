# ruff: noqa: E501
"""Headless render tests for the Offer Check page (Streamlit AppTest)."""

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
    / "16_Offer_Check.py"
)
_HOSTILE = "[x](http://evil.example) **b**"

_NET = {
    "status": "outside",
    "day_rate": 500.0,
    "gross_year": 110000.0,
    "net_year": 72000.0,
    "net_day": 327.0,
    "steps": [{"label": "Corporation tax", "amount": 17000.0}],
    "assumptions": ["220 billable days a year"],
    "tax_year": "2025-26",
    "verified": False,
}


def _bench(**overrides: object) -> dict:
    """Build the API's benchmark for an ok slice.

    Args:
        **overrides: Fields to replace.

    Returns:
        A benchmark dict.
    """
    base = {
        "value": 500.0,
        "unit": "GBP per day",
        "status": "ok",
        "percentile": 62.0,
        "slice_label": "contract type contract + category data_engineer",
        "n_disclosed": 14,
        "n_postings": 40,
        "disclosure_rate": 0.35,
        "p25": 450.0,
        "median": 490.0,
        "p75": 540.0,
        "ladder": [
            {
                "label": "contract + data_engineer",
                "n_postings": 40,
                "n_disclosed": 14,
                "disclosure_rate": 0.35,
            }
        ],
        "comparables": [
            {
                "job_group_id": "j",
                "title": _HOSTILE,
                "company": "Acme",
                "figure": 495.0,
                "region": "UKI",
                "seniority": "senior",
            }
        ],
        "direction": "insufficient history",
        "trend_note": "needs 10 disclosed figures in both windows",
        "warnings": [],
    }
    return {**base, **overrides}


_THIN = _bench(
    status="too_thin",
    percentile=None,
    slice_label=None,
    n_disclosed=4,
    n_postings=30,
    disclosure_rate=0.13,
    p25=None,
    median=None,
    p75=None,
    warnings=[
        "Too thin: no slice has 10 disclosed figures (the widest has 4), so no percentile is given."
    ],
)


class TestOfferPage(unittest.TestCase):
    """The page never shows a bare percentile and refuses thin slices."""

    def setUp(self) -> None:
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        self.sent: list[tuple[str, str, dict]] = []
        self.bench = _bench()
        self.net: dict | None = _NET
        self.offers: list[dict] = []
        self.check_status = 200

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Serve the fake API.

        Args:
            method: The HTTP method.
            url: The URL.
            **kwargs: httpx options (`json` is recorded for writes).

        Returns:
            The canned response.
        """
        request = httpx.Request(method, url)
        if method == "POST":
            self.sent.append((method, url, kwargs.get("json") or {}))  # type: ignore[arg-type]
            if url.endswith("/offers/net-compare"):
                body = {
                    "first": _NET,
                    "second": {**_NET, "status": "inside", "net_year": 60000.0},
                    "difference_year": -12000.0,
                    "difference_day": -55.0,
                }
                return httpx.Response(200, json=body, request=request)
            if self.check_status != 200:
                return httpx.Response(
                    422,
                    json={"detail": "a contract offer must be a daily or hourly rate"},
                    request=request,
                )
            body = {
                "benchmark": self.bench,
                "net": self.net,
                "net_note": "Take-home needs a contract offer with a known IR35 status (inside or outside).",
            }
            return httpx.Response(
                200,
                json={**body, "id": "x"} if url.endswith("/offers") else body,
                request=request,
            )
        if url.endswith("/market/overview"):
            return httpx.Response(
                200,
                json={"categories": ["data_engineer", "software_engineer"]},
                request=request,
            )
        if url.endswith("/offers"):
            return httpx.Response(200, json=self.offers, request=request)
        return httpx.Response(404, request=request)

    def _run(self, app: AppTest | None = None) -> AppTest:
        """Run (or rerun) the page against the fake API.

        Args:
            app: An existing AppTest, or None for a new one.

        Returns:
            The finished `AppTest`.
        """
        with mock.patch("httpx.request", side_effect=self._request):
            return (
                app
                if app is not None
                else AppTest.from_file(str(_PAGE), default_timeout=20)
            ).run()

    def _checked(self) -> AppTest:
        """Enter a contract offer and press Check.

        Returns:
            The AppTest after the check.
        """
        app = self._run()
        app.number_input(key="oc-amount").set_value(500.0)
        app = self._run(app)
        with mock.patch("httpx.request", side_effect=self._request):
            return app.button(key="oc-check").click().run()

    @staticmethod
    def _shown(app: AppTest) -> str:
        """Collect the page's text, minus markdown escapes.

        Args:
            app: A finished `AppTest`.

        Returns:
            The text joined by spaces.
        """
        groups = (
            app.markdown,
            app.caption,
            app.info,
            app.warning,
            app.success,
            app.error,
            app.subheader,
        )
        return " ".join(
            str(getattr(e, "value", "")) for g in groups for e in g
        ).replace("\\", "")

    def test_the_check_button_waits_for_an_amount(self) -> None:
        app = self._run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.button(key="oc-check").disabled)

    def test_a_percentile_is_never_shown_without_its_n_and_disclosure_rate(
        self,
    ) -> None:
        app = self._checked()
        labels = {m.label: m.value for m in app.metric}
        self.assertEqual(labels["Percentile"], "62nd")
        shown = self._shown(app)
        self.assertIn("14 postings stating pay out of 40", shown)
        self.assertIn("35% disclose", shown)
        self.assertEqual(self.sent[-1][1].rsplit("/", 1)[-1], "benchmark")
        self.assertEqual(self.sent[-1][2]["amount"], 500.0)

    def test_a_thin_slice_shows_the_warning_and_no_percentile(self) -> None:
        self.bench = _THIN
        app = self._checked()
        labels = {m.label: m.value for m in app.metric}
        self.assertEqual(labels["Percentile"], "not given")
        shown = self._shown(app)
        self.assertIn("Too thin", shown)
        self.assertIn("Only 4 postings stating pay out of 30", shown)
        self.assertNotIn("th ", labels["Percentile"])

    def test_direction_says_insufficient_history_rather_than_inventing_one(
        self,
    ) -> None:
        self.assertIn("insufficient history", self._shown(self._checked()))

    def test_take_home_is_labelled_an_unverified_estimate_with_its_assumptions(
        self,
    ) -> None:
        shown = self._shown(self._checked())
        self.assertIn("NOT verified", shown)
        self.assertIn("not tax advice", shown)
        self.assertIn("220 billable days", shown)

    def test_no_take_home_for_an_undetermined_status_and_the_page_says_why(
        self,
    ) -> None:
        self.net = None
        shown = self._shown(self._checked())
        self.assertIn("known IR35 status", shown)

    def test_hostile_comparable_text_is_not_rendered_as_markdown(self) -> None:
        app = self._checked()
        self.assertTrue(
            any(_HOSTILE in str(df.value.to_dict()) for df in app.dataframe)
        )
        self.assertNotIn(
            "[x](http://evil.example)", " ".join(m.value for m in app.markdown)
        )

    def test_an_api_refusal_is_shown_not_crashed(self) -> None:
        self.check_status = 422
        app = self._checked()
        self.assertEqual(len(app.exception), 0)
        self.assertIn(
            "a contract offer must be a daily or hourly rate", self._shown(app)
        )

    def test_saving_posts_the_offer_with_the_details(self) -> None:
        app = self._checked()
        app.text_input(key="oc-company").input("Acme")
        app.text_input(key="oc-title").input("Data Engineer")
        app = self._run(app)
        with mock.patch("httpx.request", side_effect=self._request):
            app = app.button(key="oc-save").click().run()
        method, url, body = self.sent[-1]
        self.assertTrue(url.endswith("/offers") and not url.endswith("benchmark"))
        self.assertEqual(
            (body["company"], body["title"], body["amount"]),
            ("Acme", "Data Engineer", 500.0),
        )

    def test_comparing_two_offers_posts_both_and_names_the_better_one(self) -> None:
        app = self._run()
        app.number_input(key="oc-rate-a").set_value(600.0)
        app.number_input(key="oc-rate-b").set_value(550.0)
        app = self._run(app)
        with mock.patch("httpx.request", side_effect=self._request):
            app = app.button(key="oc-compare").click().run()
        body = self.sent[-1][2]
        self.assertEqual(body["first"], {"day_rate": 600.0, "ir35_status": "inside"})
        self.assertEqual(body["second"], {"day_rate": 550.0, "ir35_status": "outside"})
        self.assertIn("A takes home more", {m.label for m in app.metric})

    def test_recorded_offers_show_their_percentile_or_that_it_was_thin(self) -> None:
        self.offers = [
            {
                "received_on": "2026-10-16",
                "company": "Acme",
                "title": "DE",
                "amount": 500.0,
                "basis": "daily",
                "ir35_status": "outside",
                "benchmark": {"percentile": 62.0, "n_disclosed": 14},
            },
            {
                "received_on": "2026-10-15",
                "company": "Beta",
                "title": "DE",
                "amount": 450.0,
                "basis": "daily",
                "ir35_status": "inside",
                "benchmark": {"percentile": None, "n_disclosed": 4},
            },
        ]
        app = self._run()
        frame = [d.value for d in app.dataframe if "Percentile" in d.value.columns][0]
        self.assertEqual(list(frame["Percentile"]), ["62nd", "not given (thin)"])
        self.assertEqual(list(frame["Disclosed (n)"]), [14, 4])

    def test_an_api_outage_does_not_crash(self) -> None:
        with mock.patch("httpx.request", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=20).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)


if __name__ == "__main__":
    unittest.main()
