"""Headless render tests for the Interview Prep page (Streamlit AppTest)."""

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
    / "13_Interview_Prep.py"
)
_HOSTILE = "[x](http://evil.example) **b**"
_QID = "11111111-1111-1111-1111-111111111111"
_OPEN = "22222222-2222-2222-2222-222222222222"


def _question(**overrides: object) -> dict:
    """Build the API's question model.

    Args:
        **overrides: Fields to replace.

    Returns:
        A question dict.
    """
    base = {
        "id": _QID,
        "qa_category": "data_engineer",
        "topic": "SQL",
        "kind": "technical",
        "difficulty": 3,
        "question": "What is a CTE?",
        "model_answer": "A named subquery.",
        "tags": ["sql"],
        "personal_answer": None,
        "answer_status": "none",
        "next_review": None,
        "cv_gap_score": 0.8,
        "open_sentences": [],
    }
    return {**base, **overrides}


_BANK = {
    "rows": [
        {
            "qa_category": "data_engineer",
            "topic": "SQL",
            "approved": 12,
            "needs_review": 1,
        }
    ],
    "work": [
        {
            "category": "data_engineer",
            "topic": "SQL",
            "kind": "technical",
            "missing": 18,
            "estimated_cost_usd": 0.16,
        }
    ],
    "unconfigured": [],
    "estimated_cost_usd": 0.16,
}
_ANKI = (
    "#separator:tab\n#html:true\n#tags column:3\n"
    "What is a CTE?\tA named subquery.\tSQL\n"
)


def _shown(app: AppTest) -> str:
    """Collect the page's text with markdown escapes removed.

    Args:
        app: A finished `AppTest`.

    Returns:
        The text joined by spaces.
    """
    parts = [
        e.value
        for group in (
            app.markdown,
            app.caption,
            app.info,
            app.warning,
            app.success,
            app.error,
            app.subheader,
        )
        for e in group
    ]
    return " ".join(parts).replace("\\", "")


class TestInterviewPage(unittest.TestCase):
    """The page drills, drafts, decides and exports."""

    def setUp(self) -> None:
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        self.sent: list[tuple[str, str, dict]] = []
        self.bank = _BANK
        self.queue = [_question()]
        self.gaps = [
            _question(question="Low?", cv_gap_score=0.1),
            _question(question="High?", cv_gap_score=0.9),
        ]
        self.anki = _ANKI

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Serve the fake API.

        Args:
            method: The HTTP method.
            url: The URL.
            **kwargs: httpx options; `json` is recorded for writes.

        Returns:
            The canned response.
        """
        request = httpx.Request(method, url)
        if method != "GET":
            self.sent.append((method, url, kwargs.get("json") or {}))  # type: ignore[arg-type]
            return httpx.Response(
                200, json=[] if "generate" in url else _question(), request=request
            )
        if url.endswith("/interview/bank"):
            return httpx.Response(200, json=self.bank, request=request)
        if "/interview/queue" in url:
            return httpx.Response(200, json=self.queue, request=request)
        if "/interview/gaps" in url:
            return httpx.Response(200, json=self.gaps, request=request)
        if "/interview/evidence" in url:
            return httpx.Response(
                200, json=[{"id": "cv:a", "label": "Acme: dbt models"}], request=request
            )
        if "/interview/export/anki" in url:
            return httpx.Response(200, text=self.anki, request=request)
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

    def _click(self, app: AppTest, key: str) -> AppTest:
        """Click a button and rerun with the fake API.

        Args:
            app: A finished AppTest.
            key: The button's key.

        Returns:
            The AppTest after the click.
        """
        with mock.patch("httpx.request", side_effect=self._request):
            return app.button(key=key).click().run()

    def test_it_shows_the_question_but_hides_the_model_answer_until_asked(self) -> None:
        app = self._run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("What is a CTE?", _shown(app))
        self.assertNotIn("A named subquery.", _shown(app))
        app.checkbox(key=f"iv-reveal-{_QID}").check()
        self.assertIn("A named subquery.", _shown(self._run(app)))

    def test_rating_posts_the_quality(self) -> None:
        self._click(self._run(), "iv-rate-4")
        self.assertEqual(
            self.sent[-1][0:1] + (self.sent[-1][2],), ("POST", {"quality": 4})
        )
        self.assertTrue(
            self.sent[-1][1].endswith(f"/interview/questions/{_QID}/review")
        )

    def test_saving_puts_the_answer_text(self) -> None:
        app = self._run()
        app.text_area(key=f"iv-answer-{_QID}").input("In my words.")
        app = self._run(app)
        self._click(app, "iv-save")
        method, url, body = self.sent[-1]
        self.assertEqual((method, body), ("PUT", {"answer": "In my words."}))

    def test_drafting_asks_for_confirmation_first(self) -> None:
        app = self._click(self._run(), "iv-draft")
        keys = [b.key for b in app.button]
        self.assertIn("iv-draft-confirm", keys)
        self.assertIn("$0.05", _shown(app))
        self.assertEqual(self.sent, [])
        app = self._click(app, "iv-draft-confirm")
        self.assertTrue(self.sent[-1][1].endswith(f"/interview/questions/{_QID}/draft"))
        self.assertEqual(self.sent[-1][2], {"replace": False})

    def test_cancelling_a_draft_spends_nothing(self) -> None:
        app = self._click(self._click(self._run(), "iv-draft"), "iv-draft-cancel")
        self.assertEqual(self.sent, [])
        self.assertNotIn("iv-draft-confirm", [b.key for b in app.button])

    def test_replacing_a_written_answer_says_so_and_sends_replace(self) -> None:
        self.queue = [_question(personal_answer="mine", answer_status="written")]
        app = self._click(self._run(), "iv-draft")
        self.assertIn("replaces your saved answer", _shown(app))
        self._click(app, "iv-draft-confirm")
        self.assertEqual(self.sent[-1][2], {"replace": True})

    def test_a_pending_sentence_can_be_linked_or_removed(self) -> None:
        self.queue = [
            _question(
                answer_status="needs_review",
                personal_answer="x",
                open_sentences=[
                    {
                        "id": _OPEN,
                        "text": "I led 40.",
                        "issue": "number not in sources",
                        "status": "pending",
                    }
                ],
            )
        ]
        app = self._run()
        self.assertIn("number not in sources", _shown(app))
        self._click(app, f"iv-remove-{_OPEN}")
        self.assertEqual(self.sent[-1][2], {"action": "remove", "evidence_ref": None})
        app = self._run()
        self._click(app, f"iv-link-{_OPEN}")
        self.assertEqual(self.sent[-1][2], {"action": "link", "evidence_ref": "cv:a"})

    def test_the_bank_tab_shows_counts_and_confirms_the_cost_before_generating(
        self,
    ) -> None:
        app = self._run()
        frames = [d.value for d in app.dataframe]
        self.assertTrue(any(list(f.get("Approved", [])) == [12] for f in frames))
        app = self._click(app, "iv-generate")
        self.assertIn("$0.16", _shown(app))
        self.assertEqual(self.sent, [])
        self._click(app, "iv-confirm")
        self.assertTrue(self.sent[-1][1].endswith("/interview/bank/generate"))
        self.assertEqual(self.sent[-1][2], {"category": "data_engineer"})

    def test_a_complete_bank_says_so(self) -> None:
        self.bank = {**_BANK, "work": []}
        self.assertIn("complete", _shown(self._run()))

    def test_gaps_are_listed_biggest_first(self) -> None:
        app = self._run()
        gap_frames = [d.value for d in app.dataframe if "Gap" in d.value.columns]
        self.assertEqual(list(gap_frames[0]["Question"]), ["Low?", "High?"])

    def test_the_export_tab_offers_a_download(self) -> None:
        names = [d.proto.label for d in self._run().get("download_button")]
        self.assertEqual(names, ["Download the Anki file"])
        self.anki = "#separator:tab\n#html:true\n#tags column:3\n"
        self.assertEqual(self._run().get("download_button"), [])

    def test_hostile_text_is_not_rendered_as_markdown(self) -> None:
        self.queue = [_question(question=_HOSTILE)]
        markdown = " ".join(m.value for m in self._run().markdown)
        self.assertNotIn("[x](http://evil.example)", markdown)

    def test_an_api_failure_does_not_crash_and_an_empty_queue_says_so(self) -> None:
        self.queue = []
        self.assertIn("Nothing to drill", _shown(self._run()))
        with mock.patch("httpx.request", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=20).run()
        self.assertEqual((len(app.exception), len(app.error)), (0, 1))


if __name__ == "__main__":
    unittest.main()
