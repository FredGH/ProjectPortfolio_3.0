"""Tests for the /ingest/manual and /sources FastAPI endpoints."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.dependencies import get_http_client, get_llm_adapters  # noqa: E402
from app.main import app  # noqa: E402

from core.llm.types import LLMAdapter, LLMResponse  # noqa: E402


class _FakeAdapter(LLMAdapter):
    """A test double so this test never touches a real LLM provider."""

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Return a fixed structured-extraction response."""
        return LLMResponse(
            text='{"title": "Data Engineer"}',
            provider="fake",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestIngestManualEndpoint(unittest.TestCase):
    """Tests for POST /ingest/manual's request/response wiring."""

    def setUp(self) -> None:
        """Override HTTP and LLM dependencies with fakes for every test."""
        app.dependency_overrides[get_http_client] = lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="unused")
            )
        )
        app.dependency_overrides[get_llm_adapters] = lambda: {"ollama": _FakeAdapter()}
        self.client = TestClient(app)

    def tearDown(self) -> None:
        """Remove the dependency overrides set up in `setUp`."""
        del app.dependency_overrides[get_http_client]
        del app.dependency_overrides[get_llm_adapters]

    def test_ingests_a_pasted_job_and_returns_the_canonical_identity(self) -> None:
        """A valid POST returns 200 with the canonicalised identity fields."""
        response = self.client.post(
            "/ingest/manual",
            json={
                "source_name": "linkedin_manual",
                "job_url": "https://www.linkedin.com/jobs/view/999/?utm_source=x",
                "job_spec": "Full posting text.",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["job_url_canonical"], "https://www.linkedin.com/jobs/view/999"
        )
        self.assertEqual(body["source_job_id"], "999")
        self.assertIn("payload_sha256", body)

    def test_missing_required_field_returns_422(self) -> None:
        """Omitting job_spec (required) is a validation error."""
        response = self.client.post(
            "/ingest/manual",
            json={"source_name": "linkedin_manual", "job_url": "https://example.com"},
        )
        self.assertEqual(response.status_code, 422)

    def test_sources_lists_a_previously_ingested_source_name(self) -> None:
        """A source_name used in a prior ingest appears in GET /sources."""
        unique_source = f"test_source_{uuid.uuid4().hex[:8]}"
        self.client.post(
            "/ingest/manual",
            json={
                "source_name": unique_source,
                "job_url": "https://example.com/job",
                "job_spec": "text",
            },
        )
        response = self.client.get("/sources")
        self.assertEqual(response.status_code, 200)
        self.assertIn(unique_source, response.json())


if __name__ == "__main__":
    unittest.main()
