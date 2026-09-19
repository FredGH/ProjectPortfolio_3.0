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


if __name__ == "__main__":
    unittest.main()
