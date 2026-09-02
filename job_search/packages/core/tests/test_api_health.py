"""Health endpoint test."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.main import app  # noqa: E402


class TestHealthEndpoint(unittest.TestCase):
    """Test FastAPI health endpoint."""

    def test_health_returns_ok(self) -> None:
        """Health endpoint returns 200 with ok status."""
        client = TestClient(app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
