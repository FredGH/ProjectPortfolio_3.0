"""Integration tests for TCA API endpoints — real DB, real JWT."""
from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient


def _login(client: TestClient, client_id: str, secret: str) -> str | None:
    resp = client.post(
        "/api/auth/token",
        data={"client_id": client_id, "client_secret": secret},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code == 200:
        return resp.json()["access_token"]
    return None


class TestTcaSummaryEndpoint(unittest.TestCase):
    _TRADE_DATE = "2025-01-15"

    @classmethod
    def setUpClass(cls) -> None:
        from api.main import app
        cls.client = TestClient(app)
        cls.token = _login(
            cls.client,
            os.environ.get("TEST_ADMIN_CLIENT_ID", "pb_admin"),
            os.environ.get("TEST_ADMIN_SECRET", "change-me-admin"),
        )

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def test_summary_returns_list(self) -> None:
        if not self.token:
            self.skipTest("Login failed")
        resp = self.client.get(
            "/api/tca/summary",
            params={"trade_date": self._TRADE_DATE},
            headers=self._h(),
        )
        self.assertIn(resp.status_code, (200, 404))
        if resp.status_code == 200:
            self.assertIsInstance(resp.json(), list)

    def test_summary_accepts_counterparty_filter(self) -> None:
        if not self.token:
            self.skipTest("Login failed")
        resp = self.client.get(
            "/api/tca/summary",
            params={"trade_date": self._TRADE_DATE, "counterparty_id": "CP_LONDON_001"},
            headers=self._h(),
        )
        self.assertIn(resp.status_code, (200, 404))

    def test_summary_accepts_instrument_class_filter(self) -> None:
        if not self.token:
            self.skipTest("Login failed")
        resp = self.client.get(
            "/api/tca/summary",
            params={"trade_date": self._TRADE_DATE, "instrument_class": "equity"},
            headers=self._h(),
        )
        self.assertIn(resp.status_code, (200, 404))
        if resp.status_code == 200:
            data = resp.json()
            for row in data:
                self.assertEqual(row["instrument_class"], "equity")

    def test_missing_trade_date_returns_422(self) -> None:
        if not self.token:
            self.skipTest("Login failed")
        resp = self.client.get("/api/tca/summary", headers=self._h())
        self.assertEqual(resp.status_code, 422)


class TestAlgoPerformanceEndpoint(unittest.TestCase):
    _TRADE_DATE = "2025-01-15"

    @classmethod
    def setUpClass(cls) -> None:
        from api.main import app
        cls.client = TestClient(app)
        cls.admin_token = _login(
            cls.client,
            os.environ.get("TEST_ADMIN_CLIENT_ID", "pb_admin"),
            os.environ.get("TEST_ADMIN_SECRET", "change-me-admin"),
        )

    def test_algo_performance_returns_list_for_admin(self) -> None:
        if not self.admin_token:
            self.skipTest("Login failed")
        resp = self.client.get(
            "/api/tca/algo-performance",
            params={"trade_date": self._TRADE_DATE},
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertIn(resp.status_code, (200, 404))
        if resp.status_code == 200:
            self.assertIsInstance(resp.json(), list)


class TestOrdersEndpoint(unittest.TestCase):
    _TRADE_DATE = "2025-01-15"

    @classmethod
    def setUpClass(cls) -> None:
        from api.main import app
        cls.client = TestClient(app)
        cls.token = _login(
            cls.client,
            os.environ.get("TEST_ADMIN_CLIENT_ID", "pb_admin"),
            os.environ.get("TEST_ADMIN_SECRET", "change-me-admin"),
        )

    def test_orders_returns_list(self) -> None:
        if not self.token:
            self.skipTest("Login failed")
        resp = self.client.get(
            "/api/orders",
            params={"trade_date": self._TRADE_DATE},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertIn(resp.status_code, (200, 404))

    def test_orders_response_has_required_fields(self) -> None:
        if not self.token:
            self.skipTest("Login failed")
        resp = self.client.get(
            "/api/orders",
            params={"trade_date": self._TRADE_DATE},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        if resp.status_code == 200 and resp.json():
            row = resp.json()[0]
            for field in ("order_id", "instrument_id", "side", "total_quantity"):
                self.assertIn(field, row, f"Missing field: {field}")


if __name__ == "__main__":
    unittest.main()
