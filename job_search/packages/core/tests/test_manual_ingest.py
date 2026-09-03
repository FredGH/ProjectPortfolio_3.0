# job_search/packages/core/tests/test_manual_ingest.py
from __future__ import annotations

import tempfile
import unittest

import httpx

from core.ingestion.extraction import ExtractedJobFields
from core.ingestion.manual import ingest_manual_job


class TestIngestManualJob(unittest.TestCase):
    """Tests for ingest_manual_job's orchestration and error resilience."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.landing_uri = f"file://{self._tmp_dir.name}"
        self.http_client = httpx.Client(
            transport=httpx.MockTransport(_no_redirect_handler)
        )
        self.bronze_calls: list[dict[str, object]] = []

    def tearDown(self) -> None:
        self._tmp_dir.cleanup()
        self.http_client.close()

    def _fake_load_to_bronze(self, **kwargs: object) -> None:
        self.bronze_calls.append(kwargs)

    def _fake_extract(self, raw_text: str, **kwargs: object) -> ExtractedJobFields:
        return ExtractedJobFields(title="Data Engineer", company="Parsed Co")

    def _raising_extract(self, raw_text: str, **kwargs: object) -> ExtractedJobFields:
        raise RuntimeError("provider unreachable")

    def test_writes_landing_and_loads_bronze_with_the_raw_text_preserved(
        self,
    ) -> None:
        """The landing record and bronze payload both carry raw_text verbatim."""
        result = ingest_manual_job(
            source_name="linkedin_manual",
            job_url="https://www.linkedin.com/jobs/view/12345/?utm_source=li",
            job_spec="Full job posting text here.",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
            extract_fn=self._fake_extract,
        )
        self.assertEqual(
            result.job_url_canonical, "https://www.linkedin.com/jobs/view/12345"
        )
        self.assertEqual(result.source_job_id, "12345")
        self.assertEqual(len(self.bronze_calls), 1)
        self.assertEqual(
            self.bronze_calls[0]["payload"]["raw_text"], "Full job posting text here."
        )

    def test_user_overrides_win_and_are_tagged(self) -> None:
        """A user-supplied company override reaches the merged result."""
        result = ingest_manual_job(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            company="User-Supplied Co",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
            extract_fn=self._fake_extract,
        )
        self.assertEqual(result.extracted.company, "User-Supplied Co")
        self.assertEqual(result.field_source, {"company": "user"})

    def test_extraction_failure_does_not_block_ingestion(self) -> None:
        """A broken LLM provider still lands the job — extraction degrades
        to an all-None result rather than failing the whole request."""
        result = ingest_manual_job(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
            extract_fn=self._raising_extract,
        )
        self.assertIsNone(result.extracted.title)
        self.assertEqual(len(self.bronze_calls), 1)

    def test_reingesting_identical_input_produces_the_same_payload_sha256(
        self,
    ) -> None:
        """Dedup identity is stable across repeated calls with the same input."""
        kwargs = dict(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
            extract_fn=self._fake_extract,
        )
        first = ingest_manual_job(**kwargs)
        second = ingest_manual_job(**kwargs)
        self.assertEqual(first.payload_sha256, second.payload_sha256)

    def test_payload_sha256_is_independent_of_extraction_result(self) -> None:
        """Identity hash is stable even when extraction fails on one call."""
        common_kwargs = dict(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
        )
        first = ingest_manual_job(**common_kwargs, extract_fn=self._fake_extract)
        second = ingest_manual_job(**common_kwargs, extract_fn=self._raising_extract)
        self.assertEqual(first.payload_sha256, second.payload_sha256)


def _no_redirect_handler(request: httpx.Request) -> httpx.Response:
    """Every request resolves to a plain 200 — nothing here redirects.

    ingest_manual_job's http_client is a required parameter, so it is
    always passed through to canonicalise_url, which always attempts one
    redirect-resolution HEAD request. A 200 response means
    _resolve_one_redirect finds `response.is_redirect` False and returns
    the URL unchanged — this handler exists to make that real code path
    exercised-but-inert, not to prove it's never called.
    """
    return httpx.Response(200)


if __name__ == "__main__":
    unittest.main()
