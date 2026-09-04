"""Live integration test — makes a real call to the Reed API.

Skips cleanly if REED_API_KEY isn't configured.
"""

from __future__ import annotations

import unittest

import httpx

from core.ingestion.reed_connector import ReedConnector, ReedQuery
from core.settings import get_settings


class TestReedConnectorLive(unittest.TestCase):
    """Proves ReedConnector works against the real Reed API."""

    @classmethod
    def setUpClass(cls) -> None:
        """Skip the whole class if a Reed API key isn't configured."""
        settings = get_settings()
        if not settings.reed_api_key:
            raise unittest.SkipTest(
                "REED_API_KEY not set in .env — skipping the live Reed test."
            )
        cls.api_key = settings.reed_api_key

    def test_fetch_returns_real_results_for_a_common_query(self) -> None:
        """A broad, common query returns at least one real, well-formed RawJob."""
        with httpx.Client() as client:
            connector = ReedConnector(http_client=client, api_key=self.api_key)
            try:
                jobs = list(
                    connector.fetch(
                        ReedQuery(keywords="data engineer", max_pages=1),
                        None,
                        run_id="live-test-run",
                    )
                )
            except httpx.HTTPError as exc:
                raise unittest.SkipTest(f"Network unreachable: {exc}") from None
        self.assertGreater(len(jobs), 0)
        first = jobs[0]
        self.assertEqual(first.source_name, "reed")
        self.assertTrue(first.job_url.startswith("http"))
        self.assertTrue(first.source_job_id)
        self.assertEqual(len(first.payload_sha256), 64)


if __name__ == "__main__":
    unittest.main()
