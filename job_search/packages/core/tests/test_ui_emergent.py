"""Headless render tests for the Emergent Roles page (Streamlit AppTest)."""

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
    / "15_Emergent_Roles.py"
)
_ID = "11111111-1111-1111-1111-111111111111"
_HOSTILE = "[x](http://evil.example) **b**"


def _cluster(**overrides: object) -> dict:
    """Build an API cluster.

    Args:
        **overrides: Fields to replace.

    Returns:
        A cluster dict with novelty withheld.
    """
    base = {
        "cluster_id": 1,
        "titles": ["forward deployed engineer", "field engineer"],
        "n_employers": 6,
        "nearest_category": "software_engineer",
        "nearest_cosine": 0.41,
        "is_candidate": True,
        "candidate_note": "outside the taxonomy",
        "hybrid": {
            "category_a": "software_engineer",
            "weight_a": 0.6,
            "category_b": "data_engineer",
            "weight_b": 0.4,
            "gain": 0.09,
        },
        "novelty": None,
        "novelty_note": "withheld: history too short for a novelty index",
    }
    return {**base, **overrides}


def _data(**overrides: object) -> dict:
    """Build the API's candidates answer for an immature corpus.

    Args:
        **overrides: Fields to replace.

    Returns:
        A status-plus-candidates dict.
    """
    base = {
        "mature": False,
        "missing": "history 1 of 90 days; 1 of 4 weekly periods",
        "history_days": 1.0,
        "periods": 1,
        "employers": 14,
        "agency_jobs_excluded": 90,
        "min_history_days": 90,
        "min_periods": 4,
        "min_employers": 5,
        "novelty_shown": False,
        "preview_label": "preview - history too short, not a finding",
        "run": {
            "id": "r",
            "created_at": "2026-10-16T09:00:00+00:00",
            "n_titles": 800,
            "n_clusters": 12,
            "n_noise": 300,
        },
        "candidates": [
            {
                "id": _ID,
                "name": "Field engineer",
                "characterisation": "Deploys software at customers.",
                "skill_signature": ["Python", "AWS"],
                "corroboration": "uncorroborated",
                "verdict": "pending",
                "rationale": None,
                "cluster": _cluster(),
            }
        ],
    }
    return {**base, **overrides}


class TestEmergentPage(unittest.TestCase):
    """The page is honest about immaturity and records explicit verdicts."""

    def setUp(self) -> None:
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        self.data: dict = _data()
        self.sent: list[tuple[str, dict]] = []
        self.post_status = 200

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
            self.sent.append((url, kwargs.get("json") or {}))  # type: ignore[arg-type]
            body = (
                {"verdict": "x"}
                if self.post_status == 200
                else {"detail": "a rationale is required"}
            )
            return httpx.Response(self.post_status, json=body, request=request)
        if url.endswith("/emergent/clusters"):
            return httpx.Response(
                200, json={**self.data, "clusters": [_cluster()]}, request=request
            )
        return httpx.Response(200, json=self.data, request=request)

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
            app.metric,
        )
        return " ".join(
            str(getattr(e, "value", "")) for g in groups for e in g
        ).replace("\\", "")

    def test_an_immature_corpus_shows_the_preview_warning_and_no_novelty_index(
        self,
    ) -> None:
        app = self._run()
        self.assertEqual(len(app.exception), 0)
        shown = self._shown(app)
        self.assertIn("Preview - history too short, not a finding", shown)
        self.assertIn("history 1 of 90 days", shown)
        self.assertIn("not shown", shown)
        labels = {m.label: m.value for m in app.metric}
        self.assertEqual(labels["Novelty index"], "not shown")

    def test_the_candidate_is_labelled_a_hypothesis_with_its_breadth_and_hybrid(
        self,
    ) -> None:
        shown = self._shown(self._run())
        self.assertIn("Field engineer", shown)
        self.assertIn("hypothesis - uncorroborated", shown)
        self.assertIn("60% software engineer + 40% data engineer", shown)
        self.assertIn("Python, AWS", shown)
        labels = {m.label: m.value for m in self._run().metric}
        self.assertEqual(labels["Distinct employers"], "6")

    def test_a_mature_corpus_shows_the_novelty_index(self) -> None:
        cluster = _cluster(
            novelty=0.42, novelty_note="recency 0.8 x breadth 1.0 x velocity 0.5"
        )
        self.data = _data(
            mature=True, missing="", preview_label=None, novelty_shown=True
        )
        self.data["candidates"][0]["cluster"] = cluster
        app = self._run()
        self.assertEqual(
            {m.label: m.value for m in app.metric}["Novelty index"], "0.42"
        )
        self.assertNotIn("Preview - history too short", self._shown(app))

    def test_confirming_posts_the_verdict_and_the_reason(self) -> None:
        app = self._run()
        app.text_area(key=f"em-reason-{_ID}").input("Seen at three clients")
        app = self._run(app)
        with mock.patch("httpx.request", side_effect=self._request):
            app.button(key=f"em-confirm-{_ID}").click().run()
        self.assertEqual(
            self.sent,
            [
                (
                    f"{self._base()}/emergent/candidates/{_ID}/review",
                    {"verdict": "confirmed", "rationale": "Seen at three clients"},
                )
            ],
        )

    def test_dismissing_posts_a_dismissal(self) -> None:
        app = self._run()
        with mock.patch("httpx.request", side_effect=self._request):
            app.button(key=f"em-dismiss-{_ID}").click().run()
        self.assertEqual(self.sent[0][1]["verdict"], "dismissed")

    def test_a_refused_verdict_shows_the_reason_and_does_not_crash(self) -> None:
        self.post_status = 422
        app = self._run()
        with mock.patch("httpx.request", side_effect=self._request):
            app = app.button(key=f"em-confirm-{_ID}").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("a rationale is required", self._shown(app))

    def test_a_recorded_verdict_is_shown_and_offers_no_buttons(self) -> None:
        self.data["candidates"][0].update(verdict="dismissed", rationale="An artefact")
        app = self._run()
        self.assertIn("Your verdict: dismissed", self._shown(app))
        self.assertNotIn(f"em-confirm-{_ID}", [b.key for b in app.button])

    def test_hostile_text_is_not_rendered_as_markdown(self) -> None:
        self.data["candidates"][0]["name"] = _HOSTILE
        self.data["candidates"][0]["cluster"]["titles"] = [_HOSTILE]
        markdown = " ".join(m.value for m in self._run().markdown)
        self.assertNotIn("[x](http://evil.example)", markdown)

    def test_no_run_yet_says_which_commands_to_run(self) -> None:
        self.data = _data(run=None, candidates=[])
        self.assertIn("No cluster run yet", self._shown(self._run()))

    def test_an_api_outage_does_not_crash(self) -> None:
        with mock.patch("httpx.request", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=20).run()
        self.assertEqual((len(app.exception), len(app.error)), (0, 1))

    @staticmethod
    def _base() -> str:
        """Return the API base URL the page uses.

        Returns:
            `Settings.api_base_url`.
        """
        from core.settings import get_settings

        return get_settings().api_base_url


if __name__ == "__main__":
    unittest.main()
