"""Headless render tests for the Tailored CV Review page (Streamlit AppTest)."""

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


def _choose_link_target(app: AppTest) -> None:
    picker = next(s for s in app.selectbox if s.key == f"link-select-{_ORPHAN_ID}")
    picker.select("b1").run()


_BACKENDS = [
    {
        "id": "claude",
        "label": "Claude · claude-sonnet-5",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "available": True,
        "detail": "API key configured",
        "default": True,
    },
    {
        "id": "native",
        "label": "Ollama on this Mac · llama3.1:8b",
        "provider": "ollama",
        "model": "llama3.1:8b",
        "available": False,
        "detail": "not reachable at http://host.docker.internal:11434",
        "default": False,
    },
    {
        "id": "docker",
        "label": "Docker Ollama (CPU only, slow) · llama3.1:8b",
        "provider": "ollama",
        "model": "llama3.1:8b",
        "available": True,
        "detail": "reachable, model present",
        "default": False,
    },
]


def _fake_get(candidates, run, backends=None):
    def _fake(url: str, **_kwargs) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url.endswith("/tailoring/backends"):
            if backends is None:
                return httpx.Response(200, json=[], request=request)
            if isinstance(backends, Exception):
                raise backends
            return httpx.Response(200, json=backends, request=request)
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

    def test_no_developer_text_leaks_onto_the_page(self) -> None:
        # A bare string literal in the page script is rendered by Streamlit
        # "magic"; the stale-threshold note once leaked this way.
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], None)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        shown = " ".join(m.value for m in app.markdown)
        self.assertNotIn("Minutes without activity", shown)
        self.assertNotIn("client timeout", shown)

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
            _choose_link_target(app)
            link = next(b for b in app.button if b.key == f"link-{_ORPHAN_ID}")
            self.assertFalse(link.disabled)
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
            _choose_link_target(app)
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

    # --- live progress of a generating run ---------------------------------

    @staticmethod
    def _generating(**overrides) -> dict:
        now = datetime.now(UTC)
        run = {
            **_RUN,
            "status": "generating",
            "attempts": 0,
            "document": None,
            "orphans": [],
            "progress": {
                "attempt": 2,
                "max_attempts": 3,
                "phase": "critic",
                "message": "Attempt 2 of 3: Claude is fact-checking 4 line(s)…",
                "phase_started_at": now.isoformat(),
                "history": ["Attempt 1: 2 point(s) to fix — trying again"],
            },
            "started_at": (now - timedelta(seconds=125)).isoformat(),
            "updated_at": (now - timedelta(seconds=7)).isoformat(),
        }
        run.update(overrides)
        return run

    def _render_generating(self, run: dict) -> AppTest:
        # The page polls with sleep + rerun; stop after the first render.
        with (
            mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], run)),
            mock.patch("time.sleep", side_effect=lambda _s: st.stop()),
        ):
            return AppTest.from_file(str(_PAGE), default_timeout=10).run()

    @staticmethod
    def _all_text(app: AppTest) -> str:
        return " | ".join(
            e.value for e in [*app.text, *app.caption, *app.warning, *app.markdown]
        )

    def test_a_generating_run_shows_phase_history_and_timers(self) -> None:
        app = self._render_generating(self._generating())
        self.assertEqual(len(app.exception), 0)
        shown = self._all_text(app)
        self.assertIn("Attempt 2 of 3: Claude is fact-checking 4 line", shown)
        self.assertIn("Attempt 1: 2 point(s) to fix", shown)
        self.assertRegex(shown, r"Running for 0[2-3]:\d\d")
        self.assertRegex(shown, r"Last activity 00:\d\d ago")
        self.assertIn("can take several minutes per attempt", shown)
        self.assertEqual(len(app.warning), 0)

    def test_a_generating_run_without_progress_says_starting(self) -> None:
        app = self._render_generating(self._generating(progress=None))
        self.assertEqual(len(app.exception), 0)
        self.assertIn("Starting…", self._all_text(app))

    def test_a_stale_generating_run_warns_it_may_have_stopped(self) -> None:
        old = (datetime.now(UTC) - timedelta(minutes=40)).isoformat()
        app = self._render_generating(self._generating(updated_at=old))
        warning = " ".join(w.value for w in app.warning)
        self.assertIn("No activity for 40 minutes", warning)
        self.assertIn("Tailor my CV to this job", warning)

    def test_a_recent_run_just_under_the_threshold_does_not_warn(self) -> None:
        # One local Tailor call can legitimately take ~33 minutes.
        recent = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
        app = self._render_generating(self._generating(updated_at=recent))
        self.assertEqual(len(app.warning), 0)

    def test_naive_and_future_timestamps_are_tolerated(self) -> None:
        naive = datetime.now(UTC).replace(tzinfo=None).isoformat()
        future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        app = self._render_generating(
            self._generating(started_at=naive, updated_at=future)
        )
        self.assertEqual(len(app.exception), 0)
        shown = self._all_text(app)
        self.assertIn("Running for 00:0", shown)
        self.assertIn("Last activity 00:00 ago", shown)

    def test_malformed_timestamps_and_progress_do_not_crash(self) -> None:
        for overrides in (
            {"started_at": "garbage", "updated_at": None},
            {"started_at": 12, "updated_at": ["x"]},
            {"progress": "oops"},
            {"progress": {"message": 5, "history": "no"}},
            {"progress": {"message": "ok", "history": [1, None, "fine"]}},
        ):
            with self.subTest(overrides=overrides):
                app = self._render_generating(self._generating(**overrides))
                self.assertEqual(len(app.exception), 0)
                self.assertIn("Status", self._all_text(app))

    def test_hostile_progress_text_is_not_rendered_as_markdown(self) -> None:
        progress = {
            "message": _HOSTILE,
            "history": [_HOSTILE],
        }
        app = self._render_generating(self._generating(progress=progress))
        self.assertEqual(len(app.exception), 0)
        self._assert_no_live_markdown(app)
        self.assertIn(_HOSTILE, " ".join(t.value for t in app.text))

    # --- cancelling a run ---------------------------------------------------

    # --- backend selector ------------------------------------------------

    def _render(self, run=None, backends=_BACKENDS):
        patcher = mock.patch(
            "httpx.get", side_effect=_fake_get([_CANDIDATE], run, backends)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return AppTest.from_file(str(_PAGE), default_timeout=10).run()

    def _backend_box(self, app: AppTest):
        return next(s for s in app.selectbox if s.key == "tailoring_backend")

    def test_the_selector_lists_every_backend_with_a_badge_and_the_default(
        self,
    ) -> None:
        app = self._render()
        box = self._backend_box(app)
        self.assertEqual(
            list(box.options),
            [
                "✓ Claude · claude-sonnet-5",
                "✗ Ollama on this Mac · llama3.1:8b",
                "✓ Docker Ollama (CPU only, slow) · llama3.1:8b",
            ],
        )
        self.assertEqual(box.value, "claude")
        self.assertIn("API key configured", " ".join(c.value for c in app.caption))
        tailor = next(b for b in app.button if b.key == "tailor-start")
        self.assertFalse(tailor.disabled)

    def test_an_unavailable_backend_disables_tailor_and_says_why(self) -> None:
        app = self._render()
        self._backend_box(app).select("native").run()
        tailor = next(b for b in app.button if b.key == "tailor-start")
        self.assertTrue(tailor.disabled)
        warnings = " ".join(w.value for w in app.warning)
        self.assertIn("is not available", warnings)
        self.assertIn("not reachable at", warnings)

    def test_the_click_posts_the_chosen_backend(self) -> None:
        post_response = httpx.Response(
            202, json={"run_id": _RUN_ID}, request=httpx.Request("POST", "http://x")
        )
        with (
            mock.patch(
                "httpx.get", side_effect=_fake_get([_CANDIDATE], None, _BACKENDS)
            ),
            mock.patch("httpx.post", return_value=post_response) as post,
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            self._backend_box(app).select("docker").run()
            next(b for b in app.button if b.key == "tailor-start").click().run()
        self.assertEqual(
            post.call_args.kwargs["json"],
            {"job_group_id": "zzfixture-job", "backend": "docker"},
        )

    def test_a_409_detail_is_shown(self) -> None:
        refused = httpx.Response(
            409,
            json={"detail": "Docker Ollama is not available: down"},
            request=httpx.Request("POST", "http://x"),
        )
        with (
            mock.patch(
                "httpx.get", side_effect=_fake_get([_CANDIDATE], None, _BACKENDS)
            ),
            mock.patch("httpx.post", return_value=refused),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == "tailor-start").click().run()
        shown = " ".join(e.value for e in app.error)
        self.assertIn("Could not start", shown)
        self.assertIn("is not available", shown)

    def test_a_failing_backends_call_falls_back_to_the_old_behaviour(self) -> None:
        post_response = httpx.Response(
            202, json={"run_id": _RUN_ID}, request=httpx.Request("POST", "http://x")
        )
        with (
            mock.patch(
                "httpx.get",
                side_effect=_fake_get(
                    [_CANDIDATE], None, httpx.ConnectError("backends down")
                ),
            ),
            mock.patch("httpx.post", return_value=post_response) as post,
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            self.assertEqual(len(app.exception), 0)
            self.assertNotIn("tailoring_backend", [s.key for s in app.selectbox])
            self.assertIn("Tailor backends", " ".join(i.value for i in app.info))
            next(b for b in app.button if b.key == "tailor-start").click().run()
        self.assertEqual(
            post.call_args.kwargs["json"], {"job_group_id": "zzfixture-job"}
        )

    def test_the_run_shows_its_tailor_label(self) -> None:
        run = {**_RUN, "tailor_backend": "docker", "tailor_label": "Docker Ollama · m"}
        app = self._render(run)
        self.assertIn("Tailor: Docker Ollama · m", " ".join(t.value for t in app.text))

    def test_the_cancel_caption_depends_on_the_backend(self) -> None:
        patcher = mock.patch("time.sleep", side_effect=lambda _s: st.stop())
        patcher.start()
        self.addCleanup(patcher.stop)
        base = {**_RUN, "status": "generating", "document": None, "orphans": []}
        local = self._render({**base, "tailor_backend": "docker"})
        self.assertIn(
            "Cancel stops the run at once and closes the connection to the local "
            "model, which stops generating within a few seconds.",
            " ".join(c.value for c in local.caption),
        )
        claude = self._render({**base, "tailor_backend": "claude"})
        self.assertIn(
            "Cancel stops the run at once; a Claude call already in flight "
            "finishes in the background and its result is discarded.",
            " ".join(c.value for c in claude.caption),
        )

    def test_hostile_backend_text_is_not_rendered_as_markdown(self) -> None:
        hostile = [{**_BACKENDS[0], "detail": _HOSTILE}]
        app = self._render(backends=hostile)
        shown = " ".join(c.value for c in app.caption)
        self.assertNotIn("](http", shown.replace("\\]\\(http", ""))
        self.assertIn("\\[x\\]", shown)

    def test_cancel_button_only_while_generating(self) -> None:
        app = self._render_generating(self._generating())
        self.assertIn("cancel-run", [b.key for b in app.button])
        self.assertIn(
            "Cancel stops the run at once; a Claude call already in flight "
            "finishes in the background and its result is discarded.",
            self._all_text(app),
        )
        for run in (_RUN, {**_RUN, "status": "failed", "document": None}):
            with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], run)):
                other = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            self.assertNotIn("cancel-run", [b.key for b in other.button])

    def test_clicking_cancel_posts_to_the_cancel_endpoint(self) -> None:
        cancelled = httpx.Response(
            200,
            json={**_RUN, "status": "cancelled"},
            request=httpx.Request("POST", "http://x"),
        )
        with (
            mock.patch(
                "httpx.get", side_effect=_fake_get([_CANDIDATE], self._generating())
            ),
            mock.patch("httpx.post", return_value=cancelled) as post,
            mock.patch("time.sleep", side_effect=lambda _s: st.stop()),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == "cancel-run").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(
            post.call_args.args[0].endswith(f"/tailoring/runs/{_RUN_ID}/cancel")
        )

    def test_api_down_or_refusing_on_cancel_shows_an_error_not_a_crash(self) -> None:
        refusal = httpx.Response(
            409,
            json={"detail": "this run is not running"},
            request=httpx.Request("POST", "http://x"),
        )
        for post_kwargs in (
            {"side_effect": httpx.ConnectError("down")},
            {"return_value": refusal},
        ):
            with self.subTest(post=post_kwargs):
                with (
                    mock.patch(
                        "httpx.get",
                        side_effect=_fake_get([_CANDIDATE], self._generating()),
                    ),
                    mock.patch("httpx.post", **post_kwargs),
                    mock.patch("time.sleep", side_effect=lambda _s: st.stop()),
                ):
                    app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
                    next(b for b in app.button if b.key == "cancel-run").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(app.error)
                self._assert_no_live_markdown(app)

    def test_a_cancelled_run_shows_an_info_and_the_tailor_button_only(self) -> None:
        cancelled = {
            **_RUN,
            "status": "cancelled",
            "document": None,
            "orphans": [_RUN["orphans"][0]],
        }
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], cancelled)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("This run was cancelled.", " ".join(i.value for i in app.info))
        keys = [b.key for b in app.button]
        self.assertIn("tailor-start", keys)
        self.assertFalse(any(k.startswith(("link-", "reject-")) for k in keys))
        self.assertNotIn("cancel-run", keys)

    def _click_with_post(self, key: str, post_kwargs: dict) -> AppTest:
        with (
            mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)),
            mock.patch("httpx.post", **post_kwargs),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            if key.startswith("link-"):
                _choose_link_target(app)
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

    # --- each line beside its source (I4) and no one-click Link (I6) ------

    def test_each_bullet_shows_its_source_text_not_its_id(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        texts = [t.value for t in app.text]
        self.assertIn("reworded · source: Built dbt models for risk reporting", texts)
        self.assertIn("orphan · source: no source", texts)
        self.assertIn(
            "summary · reworded · source: Built dbt models for risk reporting", texts
        )
        self.assertFalse([t for t in texts if t.endswith("source: b1")])

    def test_a_long_source_text_is_truncated(self) -> None:
        long_text = "Built " + "very " * 80 + "long pipelines"
        sources = [{**_RUN["sources"][0], "text": long_text}, _RUN["sources"][1]]
        run = {**_RUN, "sources": sources}
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], run)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        line = next(
            t.value for t in app.text if t.value.startswith("reworded · source:")
        )
        self.assertLess(len(line), len(long_text))
        self.assertTrue(line.endswith("…"))

    def test_the_orphan_header_names_the_role(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        texts = [t.value for t in app.text]
        self.assertIn("Senior Data Engineer at Acme Bank (orphan)", texts)
        self.assertFalse([t for t in texts if t.startswith("Role 0")])

    def test_an_unsupported_orphan_shows_its_claimed_source_text(self) -> None:
        orphan = {
            **_RUN["orphans"][0],
            "kind": "unsupported",
            "bullet_index": 0,
            "text": "Built dbt models powering risk reporting",
            "claimed_refs": ["b1"],
            "issue": "adds a claim",
        }
        run = {**_RUN, "orphans": [orphan]}
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], run)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertIn(
            "Claimed source: Built dbt models for risk reporting",
            [t.value for t in app.text],
        )

    def test_hostile_source_text_is_shown_as_plain_text(self) -> None:
        sources = [{**_RUN["sources"][0], "text": _HOSTILE}, _RUN["sources"][1]]
        run = {**_RUN, "sources": sources}
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], run)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self._assert_no_live_markdown(app)
        self.assertIn(_HOSTILE, " ".join(t.value for t in app.text))

    def test_link_is_disabled_until_a_bullet_is_chosen(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            picker = next(
                s for s in app.selectbox if s.key == f"link-select-{_ORPHAN_ID}"
            )
            self.assertIsNone(picker.value)
            link = next(b for b in app.button if b.key == f"link-{_ORPHAN_ID}")
            self.assertTrue(link.disabled)
            _choose_link_target(app)
            link = next(b for b in app.button if b.key == f"link-{_ORPHAN_ID}")
            self.assertFalse(link.disabled)


if __name__ == "__main__":
    unittest.main()
