"""Headless render tests for the Cover Letter & Pitch page (Streamlit AppTest)."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest import mock

import httpx
import streamlit as st
from streamlit.testing.v1 import AppTest
from tests.artefacts_fixtures import (
    TODAY,
    letter_reply_json,
    make_pack,
    pitch_reply_json,
)

from core.artefacts.assemble import build_letter, build_pitch, parse_writer_reply

_PAGE = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "ui"
    / "app"
    / "pages"
    / "12_Cover_Letter_and_Pitch.py"
)
_HOSTILE = "[x](http://evil.example) **b**"
_OPEN_ID = "11111111-1111-1111-1111-111111111111"
_CANDIDATE = {
    "job_group_id": "zzfixture-job",
    "title_for_display": "Data Engineer",
    "company": "Gamma",
    "final_score": 0.8,
    "latest_run_id": None,
    "latest_status": None,
}
_OTHER = {**_CANDIDATE, "job_group_id": "zzfixture-other", "company": "Delta"}


def _artefact(kind: str, status: str, open_sentences: list | None = None) -> dict:
    """Build the API's artefact model.

    Args:
        kind: `cover_letter` or `pitch`.
        status: `needs_review`, `approved` or `failed`.
        open_sentences: Open sentence dicts.

    Returns:
        An artefact dict as the API returns it.
    """
    pack = make_pack()
    if kind == "cover_letter":
        doc = build_letter(
            pack, parse_writer_reply(letter_reply_json(), "cover_letter"), today=TODAY
        )
    else:
        doc = build_pitch(pack, parse_writer_reply(pitch_reply_json(), "pitch"))
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "status": status,
        "target_title": "Data Engineer",
        "attempts": 1,
        "created_at": "2026-10-15T09:00:00+00:00",
        "error_message": None,
        "content": doc.model_dump(),
        "open_sentences": open_sentences or [],
        "usage": None,
    }


def _open(text: str = "I did it alone.") -> dict:
    """Build a pending open sentence.

    Args:
        text: The sentence.

    Returns:
        An open-sentence dict.
    """
    return {
        "id": _OPEN_ID,
        "location": "evidence:2",
        "kind": "unsupported",
        "text": text,
        "issue": "adds ownership",
        "status": "pending",
        "evidence_ref": None,
    }


def _view(**overrides: object) -> dict:
    """Build the API's page view.

    Args:
        **overrides: Fields to replace.

    Returns:
        A view dict with a letter needing review and an approved pitch.
    """
    evidence = [
        {"id": e.id, "label": e.label, "text": e.text}
        for e in make_pack().items.values()
    ]
    base = {
        "job_group_id": "zzfixture-job",
        "has_saved_research": True,
        "evidence": evidence,
        "cover_letter": _artefact("cover_letter", "needs_review", [_open()]),
        "pitch": _artefact("pitch", "approved"),
    }
    return {**base, **overrides}


def _shown(app: AppTest) -> str:
    """Collect everything the page shows as text, minus markdown escapes.

    Args:
        app: A finished `AppTest`.

    Returns:
        The page text joined by spaces.
    """
    parts = [
        element.value
        for group in (
            app.markdown,
            app.text,
            app.caption,
            app.info,
            app.warning,
            app.success,
            app.error,
            app.subheader,
            app.header,
        )
        for element in group
    ]
    return " ".join(parts).replace("\\", "")


class TestArtefactsPage(unittest.TestCase):
    """The page shows documents, their sources and the decision panel."""

    def setUp(self) -> None:
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        self.posts: list[tuple[str, dict]] = []
        self.candidates = [_CANDIDATE]
        self.view: dict | httpx.Response = _view()

    def _get(self, url: str, **_kwargs: object) -> httpx.Response:
        """Serve the fake API's GETs.

        Args:
            url: The requested URL.
            **_kwargs: Ignored httpx options.

        Returns:
            The canned response.
        """
        request = httpx.Request("GET", url)
        if "/tailoring/candidates" in url:
            return httpx.Response(200, json=self.candidates, request=request)
        if isinstance(self.view, httpx.Response):
            return self.view
        return httpx.Response(200, json=self.view, request=request)

    def _post(self, url: str, json: dict | None = None, **_kwargs: object):
        """Record a POST and answer with the current view.

        Args:
            url: The requested URL.
            json: The body.
            **_kwargs: Ignored httpx options.

        Returns:
            A 200 response carrying the view.
        """
        self.posts.append((url, json or {}))
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=self.view, request=request)

    def _app(self) -> AppTest:
        """Create the AppTest for the page.

        Returns:
            An unrun `AppTest`.
        """
        return AppTest.from_file(str(_PAGE), default_timeout=20)

    def _run(self, app: AppTest | None = None) -> AppTest:
        """Run (or rerun) the page against the fake API.

        Args:
            app: An existing AppTest to rerun, or None for a new one.

        Returns:
            The finished `AppTest`.
        """
        with (
            mock.patch("httpx.get", side_effect=self._get),
            mock.patch("httpx.post", side_effect=self._post),
        ):
            return (app if app is not None else self._app()).run()

    def _click(self, app: AppTest, key: str) -> AppTest:
        """Click a button and rerun with the fake API.

        Args:
            app: A finished AppTest.
            key: The button's key.

        Returns:
            The AppTest after the click.
        """
        with (
            mock.patch("httpx.get", side_effect=self._get),
            mock.patch("httpx.post", side_effect=self._post),
        ):
            return app.button(key=key).click().run()

    def test_it_shows_the_letter_text_and_the_three_pitch_variants(self) -> None:
        app = self._run()
        self.assertEqual(len(app.exception), 0)
        shown = _shown(app)
        self.assertIn("Re: Data Engineer", shown)
        self.assertIn("I built dbt models for risk reporting.", shown)
        labels = [t.label for t in app.tabs]
        self.assertEqual(labels, ["30 seconds", "60 seconds", "90 seconds"])
        self.assertIn("words", shown)

    def test_a_sentence_names_the_sources_it_rests_on(self) -> None:
        shown = _shown(self._run())
        self.assertIn("Built dbt models for risk reporting", shown)
        self.assertIn("written by the app from the job title", shown)

    def test_a_pending_sentence_shows_its_issue_and_offers_link_and_remove(
        self,
    ) -> None:
        app = self._run()
        keys = [b.key for b in app.button]
        self.assertIn(f"art-link-{_OPEN_ID}", keys)
        self.assertIn(f"art-remove-{_OPEN_ID}", keys)
        self.assertIn("adds ownership", _shown(app))
        select = app.selectbox(key=f"art-link-select-{_OPEN_ID}")
        self.assertIn("The job posting", select.options)
        self.assertIn("1 sentence(s) await your decision", _shown(app))

    def test_remove_posts_a_remove_decision_with_the_job_and_kind(self) -> None:
        app = self._click(self._run(), f"art-remove-{_OPEN_ID}")
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            self.posts,
            [
                (
                    f"{_base()}/artefacts/open-sentences/{_OPEN_ID}/decision",
                    {
                        "kind": "cover_letter",
                        "job_group_id": "zzfixture-job",
                        "action": "remove",
                        "evidence_ref": None,
                    },
                )
            ],
        )

    def test_link_posts_the_chosen_evidence(self) -> None:
        app = self._run()
        app.selectbox(key=f"art-link-select-{_OPEN_ID}").select("job:posting")
        app = self._click(app, f"art-link-{_OPEN_ID}")
        (url, body) = self.posts[0]
        self.assertTrue(url.endswith(f"/open-sentences/{_OPEN_ID}/decision"))
        self.assertEqual(body["action"], "link")
        self.assertEqual(body["evidence_ref"], "job:posting")

    def test_generate_asks_for_confirmation_with_the_cost_and_makes_no_call_yet(
        self,
    ) -> None:
        app = self._click(self._run(), "art-generate-letter")
        keys = [b.key for b in app.button]
        self.assertIn("art-confirm", keys)
        self.assertIn("art-cancel", keys)
        self.assertIn("$0.10", _shown(app))
        self.assertEqual(self.posts, [])

    def test_confirming_posts_to_the_cover_letter_endpoint_and_cancel_makes_no_call(
        self,
    ) -> None:
        app = self._click(self._run(), "art-generate-letter")
        app = self._click(app, "art-confirm")
        self.assertEqual(
            [u for u, _b in self.posts],
            [f"{_base()}/artefacts/jobs/zzfixture-job/cover-letter"],
        )
        self.posts.clear()
        app = self._click(self._run(), "art-generate-pitch")
        app = self._click(app, "art-cancel")
        self.assertEqual(self.posts, [])
        self.assertNotIn("art-confirm", [b.key for b in app.button])

    def test_without_saved_research_the_page_says_the_letter_uses_the_posting_alone(
        self,
    ) -> None:
        self.view = _view(has_saved_research=False)
        shown = _shown(self._run())
        self.assertIn("posting alone", shown)
        self.assertIn("Company Intel", shown)

    def test_an_approved_letter_offers_downloads_and_an_unapproved_one_does_not(
        self,
    ) -> None:
        self.view = _view(cover_letter=_artefact("cover_letter", "approved"))
        app = self._run()
        self.assertEqual(len(app.exception), 0)
        names = [d.proto.label for d in app.get("download_button")]
        self.assertTrue(any(n.endswith("cover_letter.pdf") for n in names), names)
        self.assertTrue(any(n.endswith("cover_letter.docx") for n in names), names)
        self.assertTrue(any(n.endswith("pitch.txt") for n in names), names)
        self.view = _view(pitch=None)
        app = self._run()
        self.assertEqual(
            [d for d in app.get("download_button") if "cover_letter" in d.proto.label],
            [],
        )

    def test_a_failed_run_shows_its_error(self) -> None:
        failed = _artefact("cover_letter", "failed")
        failed.update(content=None, error_message="the fact-checker was unusable")
        self.view = _view(cover_letter=failed)
        app = self._run()
        self.assertIn("the fact-checker was unusable", _shown(app))
        self.assertNotIn("Re: Data Engineer", _shown(app).split("Pitch")[0])

    def test_hostile_text_is_not_rendered_as_markdown(self) -> None:
        view = _view()
        view["cover_letter"] = copy.deepcopy(view["cover_letter"])
        view["cover_letter"]["open_sentences"] = [_open(_HOSTILE)]
        self.view = view
        app = self._run()
        markdown = " ".join(m.value for m in app.markdown)
        self.assertNotIn("[x](http://evil.example)", markdown)

    def test_a_job_link_opens_the_page_on_that_job(self) -> None:
        self.candidates = [_CANDIDATE, _OTHER]
        app = self._app()
        app.query_params["job"] = "zzfixture-other"
        app = self._run(app)
        self.assertEqual(app.selectbox(key="art-job").value, "zzfixture-other")

    def test_no_candidates_and_an_api_error_do_not_crash(self) -> None:
        self.candidates = []
        app = self._run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("No scored jobs", " ".join(i.value for i in app.info))
        with (
            mock.patch("httpx.get", side_effect=httpx.ConnectError("down")),
            mock.patch("httpx.post", side_effect=self._post),
        ):
            app = self._app().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 1)

    def test_no_cv_409_shows_a_clear_message(self) -> None:
        self.view = httpx.Response(
            409,
            json={"detail": "add your CV first"},
            request=httpx.Request("GET", "http://x"),
        )
        app = self._run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("add your CV first", " ".join(i.value for i in app.info))


def _base() -> str:
    """Return the API base URL the page uses.

    Returns:
        `Settings.api_base_url`.
    """
    from core.settings import get_settings

    return get_settings().api_base_url


if __name__ == "__main__":
    unittest.main()
