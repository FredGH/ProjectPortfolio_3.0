"""Integration tests for the CV router, against live Postgres."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.db.session import (  # noqa: E402
    build_engine,
    get_current_user_id,
    session_scope,
)
from core.settings import get_settings  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
