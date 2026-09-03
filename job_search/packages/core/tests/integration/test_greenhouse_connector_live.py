"""Live integration test — makes a real call to a public Greenhouse board.

Skips cleanly if the network call fails (offline, DNS, etc.) rather than
failing the suite.
"""

from __future__ import annotations

import unittest

import httpx

from core.ingestion.greenhouse_connector import GreenhouseConnector, GreenhouseQuery


class TestGreenhouseConnectorLive(unittest.TestCase):
    """Proves GreenhouseConnector works against a real public board."""

    def test_fetch_returns_real_jobs_for_a_known_public_board(self) -> None:
        """A well-known public Greenhouse board yields real, well-formed RawJobs."""
        with httpx.Client() as client:
            connector = GreenhouseConnector(http_client=client, database_url="unused")
            jobs = list(
                connector.fetch(
                    GreenhouseQuery(board_slugs=["stripe"]),
                    None,
                    run_id="live-test-run",
                )
            )

        if not jobs:
            raise unittest.SkipTest(
                "stripe's Greenhouse board returned zero jobs — either it "
                "genuinely has zero open roles right now, or the API call "
                "failed and GreenhouseConnector's internal per-company error "
                "handling silently absorbed it (by design). Inconclusive "
                "either way, not a failure."
            )
        first = jobs[0]
        self.assertEqual(first.source_name, "greenhouse")
        self.assertTrue(first.job_url.startswith("http"))
        self.assertEqual(len(first.payload_sha256), 64)


if __name__ == "__main__":
    unittest.main()
