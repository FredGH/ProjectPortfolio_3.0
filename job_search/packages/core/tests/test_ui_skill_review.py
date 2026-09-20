"""Headless render test for the Skill Review page (Streamlit AppTest)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import httpx
from streamlit.testing.v1 import AppTest

_PAGE = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "ui"
    / "app"
    / "pages"
    / "6_Skill_Review.py"
)

_UNMAPPED = {
    "raw_norm": "zzfixture thing",
    "raw_example": "ZZFixture Thing",
    "review_status": "open",
    "seen_in_cv": True,
    "jd_job_count": 3,
    "sample_job_group_ids": ["j1"],
    "candidate_skill_id": "fixture-cloud",
    "candidate_label": "cloud technologies",
    "candidate_score": 0.62,
}
_REJECTED = {
    **_UNMAPPED,
    "raw_norm": "zzfixture rejected",
    "raw_example": "ZZFixture Rejected",
    "review_status": "rejected",
}
_MATCH = {
    "raw_norm": "zzfixture matched",
    "raw_example": "ZZFixture Matched",
    "skill_id": "fixture-python",
    "skill_label": "python",
    "method": "embedding",
    "score": 0.86,
    "suspicious": False,
    "seen_in_cv": False,
    "jd_job_count": 1,
}
_LABEL_MATCH = {
    "raw_norm": "zzfixture kotlin",
    "raw_example": "ZZFixture Kotlin",
    "skill_id": "fixture-cloud",
    "skill_label": "computer programming",
    "method": "label",
    "score": None,
    "suspicious": True,
    "seen_in_cv": True,
    "jd_job_count": 2,
}
_INJECTION = "[click me](http://evil.example) ![pixel](http://evil.example/p.png) **x**"


def _get_returning(unmapped: list[dict], matches: list[dict] | None = None):
    """Build an `httpx.get` stand-in serving given review lists.

    Args:
        unmapped: The items `GET /skills/review` should return.
        matches: The items `GET /skills/review/auto-matches` should return
            (default: one embedding match).

    Returns:
        A callable usable as `httpx.get`'s `side_effect`.
    """
    served_matches = [_MATCH] if matches is None else matches

    def _fake(url: str, **_kwargs) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url.endswith("/skills/review/auto-matches"):
            return httpx.Response(200, json=served_matches, request=request)
        if url.endswith("/skills/review"):
            return httpx.Response(200, json=unmapped, request=request)
        return httpx.Response(200, json=[], request=request)

    return _fake


_fake_get = _get_returning([_UNMAPPED])


class TestSkillReviewPage(unittest.TestCase):
    def test_renders_both_lists_with_their_actions(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        shown = " ".join(m.value for m in app.markdown)
        self.assertIn("ZZFixture Thing", shown)
        self.assertIn("ZZFixture Matched", shown)
        labels = [b.label for b in app.button]
        self.assertIn("Accept suggestion: cloud technologies (0.62)", labels)
        self.assertIn("Dismiss", labels)
        self.assertIn("Confirm", labels)
        self.assertIn("Reject", labels)

    def test_a_rejected_item_shows_what_was_rejected_and_no_accept_button(self) -> None:
        # The candidate of a rejected row is the match the reviewer threw
        # out, so a one-click "Accept suggestion" would hand it straight
        # back; an open row must still offer it.
        with mock.patch(
            "httpx.get", side_effect=_get_returning([_UNMAPPED, _REJECTED])
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        labels = [b.label for b in app.button]
        self.assertEqual(
            [label for label in labels if label.startswith("Accept suggestion")],
            ["Accept suggestion: cloud technologies (0.62)"],
        )
        captions = [c.value for c in app.caption]
        self.assertIn("Previously rejected: cloud technologies (0.62)", captions)

    def test_the_tabs_are_unmapped_and_auto_matches(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(
            [tab.label for tab in app.tabs], ["Unmapped", "Auto-matches — verify"]
        )

    def test_the_verify_tab_shows_each_matchs_method_and_flags_suspicious_ones(
        self,
    ) -> None:
        with mock.patch(
            "httpx.get",
            side_effect=_get_returning([_UNMAPPED], [_LABEL_MATCH, _MATCH]),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        shown = " ".join(m.value for m in app.markdown)
        self.assertIn("exact ESCO label match", shown)
        self.assertIn("similarity 0.86", shown)
        self.assertIn("computer programming", shown)
        self.assertIn("on your CV", shown)
        # Only the suspicious label match carries a warning.
        self.assertEqual(len(app.warning), 1)
        self.assertIn("alternative ESCO label", app.warning[0].value)
        labels = [b.label for b in app.button]
        self.assertEqual(labels.count("Confirm"), 2)
        self.assertEqual(labels.count("Reject"), 2)

    def test_job_text_is_rendered_as_plain_text_not_markdown(self) -> None:
        # A job description is third-party text: a link or image in a skill
        # string must not become a live link or a tracking pixel.
        hostile_unmapped = {**_UNMAPPED, "raw_example": _INJECTION}
        hostile_match = {**_LABEL_MATCH, "raw_example": _INJECTION}
        with mock.patch(
            "httpx.get",
            side_effect=_get_returning([hostile_unmapped], [hostile_match]),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        rendered = [m.value for m in app.markdown] + [c.value for c in app.caption]
        for value in rendered:
            self.assertNotIn("](http", value)
            self.assertNotIn("![", value)
        shown = " ".join(rendered)
        self.assertIn("\\[click me\\]", shown)  # the escaped, literal form
        self.assertIn("\\*\\*x\\*\\*", shown)

    def test_a_rejected_label_match_with_no_score_renders_without_crashing(
        self,
    ) -> None:
        # A rejected *label* match has no cosine score to show.
        no_score = {**_REJECTED, "candidate_score": None}
        open_no_score = {**_UNMAPPED, "candidate_score": None}
        with mock.patch(
            "httpx.get", side_effect=_get_returning([no_score, open_no_score])
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn(
            "Previously rejected: cloud technologies", [c.value for c in app.caption]
        )
        self.assertIn(
            "Accept suggestion: cloud technologies", [b.label for b in app.button]
        )

    def test_shows_an_error_instead_of_crashing_when_the_api_is_down(self) -> None:
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertGreaterEqual(len(app.error), 1)


def _dismiss_with(post_response: httpx.Response) -> tuple[AppTest, mock.MagicMock]:
    """Render the page, click Dismiss, and return the app and the post mock."""
    with (
        mock.patch("httpx.get", side_effect=_fake_get),
        mock.patch("httpx.post", return_value=post_response) as post,
    ):
        app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        (button,) = [b for b in app.button if b.label == "Dismiss"]
        app = button.click().run()
    return app, post


def _post_response(status: int, **kwargs) -> httpx.Response:
    request = httpx.Request("POST", "http://api/skills/review/dismiss")
    return httpx.Response(status, request=request, **kwargs)


class TestSkillReviewActions(unittest.TestCase):
    def test_a_successful_action_posts_the_raw_norm_and_shows_no_error(self) -> None:
        app, post = _dismiss_with(_post_response(200, json={"status": "ok"}))
        post.assert_called_once()
        self.assertTrue(post.call_args.args[0].endswith("/skills/review/dismiss"))
        self.assertEqual(post.call_args.kwargs["json"], {"raw_norm": "zzfixture thing"})
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)

    def test_shows_the_api_detail_when_the_action_is_rejected(self) -> None:
        detail = "unknown skill string 'zzfixture thing'"
        app, _ = _dismiss_with(_post_response(404, json={"detail": detail}))
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([e.value for e in app.error], [detail])

    def test_shows_an_error_instead_of_crashing_on_a_plain_text_500(self) -> None:
        app, _ = _dismiss_with(_post_response(500, text="Internal Server Error"))
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 1)
        self.assertIn("500", app.error[0].value)

    def test_joins_the_messages_of_a_request_validation_error(self) -> None:
        detail = [{"msg": "field required"}, {"msg": "value is not valid"}]
        app, _ = _dismiss_with(_post_response(422, json={"detail": detail}))
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            [e.value for e in app.error], ["field required; value is not valid"]
        )


if __name__ == "__main__":
    unittest.main()
