from __future__ import annotations

import unittest

import httpx

from core.ingestion.extraction import ExtractedJobFields
from core.ingestion.manual_connector import ManualConnector, ManualJobQuery


def _no_redirect_handler(request: httpx.Request) -> httpx.Response:
    """Every request resolves to a plain 200 — nothing here redirects.

    fetch()'s http_client is always passed to canonicalise_url, which
    always attempts one redirect-resolution HEAD request — a 200 means
    "nothing to follow," matching the pattern from Step 2's test suite.
    """
    return httpx.Response(200)


class TestManualConnector(unittest.TestCase):
    """Tests for ManualConnector.fetch()'s single-RawJob output."""

    def setUp(self) -> None:
        self.http_client = httpx.Client(
            transport=httpx.MockTransport(_no_redirect_handler)
        )

    def tearDown(self) -> None:
        self.http_client.close()

    def _fake_extract(self, raw_text: str, **kwargs: object) -> ExtractedJobFields:
        return ExtractedJobFields(title="Data Engineer", company="Parsed Co")

    def _raising_extract(self, raw_text: str, **kwargs: object) -> ExtractedJobFields:
        raise RuntimeError("provider unreachable")

    def test_yields_exactly_one_raw_job_with_canonicalised_url_and_stamped_run_id(
        self,
    ) -> None:
        """fetch() yields one RawJob; job_url_canonical and run_id are set correctly."""
        connector = ManualConnector(
            http_client=self.http_client, llm_adapters={}, extract_fn=self._fake_extract
        )
        query = ManualJobQuery(
            source_name="linkedin_manual",
            job_url="https://www.linkedin.com/jobs/view/12345/?utm_source=li",
            job_spec="Full job posting text here.",
        )
        jobs = list(connector.fetch(query, None, run_id="01J000000000000000000000"))
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(
            job.job_url_canonical, "https://www.linkedin.com/jobs/view/12345"
        )
        self.assertEqual(job.source_job_id, "12345")
        self.assertEqual(job.run_id, "01J000000000000000000000")
        self.assertEqual(job.payload["raw_text"], "Full job posting text here.")

    def test_payload_includes_parsed_fields_and_field_source(self) -> None:
        """The enriched payload carries both the extraction and override tags."""
        connector = ManualConnector(
            http_client=self.http_client, llm_adapters={}, extract_fn=self._fake_extract
        )
        query = ManualJobQuery(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            company="User-Supplied Co",
        )
        job = next(
            iter(connector.fetch(query, None, run_id="01J000000000000000000000"))
        )
        self.assertEqual(job.payload["parsed"]["company"], "User-Supplied Co")
        self.assertEqual(job.payload["field_source"], {"company": "user"})

    def test_extraction_failure_still_yields_one_job_with_null_parsed_fields(
        self,
    ) -> None:
        """A broken LLM provider still lands the job — extraction is best-effort."""
        connector = ManualConnector(
            http_client=self.http_client,
            llm_adapters={},
            extract_fn=self._raising_extract,
        )
        query = ManualJobQuery(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
        )
        jobs = list(connector.fetch(query, None, run_id="01J000000000000000000000"))
        self.assertEqual(len(jobs), 1)
        self.assertIsNone(jobs[0].payload["parsed"]["title"])

    def test_payload_sha256_is_independent_of_extraction_result(self) -> None:
        """Dedup identity is stable even when extraction fails on one call."""
        query = ManualJobQuery(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
        )
        ok_connector = ManualConnector(
            http_client=self.http_client, llm_adapters={}, extract_fn=self._fake_extract
        )
        failing_connector = ManualConnector(
            http_client=self.http_client,
            llm_adapters={},
            extract_fn=self._raising_extract,
        )
        job_ok = next(
            iter(ok_connector.fetch(query, None, run_id="01J000000000000000000000"))
        )
        job_failed = next(
            iter(
                failing_connector.fetch(query, None, run_id="01J000000000000000000000")
            )
        )
        self.assertEqual(job_ok.payload_sha256, job_failed.payload_sha256)

    def test_reingesting_identical_input_produces_the_same_payload_sha256(self) -> None:
        """Dedup identity is stable across repeated calls with the same input."""
        connector = ManualConnector(
            http_client=self.http_client, llm_adapters={}, extract_fn=self._fake_extract
        )
        query = ManualJobQuery(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
        )
        first = next(
            iter(connector.fetch(query, None, run_id="01J000000000000000000000"))
        )
        second = next(
            iter(connector.fetch(query, None, run_id="01J000000000000000000001"))
        )
        self.assertEqual(first.payload_sha256, second.payload_sha256)


if __name__ == "__main__":
    unittest.main()
