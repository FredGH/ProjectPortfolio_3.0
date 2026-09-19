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
    "seen_in_cv": True,
    "jd_job_count": 3,
    "sample_job_group_ids": ["j1"],
    "candidate_skill_id": "fixture-cloud",
    "candidate_label": "cloud technologies",
    "candidate_score": 0.62,
}
_MATCH = {
    "raw_norm": "zzfixture matched",
    "raw_example": "ZZFixture Matched",
    "skill_id": "fixture-python",
    "skill_label": "python",
    "score": 0.86,
    "jd_job_count": 1,
}


def _fake_get(url: str, **_kwargs) -> httpx.Response:
    request = httpx.Request("GET", url)
    if url.endswith("/skills/review/embedding-matches"):
        return httpx.Response(200, json=[_MATCH], request=request)
    if url.endswith("/skills/review"):
        return httpx.Response(200, json=[_UNMAPPED], request=request)
    return httpx.Response(200, json=[], request=request)


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
