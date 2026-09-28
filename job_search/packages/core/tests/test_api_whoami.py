"""Tests for the /whoami FastAPI endpoint."""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.main import app  # noqa: E402

from core.db.session import get_current_user_id  # noqa: E402
from core.settings import get_settings  # noqa: E402


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

    def test_dev_user_id_is_used_locally_when_no_real_identity_is_set(self) -> None:
        """PLAN.md Step 22a's local-dev override: an env var stands in for
        the not-yet-built IAP identity, so per-user pages work locally
        without waiting on Step 22's cloud auth."""
        dev_id = uuid.uuid4()
        os.environ["DEV_USER_ID"] = str(dev_id)
        get_settings.cache_clear()
        try:
            client = TestClient(app)
            response = client.get("/whoami")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"user_id": str(dev_id)})
        finally:
            del os.environ["DEV_USER_ID"]
            get_settings.cache_clear()

    def test_dev_user_id_is_never_used_outside_the_local_environment(self) -> None:
        """Hard-disabled when ENV=gcp, even if DEV_USER_ID is set — the
        override must never leak into a real deployment."""
        os.environ["DEV_USER_ID"] = str(uuid.uuid4())
        os.environ["ENV"] = "gcp"
        get_settings.cache_clear()
        try:
            client = TestClient(app)
            response = client.get("/whoami")
            self.assertEqual(response.status_code, 501)
        finally:
            del os.environ["DEV_USER_ID"]
            os.environ["ENV"] = "local"
            get_settings.cache_clear()

    def test_a_real_identity_wins_over_the_dev_override(self) -> None:
        """Once real middleware sets request.state.user_id (what Step 22a's
        eventual IAP middleware will do), that value is used even if
        DEV_USER_ID is also set — exercised through a real ASGI request
        against a throwaway app with a stand-in middleware, not a
        dependency override (which would bypass get_current_user_id's own
        priority logic entirely rather than testing it)."""
        real_id = uuid.uuid4()
        os.environ["DEV_USER_ID"] = str(uuid.uuid4())
        get_settings.cache_clear()

        probe_app = FastAPI()

        @probe_app.middleware("http")
        async def _set_real_identity(request, call_next):
            request.state.user_id = real_id
            return await call_next(request)

        @probe_app.get("/probe-whoami")
        def _probe(user_id: uuid.UUID = Depends(get_current_user_id)) -> dict[str, str]:
            return {"user_id": str(user_id)}

        try:
            response = TestClient(probe_app).get("/probe-whoami")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"user_id": str(real_id)})
        finally:
            del os.environ["DEV_USER_ID"]
            get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
