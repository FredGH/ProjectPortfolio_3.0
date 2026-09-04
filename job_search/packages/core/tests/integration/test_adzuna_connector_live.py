"""Live integration test — makes a real call to the Adzuna API.

Skips cleanly if ADZUNA_APP_ID/ADZUNA_APP_KEY aren't configured.
"""

from __future__ import annotations

import unittest

import httpx

from core.ingestion.adzuna_connector import AdzunaConnector, AdzunaQuery
from core.settings import get_settings


class TestAdzunaConnectorLive(unittest.TestCase):
    """Proves AdzunaConnector works against the real Adzuna API."""

    @classmethod
    def setUpClass(cls) -> None:
        """Skip the whole class if Adzuna credentials aren't configured."""
        settings = get_settings()
        if not settings.adzuna_app_id or not settings.adzuna_app_key:
            raise unittest.SkipTest(
                "ADZUNA_APP_ID/ADZUNA_APP_KEY not set in .env — skipping "
                "the live Adzuna test."
            )
        cls.app_id = settings.adzuna_app_id
        cls.app_key = settings.adzuna_app_key

    def test_fetch_returns_real_results_for_a_common_query(self) -> None:
        """A broad, common query returns at least one real, well-formed RawJob."""
        with httpx.Client() as client:
            connector = AdzunaConnector(
                http_client=client, app_id=self.app_id, app_key=self.app_key
            )
            try:
                jobs = list(
                    connector.fetch(
                        AdzunaQuery(
                            keywords="data engineer", country="gb", max_pages=1
                        ),
                        None,
                        run_id="live-test-run",
                    )
                )
            except httpx.HTTPError as exc:
                raise unittest.SkipTest(f"Network unreachable: {exc}") from None
        self.assertGreater(len(jobs), 0)
        first = jobs[0]
        self.assertEqual(first.source_name, "adzuna")
        self.assertTrue(first.job_url.startswith("http"))
        self.assertTrue(first.source_job_id)
        self.assertEqual(len(first.payload_sha256), 64)


if __name__ == "__main__":
    unittest.main()
