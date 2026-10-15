"""Unit tests for the artefacts API router (database and services patched)."""

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
from tests.artefacts_fixtures import (  # noqa: E402
    TODAY,
    letter_reply_json,
    make_pack,
    pitch_reply_json,
)
from tests.company_intel_fixtures import make_job  # noqa: E402

from core.artefacts.assemble import (  # noqa: E402
    build_letter,
    build_pitch,
    parse_writer_reply,
)
from core.artefacts.context import LoadedPack  # noqa: E402
from core.artefacts.decisions import DecisionError  # noqa: E402
from core.artefacts.service import NoCvError  # noqa: E402
from core.artefacts.store import StaleDecisionError  # noqa: E402

_NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def _stored(kind: str, status: str = "approved"):  # type: ignore[no-untyped-def]
    """Build a stand-in stored artefact.

    Args:
        kind: `cover_letter` or `pitch`.
        status: The status.

    Returns:
        A namespace shaped like `StoredArtefact`.
    """
    pack = make_pack()
    doc = (
        build_letter(
            pack, parse_writer_reply(letter_reply_json(), "cover_letter"), today=TODAY
        )
        if kind == "cover_letter"
        else build_pitch(pack, parse_writer_reply(pitch_reply_json(), "pitch"))
    )
    opens = (
        [
            SimpleNamespace(
                id=uuid.uuid4(),
                location="evidence:2",
                kind="unsupported",
                text="I did it alone.",
                issue="adds ownership",
                status="pending",
                evidence_ref=None,
            )
        ]
        if status == "needs_review"
        else []
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind=kind,
        status=status,
        target_title="Data Engineer",
        attempts=1,
        created_at=_NOW,
        error_message=None,
        content=doc.model_dump(),
        usage={"cost_usd": 0.05},
        open_sentences=opens,
    )


class TestArtefactsRouter(unittest.TestCase):
    """GET / POST under /artefacts."""

    def setUp(self) -> None:
        """Override collaborators and patch data access."""
        app.dependency_overrides[get_app_db_engine] = lambda: object()
        app.dependency_overrides[get_llm_adapters] = lambda: {}
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.latest = {
            "cover_letter": _stored("cover_letter", "needs_review"),
            "pitch": _stored("pitch"),
        }
        self.mocks = {}
        patches = {
            "job": mock.patch(
                "app.routers.artefacts.load_job_context", return_value=make_job()
            ),
            "pack": mock.patch(
                "app.routers.artefacts.load_pack",
                return_value=LoadedPack(make_pack(), 3, 1),
            ),
            "latest": mock.patch(
                "app.routers.artefacts.store.read_latest",
                side_effect=lambda e, u, k, j: self.latest.get(k),
            ),
        }
        for name, patch in patches.items():
            self.mocks[name] = patch.start()
            self.addCleanup(patch.stop)

    def test_get_returns_both_artefacts_the_evidence_and_the_research_flag(
        self,
    ) -> None:
        body = self.client.get("/artefacts/jobs/zzfixture-intel-job").json()
        self.assertTrue(body["has_saved_research"])
        self.assertEqual(body["cover_letter"]["status"], "needs_review")
        self.assertEqual(
            body["cover_letter"]["open_sentences"][0]["text"], "I did it alone."
        )
        self.assertEqual(body["pitch"]["status"], "approved")
        ids = [e["id"] for e in body["evidence"]]
        self.assertIn("job:posting", ids)
        self.assertIn("site:business_model:0", ids)

    def test_get_for_an_unknown_job_is_404_and_for_no_cv_is_409(self) -> None:
        self.mocks["job"].return_value = None
        self.assertEqual(self.client.get("/artefacts/jobs/nope").status_code, 404)
        self.mocks["job"].return_value = make_job()
        self.mocks["pack"].return_value = None
        self.assertEqual(
            self.client.get("/artefacts/jobs/zzfixture-intel-job").status_code, 409
        )

    def test_get_with_nothing_generated_yet_has_null_artefacts(self) -> None:
        self.latest.clear()
        body = self.client.get("/artefacts/jobs/zzfixture-intel-job").json()
        self.assertIsNone(body["cover_letter"])
        self.assertIsNone(body["pitch"])

    def test_post_cover_letter_generates_for_the_current_user(self) -> None:
        with mock.patch(
            "app.routers.artefacts.generate_artefact",
            return_value=_stored("cover_letter"),
        ) as gen:
            response = self.client.post(
                "/artefacts/jobs/zzfixture-intel-job/cover-letter"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(gen.call_args.args[2], "cover_letter")

    def test_post_pitch_with_no_cv_is_409(self) -> None:
        with mock.patch(
            "app.routers.artefacts.generate_artefact",
            side_effect=NoCvError("add your CV first"),
        ):
            response = self.client.post("/artefacts/jobs/zzfixture-intel-job/pitch")
        self.assertEqual(response.status_code, 409)
        self.assertIn("CV", response.json()["detail"])

    def test_a_decision_is_applied_and_a_stale_or_refused_one_is_409(self) -> None:
        body = {
            "kind": "cover_letter",
            "job_group_id": "zzfixture-intel-job",
            "action": "remove",
        }
        open_id = uuid.uuid4()
        with mock.patch(
            "app.routers.artefacts.decide", return_value=_stored("cover_letter")
        ) as decide:
            ok = self.client.post(
                f"/artefacts/open-sentences/{open_id}/decision", json=body
            )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(decide.call_args.kwargs["action"], "remove")
        for exc in (StaleDecisionError("reload"), DecisionError("bad link")):
            with mock.patch("app.routers.artefacts.decide", side_effect=exc):
                response = self.client.post(
                    f"/artefacts/open-sentences/{open_id}/decision", json=body
                )
            self.assertEqual(response.status_code, 409)

    def test_a_bad_action_is_422(self) -> None:
        body = {"kind": "cover_letter", "job_group_id": "j", "action": "dance"}
        response = self.client.post(
            f"/artefacts/open-sentences/{uuid.uuid4()}/decision", json=body
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
