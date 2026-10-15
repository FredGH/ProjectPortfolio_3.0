"""Unit tests for the interview API router (database and services patched)."""

from __future__ import annotations

import sys
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.dependencies import get_app_db_engine, get_llm_adapters  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.artefacts.service import NoCvError  # noqa: E402
from core.interview import progress as progress_mod  # noqa: E402
from core.interview.config import WorkItem  # noqa: E402
from core.interview.personal import AnswerDraft  # noqa: E402
from core.interview.service import TopicResult  # noqa: E402
from core.interview.sm2 import Sm2State  # noqa: E402
from core.interview.store import StoredQuestion  # noqa: E402

_QID = uuid.uuid4()
_NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
_P = "app.routers.interview"


def _q(status: str = "approved") -> StoredQuestion:
    """Build a stored question.

    Args:
        status: Its status.

    Returns:
        A `StoredQuestion`.
    """
    return StoredQuestion(
        _QID,
        "data_engineer",
        "SQL",
        "technical",
        3,
        "What is a CTE?",
        "A named subquery.",
        ["sql"],
        "generated",
        status,
        None,
        _NOW,
    )


def _mine(answer: str | None = None, status: str = "none") -> progress_mod.Progress:
    """Build a progress row.

    Args:
        answer: The personal answer.
        status: The answer status.

    Returns:
        A `Progress`.
    """
    return progress_mod.Progress(
        _QID, answer, status, Sm2State(2.5, 0, 0), None, 0.7, 1
    )


class TestInterviewRouter(unittest.TestCase):
    """GET / POST / PUT under /interview."""

    def setUp(self) -> None:
        """Override collaborators and patch data access."""
        app.dependency_overrides[get_app_db_engine] = lambda: object()
        app.dependency_overrides[get_llm_adapters] = lambda: {}
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        for target, kwargs in {
            f"{_P}.store.get_question": {"return_value": _q()},
            f"{_P}.store.list_questions": {"return_value": [_q()]},
            f"{_P}.store.bank_status": {
                "return_value": [
                    {
                        "qa_category": "data_engineer",
                        "topic": "SQL",
                        "approved": 12,
                        "needs_review": 1,
                    }
                ]
            },
            f"{_P}.progress.get_progress": {"return_value": _mine("mine", "written")},
            f"{_P}.progress.list_progress": {"return_value": {_QID: _mine()}},
            f"{_P}.progress.list_open": {"return_value": []},
            f"{_P}.practice.refresh_gaps": {"return_value": 1},
        }.items():
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_bank_reports_status_work_and_cost(self) -> None:
        work = [WorkItem("data_engineer", "SQL", "technical", 18)]
        with mock.patch(f"{_P}.service.pending_work", return_value=(work, ["zzz"])):
            body = self.client.get("/interview/bank").json()
        self.assertEqual(body["rows"][0]["approved"], 12)
        self.assertEqual(body["work"][0]["missing"], 18)
        self.assertEqual(body["unconfigured"], ["zzz"])
        self.assertGreater(body["estimated_cost_usd"], 0)

    def test_generate_returns_one_result_per_topic(self) -> None:
        result = TopicResult("data_engineer", "SQL", "technical", approved=18, rounds=2)
        with (
            mock.patch(f"{_P}.service.pending_work", return_value=([object()], [])),
            mock.patch(f"{_P}.service.generate_work", return_value=[result]),
        ):
            body = self.client.post(
                "/interview/bank/generate",
                json={"category": "data_engineer", "confirm": True},
            ).json()
        self.assertEqual(body[0]["approved"], 18)

    def test_generate_needs_confirmation_and_refuses_a_concurrent_run(self) -> None:
        unconfirmed = self.client.post(
            "/interview/bank/generate", json={"category": "c"}
        )
        self.assertEqual(unconfirmed.status_code, 422)
        from app.routers import interview as router_module

        router_module._GENERATING.add("c")
        self.addCleanup(router_module._GENERATING.discard, "c")
        with mock.patch(f"{_P}.service.generate_work") as generate:
            busy = self.client.post(
                "/interview/bank/generate", json={"category": "c", "confirm": True}
            )
        self.assertEqual(busy.status_code, 409)
        generate.assert_not_called()

    def test_the_queue_carries_the_users_state(self) -> None:
        with mock.patch(f"{_P}.practice.build_queue", return_value=[_q()]):
            body = self.client.get("/interview/queue?category=data_engineer").json()
        self.assertEqual(body[0]["question"], "What is a CTE?")
        self.assertEqual(body[0]["personal_answer"], "mine")

    def test_the_queue_still_works_for_a_user_without_a_cv(self) -> None:
        with (
            mock.patch(f"{_P}.practice.refresh_gaps", side_effect=NoCvError("x")),
            mock.patch(f"{_P}.practice.build_queue", return_value=[_q()]),
        ):
            self.assertEqual(self.client.get("/interview/queue").status_code, 200)

    def test_gaps_need_a_cv(self) -> None:
        with mock.patch(
            f"{_P}.practice.refresh_gaps", side_effect=NoCvError("add your CV first")
        ):
            response = self.client.get("/interview/gaps")
        self.assertEqual(response.status_code, 409)

    def test_review_validates_the_rating_and_the_question(self) -> None:
        with mock.patch(f"{_P}.practice.review_question") as review:
            ok = self.client.post(
                f"/interview/questions/{_QID}/review", json={"quality": 4}
            )
            bad = self.client.post(
                f"/interview/questions/{_QID}/review", json={"quality": 9}
            )
        self.assertEqual((ok.status_code, bad.status_code), (200, 422))
        review.assert_called_once()
        with mock.patch(f"{_P}.store.get_question", return_value=_q("needs_review")):
            missing = self.client.post(
                f"/interview/questions/{_QID}/review", json={"quality": 4}
            )
        self.assertEqual(missing.status_code, 404)

    def test_saving_an_answer(self) -> None:
        with mock.patch(f"{_P}.progress.save_written_answer") as save:
            response = self.client.put(
                f"/interview/questions/{_QID}/answer", json={"answer": "x"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(save.call_args.args[3], "x")

    def test_drafting_maps_failures_to_409(self) -> None:
        for outcome in (
            NoCvError("add your CV first"),
            __import__("core.interview.practice", fromlist=["x"]).AnswerExistsError(
                "confirm"
            ),
        ):
            with mock.patch(
                f"{_P}.practice.draft_personal_answer", side_effect=outcome
            ):
                response = self.client.post(
                    f"/interview/questions/{_QID}/draft", json={}
                )
            self.assertEqual(response.status_code, 409)
        failed = AnswerDraft(status="failed", error="unusable")
        with mock.patch(f"{_P}.practice.draft_personal_answer", return_value=failed):
            response = self.client.post(f"/interview/questions/{_QID}/draft", json={})
        self.assertEqual(
            (response.status_code, response.json()["detail"]), (409, "unusable")
        )
        ok = AnswerDraft(status="drafted", text="x")
        with mock.patch(f"{_P}.practice.draft_personal_answer", return_value=ok):
            self.assertEqual(
                self.client.post(
                    f"/interview/questions/{_QID}/draft", json={}
                ).status_code,
                200,
            )

    def test_a_stale_or_refused_decision_is_409_and_a_good_one_200(self) -> None:
        open_id = uuid.uuid4()
        opened = SimpleNamespace(question_id=_QID)
        body = {"action": "remove"}
        with (
            mock.patch(f"{_P}.progress.read_open", return_value=opened),
            mock.patch(f"{_P}.practice.decide_sentence", return_value="drafted"),
        ):
            ok = self.client.post(
                f"/interview/open-sentences/{open_id}/decision", json=body
            )
        self.assertEqual(ok.status_code, 200)
        for exc in (progress_mod.StaleDecisionError("reload"), ValueError("bad link")):
            with (
                mock.patch(f"{_P}.progress.read_open", return_value=opened),
                mock.patch(f"{_P}.practice.decide_sentence", side_effect=exc),
            ):
                response = self.client.post(
                    f"/interview/open-sentences/{open_id}/decision", json=body
                )
            self.assertEqual(response.status_code, 409)

    def test_the_anki_export_is_plain_text(self) -> None:
        response = self.client.get("/interview/export/anki")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertIn("What is a CTE?", response.text)

    def test_evidence_needs_a_cv(self) -> None:
        with mock.patch(f"{_P}.read_truth_base", return_value=None):
            self.assertEqual(self.client.get("/interview/evidence").status_code, 409)


if __name__ == "__main__":
    unittest.main()
