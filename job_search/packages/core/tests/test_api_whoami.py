"""Tests for the /whoami FastAPI endpoint."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.main import app  # noqa: E402
from core.db.session import get_current_user_id  # noqa: E402


class TestWhoamiEndpoint(unittest.TestCase):
    """Whoami endpoint returns the authenticated user's ID."""

    def test_returns_501_with_no_identity_middleware(self) -> None:
        """Endpoint returns 501 when request.state.user_id is not set."""
        client = TestClient(app)
        response = client.get("/whoami")
        self.assertEqual(response.status_code, 501)

    def test_returns_the_user_id_once_request_state_is_set(self) -> None:
        """Endpoint returns user_id once dependency is overridden."""
        # Simulates what Step 22a's IAP identity middleware will eventually
        # set on request.state — overriding the dependency directly (rather
        # than registering real ASGI middleware) is the standard FastAPI
        # test pattern and avoids leaking state onto the shared `app`
        # singleton between tests.
        user_id = uuid.uuid4()
        app.dependency_overrides[get_current_user_id] = lambda: user_id

        try:
            client = TestClient(app)
            response = client.get("/whoami")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"user_id": str(user_id)})
        finally:
            del app.dependency_overrides[get_current_user_id]


if __name__ == "__main__":
    unittest.main()
