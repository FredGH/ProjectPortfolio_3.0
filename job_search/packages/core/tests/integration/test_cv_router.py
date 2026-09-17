"""Integration tests for the CV router, against live Postgres."""

from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine, get_llm_adapters  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.db.session import (  # noqa: E402
    build_engine,
    get_current_user_id,
    session_scope,
)
from core.llm.types import LLMResponse  # noqa: E402
from core.settings import get_settings  # noqa: E402


class _UnparseableAdapter:
    """A fake `LLMAdapter` whose `complete` always returns non-JSON text.

    Mirrors `test_cv_extract.py`'s own `_FakeAdapter` pattern — this
    codebase's tests implement `core.llm.types.LLMAdapter` directly
    rather than mocking it. Used to drive `extract_truth_base`'s
    documented `ValueError` path through `POST /cv/extract` without
    needing a real LLM call.
    """

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Return a fixed, unparseable response regardless of input.

        Args:
            model: The provider-specific model identifier (unused).
            prompt: The prompt text (unused).
            temperature: Sampling temperature (unused).
            seed: A fixed seed (unused).

        Returns:
            An `LLMResponse` whose `text` is not valid JSON.
        """
        return LLMResponse(
            text="not json",
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class _WorkingAdapter:
    """A fake `LLMAdapter` that returns a minimal, valid CV extraction
    response — the success-path counterpart to `_UnparseableAdapter`.
    """

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Return a fixed, valid extraction payload regardless of input.

        Args:
            model: The provider-specific model identifier (unused).
            prompt: The prompt text (unused).
            temperature: Sampling temperature (unused by the fake).
            seed: A fixed seed (unused by the fake).

        Returns:
            An `LLMResponse` whose `text` is valid `_RawCVTruthBase` JSON.
        """
        return LLMResponse(
            text=json.dumps({"identity": "Jane Doe", "headline": "Engineer"}),
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


def _live_migration_engine():
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(
            f"Postgres not reachable ({exc}); "
            "run `docker compose up -d postgres` first."
        ) from None
    return engine


class TestCvRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration_engine = _live_migration_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        with session_scope(self.migration_engine) as conn:
            self.user_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'Test User')"
                ),
                {"id": self.user_id, "email": f"{self.user_id}@example.com"},
            )
        app.dependency_overrides[get_current_user_id] = lambda: self.user_id
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_get_truth_base_returns_null_when_none_exists(self) -> None:
        response = self.client.get("/cv/truth-base")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json())

    def test_put_then_get_round_trips_the_truth_base(self) -> None:
        payload = {
            "extracted_markdown": "# Jane Doe",
            "truth_base": {
                "identity": "Jane Doe",
                "headline": "Engineer",
                "experience": [],
            },
        }
        put_response = self.client.put("/cv/truth-base", json=payload)
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(put_response.json()["version"], 1)

        get_response = self.client.get("/cv/truth-base")
        self.assertEqual(get_response.status_code, 200)
        body = get_response.json()
        self.assertEqual(body["truth_base"]["identity"], "Jane Doe")
        self.assertEqual(body["version"], 1)

    def test_extract_job_succeeds_and_saves_the_truth_base(self) -> None:
        app.dependency_overrides[get_llm_adapters] = lambda: {
            "ollama": _WorkingAdapter()
        }
        html = b"<html><body><h1>Jane Doe</h1><p>Engineer.</p></body></html>"

        accept_response = self.client.post(
            "/cv/extract", files={"file": ("cv.html", html, "text/html")}
        )
        self.assertEqual(accept_response.status_code, 202)
        job_id = accept_response.json()["job_id"]

        status_response = self.client.get(f"/cv/extract/jobs/{job_id}")
        self.assertEqual(status_response.status_code, 200)
        body = status_response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["version"], 1)
        self.assertEqual(
            [step["name"] for step in body["steps"]],
            ["parsing_document", "extracting_fields", "saving"],
        )
        for step in body["steps"]:
            self.assertEqual(step["status"], "done")
            self.assertIsNotNone(step["duration_seconds"])

        get_response = self.client.get("/cv/truth-base")
        get_body = get_response.json()
        self.assertEqual(get_body["truth_base"]["identity"], "Jane Doe")
        self.assertIsNotNone(get_body["extraction_seconds"])
        self.assertGreaterEqual(get_body["extraction_seconds"], 0.0)

    def test_extract_job_fails_at_extracting_fields_for_unparseable_llm_response(
        self,
    ) -> None:
        app.dependency_overrides[get_llm_adapters] = lambda: {
            "ollama": _UnparseableAdapter()
        }
        html = b"<html><body><h1>Jane Doe</h1><p>Engineer.</p></body></html>"

        accept_response = self.client.post(
            "/cv/extract", files={"file": ("cv.html", html, "text/html")}
        )
        self.assertEqual(accept_response.status_code, 202)
        job_id = accept_response.json()["job_id"]

        status_response = self.client.get(f"/cv/extract/jobs/{job_id}")
        body = status_response.json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["failed_step"], "extracting_fields")
        self.assertIn("could not extract", body["error"])

        # Nothing should have been written on a failed extraction.
        get_response = self.client.get("/cv/truth-base")
        self.assertIsNone(get_response.json())

    def test_extract_job_status_returns_404_for_unknown_job_id(self) -> None:
        response = self.client.get(f"/cv/extract/jobs/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_put_persists_and_returns_the_given_label(self) -> None:
        payload = {
            "extracted_markdown": "# Jane Doe",
            "truth_base": {"identity": "Jane Doe", "headline": "Engineer"},
            "label": "Before I added the AI section",
        }
        self.client.put("/cv/truth-base", json=payload)

        get_response = self.client.get("/cv/truth-base")
        self.assertEqual(get_response.json()["label"], "Before I added the AI section")

    def test_list_versions_returns_every_version_newest_first(self) -> None:
        self.client.put(
            "/cv/truth-base",
            json={
                "extracted_markdown": "# v1",
                "truth_base": {"identity": "Jane Doe", "headline": "Engineer"},
                "label": "First draft",
            },
        )
        self.client.put(
            "/cv/truth-base",
            json={
                "extracted_markdown": "# v2",
                "truth_base": {"identity": "Jane A. Doe", "headline": "Engineer"},
            },
        )

        response = self.client.get("/cv/truth-base/versions")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([entry["version"] for entry in body], [2, 1])
        self.assertIsNone(body[0]["label"])
        self.assertEqual(body[1]["label"], "First draft")

    def test_restore_creates_a_new_version_with_the_old_content(self) -> None:
        self.client.put(
            "/cv/truth-base",
            json={
                "extracted_markdown": "# v1",
                "truth_base": {"identity": "Jane Doe", "headline": "Engineer"},
                "label": "First draft",
            },
        )
        self.client.put(
            "/cv/truth-base",
            json={
                "extracted_markdown": "# v2",
                "truth_base": {"identity": "Jane A. Doe", "headline": "Engineer"},
            },
        )

        restore_response = self.client.post("/cv/truth-base/versions/1/restore")

        self.assertEqual(restore_response.status_code, 200)
        self.assertEqual(restore_response.json()["version"], 3)
        get_response = self.client.get("/cv/truth-base")
        body = get_response.json()
        self.assertEqual(body["version"], 3)
        self.assertEqual(body["truth_base"]["identity"], "Jane Doe")
        self.assertEqual(body["label"], "First draft")

        history = self.client.get("/cv/truth-base/versions").json()
        self.assertEqual([entry["version"] for entry in history], [3, 2, 1])

    def test_restore_returns_404_for_an_unknown_version(self) -> None:
        self.client.put(
            "/cv/truth-base",
            json={
                "extracted_markdown": "# v1",
                "truth_base": {"identity": "Jane Doe", "headline": "Engineer"},
            },
        )
        response = self.client.post("/cv/truth-base/versions/99/restore")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
