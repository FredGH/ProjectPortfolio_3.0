"""Headless render tests for the Tailored CV Review page (Streamlit AppTest)."""

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
    / "10_Tailored_CV_Review.py"
)

_HOSTILE = "[x](http://evil.example) ![p](http://evil/p.png) **b**"
_RUN_ID = "11111111-1111-1111-1111-111111111111"
_ORPHAN_ID = "22222222-2222-2222-2222-222222222222"

_CANDIDATE = {
    "job_group_id": "zzfixture-job",
    "title_for_display": "Lead Data Engineer",
    "company": "Gamma",
    "final_score": 0.81,
    "latest_run_id": _RUN_ID,
    "latest_status": "needs_review",
}
_DOCUMENT = {
    "target_title": "Lead Data Engineer",
    "headline": "Lead Data Engineer",
    "identity": "Zz Fixture",
    "summary": {
        "text": "Data engineer.",
        "evidence_refs": ["b1"],
        "origin": "reworded",
    },
    "experience": [
        {
            "truth_index": 0,
            "company": "Acme Bank",
            "title": "Senior Data Engineer",
            "start": "2019-01",
            "end": None,
            "tech": [],
            "bullets": [
                {
                    "text": "Built dbt models powering risk reporting",
                    "evidence_refs": ["b1"],
                    "origin": "reworded",
                },
                {
                    "text": "[click me](http://evil.example) **x**",
                    "evidence_refs": [],
                    "origin": "orphan",
                },
            ],
        },
        {
            "truth_index": 1,
            "company": "Beta Retail",
            "title": "Data Analyst",
            "start": "2015-06",
            "end": "2018-12",
            "tech": [],
            "bullets": [],
        },
    ],
    "skills": [
        {
            "name": "dbt",
            "canonical_id": None,
            "years": None,
            "last_used": None,
            "evidence_refs": [],
        }
    ],
    "stretch": {"is_stretch": True, "reason": "Lead implies managing people"},
    "keyword_coverage": {
        "covered": ["dbt"],
        "missing_evidenced": ["SQL"],
        "missing_unevidenced": ["Kubernetes"],
    },
}
_RUN = {
    "run_id": _RUN_ID,
    "job_group_id": "zzfixture-job",
    "target_title": "Lead Data Engineer",
    "status": "needs_review",
    "attempts": 3,
    "error_message": None,
    "document": _DOCUMENT,
    "orphans": [
        {
            "id": _ORPHAN_ID,
            "kind": "orphan",
            "section": "experience",
            "experience_index": 0,
            "bullet_index": 1,
            "text": "[click me](http://evil.example) **x**",
            "claimed_refs": [],
            "issue": "no evidence_ref in the CV",
            "status": "pending",
            "evidence_ref": None,
        }
    ],
    "sources": [
        {
            "bullet_id": "b1",
            "text": "Built dbt models for risk reporting",
            "experience_index": 0,
            "role": "Senior Data Engineer at Acme Bank",
        },
        {
            "bullet_id": "b2",
            "text": "Wrote SQL reports",
            "experience_index": 1,
            "role": "Data Analyst at Beta Retail",
        },
    ],
}


def _fake_get(candidates, run):
    def _fake(url: str, **_kwargs) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url.endswith("/tailoring/candidates") or "/tailoring/candidates?" in url:
            return httpx.Response(200, json=candidates, request=request)
        if "/tailoring/runs/" in url:
            if run is None:
                return httpx.Response(
                    404, json={"detail": "unknown run"}, request=request
                )
            return httpx.Response(200, json=run, request=request)
        return httpx.Response(200, json=[], request=request)

    return _fake


class TestTailoredCvReviewPage(unittest.TestCase):
    def test_renders_with_no_candidates(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([], None)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("No scored jobs", " ".join(c.value for c in app.info))

    def test_shows_candidates_and_a_tailor_button(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], None)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("tailor-start", [b.key for b in app.button])
        self.assertTrue(
            any("Lead Data Engineer" in o for o in app.selectbox[0].options)
        )

    def test_shows_the_run_the_stretch_warning_and_keyword_coverage(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        warnings = " ".join(w.value for w in app.warning)
        self.assertIn("Lead implies managing people", warnings)
        shown = " ".join(t.value for t in app.text)
        self.assertIn("SQL", shown)
        self.assertIn("Kubernetes", shown)

    def test_shows_each_pending_orphan_with_link_and_reject(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        keys = [b.key for b in app.button]
        self.assertIn(f"link-{_ORPHAN_ID}", keys)
        self.assertIn(f"reject-{_ORPHAN_ID}", keys)

    def test_the_link_picker_offers_only_the_same_roles_bullets(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        picker = next(s for s in app.selectbox if s.key == f"link-select-{_ORPHAN_ID}")
        joined = " ".join(picker.options)
        self.assertIn("Built dbt models for risk reporting", joined)
        self.assertNotIn("Wrote SQL reports", joined)

    def test_cv_and_orphan_text_is_rendered_as_plain_text_not_markdown(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        markdown = " ".join(m.value for m in app.markdown)
        self.assertNotIn("[click me](http://evil.example)", markdown)
        self.assertIn(
            "[click me](http://evil.example) **x**", " ".join(t.value for t in app.text)
        )

    def test_a_failed_run_shows_its_error_and_no_orphan_controls(self) -> None:
        failed = {
            **_RUN,
            "status": "failed",
            "document": None,
            "orphans": [_RUN["orphans"][0]],
            "error_message": "CriticError: unusable critic reply",
        }
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], failed)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("unusable critic reply", " ".join(e.value for e in app.error))
        self.assertFalse([b for b in app.button if b.key.startswith("link-")])
        self.assertFalse([b for b in app.button if b.key.startswith("reject-")])

    def test_an_approved_run_has_no_orphan_controls(self) -> None:
        decided = [
            {**_RUN["orphans"][0], "status": "linked", "evidence_ref": "b1"},
            {**_RUN["orphans"][0], "id": "33", "status": "rejected"},
        ]
        approved = {**_RUN, "status": "approved", "orphans": decided}
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], approved)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertFalse([b for b in app.button if b.key.startswith("reject-")])
        self.assertFalse([b for b in app.button if b.key.startswith("link-")])
        self.assertTrue(app.success)
        self.assertIn("approved", " ".join(m.value for m in app.markdown).lower())

    def test_clicking_link_posts_the_decision(self) -> None:
        with (
            mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)),
            mock.patch("httpx.post") as post,
        ):
            post.return_value = httpx.Response(
                200, json=_RUN, request=httpx.Request("POST", "http://x")
            )
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            link = next(b for b in app.button if b.key == f"link-{_ORPHAN_ID}")
            link.click().run()
        url = post.call_args.args[0]
        self.assertTrue(url.endswith(f"/tailoring/orphans/{_ORPHAN_ID}/decision"))
        self.assertEqual(post.call_args.kwargs["json"]["action"], "link")
        self.assertEqual(post.call_args.kwargs["json"]["evidence_ref"], "b1")

    def test_an_api_rejection_shows_the_detail(self) -> None:
        with (
            mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)),
            mock.patch("httpx.post") as post,
        ):
            post.return_value = httpx.Response(
                422,
                json={"detail": "the evidence must come from the same role"},
                request=httpx.Request("POST", "http://x"),
            )
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == f"link-{_ORPHAN_ID}").click().run()
        self.assertIn("same role", " ".join(e.value for e in app.error))

    def test_the_api_being_down_shows_an_error_not_a_crash(self) -> None:
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)

    def _assert_no_live_markdown(self, app: AppTest) -> None:
        elements = [
            *app.markdown,
            *app.caption,
            *app.error,
            *app.warning,
            *app.info,
            *app.success,
        ]
        for element in elements:
            self.assertNotIn("[x](http", element.value)
            self.assertNotIn("![p](http", element.value)
            self.assertNotIn("**b**", element.value)

    def test_hostile_api_text_never_renders_as_markdown(self) -> None:
        doc = {
            **_DOCUMENT,
            "stretch": {"is_stretch": True, "reason": _HOSTILE},
            "keyword_coverage": {
                "covered": [_HOSTILE],
                "missing_evidenced": [_HOSTILE],
                "missing_unevidenced": [_HOSTILE],
            },
        }
        orphan = {**_RUN["orphans"][0], "issue": _HOSTILE}
        run = {**_RUN, "document": doc, "orphans": [orphan]}
        with (
            mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], run)),
            mock.patch("httpx.post") as post,
        ):
            post.return_value = httpx.Response(
                422,
                json={"detail": _HOSTILE},
                request=httpx.Request("POST", "http://x"),
            )
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            self._assert_no_live_markdown(app)
            self.assertIn(_HOSTILE, " ".join(t.value for t in app.text))
            next(b for b in app.button if b.key == f"reject-{_ORPHAN_ID}").click().run()
        self._assert_no_live_markdown(app)
        self.assertTrue(app.error)

    def test_hostile_run_error_never_renders_as_markdown(self) -> None:
        failed = {
            **_RUN,
            "status": "failed",
            "document": None,
            "error_message": _HOSTILE,
        }
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], failed)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)
        self._assert_no_live_markdown(app)

    def _click_with_post(self, key: str, post_kwargs: dict) -> AppTest:
        with (
            mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)),
            mock.patch("httpx.post", **post_kwargs),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == key).click().run()
        return app

    def test_api_down_on_tailor_link_and_reject_shows_an_error(self) -> None:
        for key in ("tailor-start", f"link-{_ORPHAN_ID}", f"reject-{_ORPHAN_ID}"):
            with self.subTest(key=key):
                app = self._click_with_post(
                    key, {"side_effect": httpx.ConnectError("down")}
                )
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(app.error)

    def test_a_202_with_a_non_json_body_shows_an_error_not_a_crash(self) -> None:
        bad = httpx.Response(
            202, content=b"<html>", request=httpx.Request("POST", "http://x")
        )
        app = self._click_with_post("tailor-start", {"return_value": bad})
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)

    def test_a_non_json_get_body_shows_an_error_not_a_crash(self) -> None:
        bad = httpx.Response(
            200, content=b"<html>", request=httpx.Request("GET", "http://x")
        )
        with mock.patch("httpx.get", return_value=bad):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)


if __name__ == "__main__":
    unittest.main()
