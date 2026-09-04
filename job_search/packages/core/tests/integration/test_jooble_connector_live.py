"""Live integration test — makes a real call to the Jooble API.

Skips cleanly if JOOBLE_KEY isn't configured or the network is
unreachable.
"""

from __future__ import annotations

import unittest

import httpx

from core.ingestion.jooble_connector import JoobleConnector, JoobleQuery
from core.settings import get_settings


class TestJoobleConnectorLive(unittest.TestCase):
    """Proves JoobleConnector works against the real Jooble API."""

    @classmethod
    def setUpClass(cls) -> None:
        """Skip the whole class if a Jooble API key isn't configured."""
        settings = get_settings()
        if not settings.jooble_key:
            raise unittest.SkipTest(
                "JOOBLE_KEY not set in .env — skipping the live Jooble test."
            )
        cls.api_key = settings.jooble_key

    def test_fetch_returns_real_results_for_a_common_query(self) -> None:
        """A broad, common query returns at least one real, well-formed RawJob."""
        with httpx.Client() as client:
            connector = JoobleConnector(http_client=client, api_key=self.api_key)
            try:
                jobs = list(
                    connector.fetch(
                        JoobleQuery(
                            keywords="data engineer", location="UK", max_pages=1
                        ),
                        None,
                        run_id="live-test-run",
                    )
                )
            except httpx.HTTPError as exc:
                raise unittest.SkipTest(f"Network unreachable: {exc}") from None
        self.assertGreater(len(jobs), 0)
        first = jobs[0]
        self.assertEqual(first.source_name, "jooble")
        self.assertTrue(first.job_url.startswith("http"))
        self.assertTrue(first.source_job_id)
        self.assertEqual(len(first.payload_sha256), 64)


if __name__ == "__main__":
    unittest.main()
