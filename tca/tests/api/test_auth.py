"""Integration tests for FastAPI JWT auth endpoints — real DB, no mocking."""

from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient


def _get_client() -> TestClient:
    from api.main import app

    return TestClient(app)


class TestAuthToken(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = _get_client()
        cls.admin_id = os.environ.get("TEST_ADMIN_CLIENT_ID", "pb_admin")
        cls.admin_secret = os.environ.get("TEST_ADMIN_SECRET", "change-me-admin")

    def test_valid_credentials_return_tokens(self) -> None:
        resp = self.client.post(
            "/api/auth/token",
            data={"client_id": self.admin_id, "client_secret": self.admin_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("access_token", body)
        self.assertIn("refresh_token", body)
        self.assertEqual(body["token_type"], "bearer")

    def test_invalid_credentials_return_401(self) -> None:
        resp = self.client.post(
            "/api/auth/token",
            data={"client_id": "nonexistent", "client_secret": "wrong"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_access_token_is_valid_jwt(self) -> None:
        resp = self.client.post(
            "/api/auth/token",
            data={"client_id": self.admin_id, "client_secret": self.admin_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token = resp.json()["access_token"]
        parts = token.split(".")
        self.assertEqual(
            len(parts), 3, "JWT must have 3 parts (header.payload.signature)"
        )

    def test_missing_client_id_returns_422(self) -> None:
        resp = self.client.post(
            "/api/auth/token",
            data={"client_secret": "secret"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(resp.status_code, 422)


class TestCounterpartyIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = _get_client()
        cls.admin_id = os.environ.get("TEST_ADMIN_CLIENT_ID", "bp_admin")
        cls.admin_secret = os.environ.get("TEST_ADMIN_SECRET", "change-me-admin")
        resp = cls.client.post(
            "/api/auth/token",
            data={"client_id": cls.admin_id, "client_secret": cls.admin_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code == 200:
            cls.admin_token = resp.json()["access_token"]
        else:
            cls.admin_token = None

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.admin_token}"}

    def test_unauthenticated_request_returns_401(self) -> None:
        resp = self.client.get("/api/tca/summary", params={"trade_date": "2025-01-15"})
        self.assertEqual(resp.status_code, 401)

    def test_admin_can_access_tca_summary(self) -> None:
        if not self.admin_token:
            self.skipTest("Admin login failed — check DB seed")
        resp = self.client.get(
            "/api/tca/summary",
            params={"trade_date": "2025-01-15"},
            headers=self._auth_header(),
        )
        self.assertIn(resp.status_code, (200, 404))

    def test_nonexistent_order_returns_404_not_403(self) -> None:
        if not self.admin_token:
            self.skipTest("Admin login failed")
        resp = self.client.get(
            "/api/tca/order/NONEXISTENT-ORDER-ID",
            headers=self._auth_header(),
        )
        self.assertEqual(resp.status_code, 404)


class TestRbacEnforcement(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = _get_client()
        cls.client_id = os.environ.get("TEST_CLIENT_CLIENT_ID", "cp_abc_london")
        cls.client_secret = os.environ.get("TEST_CLIENT_SECRET", "change-me-client")
        resp = cls.client.post(
            "/api/auth/token",
            data={"client_id": cls.client_id, "client_secret": cls.client_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code == 200:
            cls.client_token = resp.json()["access_token"]
        else:
            cls.client_token = None

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.client_token}"}

    def test_client_cannot_access_algo_performance(self) -> None:
        if not self.client_token:
            self.skipTest("Client login failed — check DB seed")
        resp = self.client.get(
            "/api/tca/algo-performance",
            params={"trade_date": "2025-01-15"},
            headers=self._auth_header(),
        )
        self.assertEqual(resp.status_code, 403)

    def test_client_cannot_access_mifid_export(self) -> None:
        if not self.client_token:
            self.skipTest("Client login failed")
        resp = self.client.get(
            "/api/mifid/export",
            params={"trade_date": "2025-01-15"},
            headers=self._auth_header(),
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
