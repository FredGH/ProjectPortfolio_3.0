"""Tests for AdzunaConnector."""

from __future__ import annotations

import datetime
import unittest

import httpx

from core.ingestion.adzuna_connector import AdzunaConnector, AdzunaQuery


def _result(job_id: int, title: str) -> dict[str, object]:
    return {
        "id": job_id,
        "title": title,
        "redirect_url": f"https://www.adzuna.co.uk/jobs/details/{job_id}",
        "company": {"display_name": "Acme"},
        "location": {"display_name": "London"},
    }


class TestAdzunaConnector(unittest.TestCase):
    """Unit tests — every HTTP call is injected, no real network."""

    def setUp(self) -> None:
        """Provide a real (but unused) httpx.Client for the connector."""
        self.http_client = httpx.Client()
        self.calls: list[dict[str, object]] = []

    def tearDown(self) -> None:
        """Close the httpx.Client."""
        self.http_client.close()

    def _make_connector(self, pages: list[list[dict[str, object]]]) -> AdzunaConnector:
        """Build a connector whose fetch_page_fn returns `pages` in order,
        recording every call's kwargs into self.calls."""

        def fake_fetch_page(
            http_client,
            *,
            app_id,
            app_key,
            country,
            page,
            results_per_page,
            what,
            max_days_old,
        ):
            self.calls.append(
                {
                    "country": country,
                    "page": page,
                    "what": what,
                    "max_days_old": max_days_old,
                }
            )
            index = page - 1
            results = pages[index] if index < len(pages) else []
            return {"results": results, "count": sum(len(p) for p in pages)}

        return AdzunaConnector(
            http_client=self.http_client,
            app_id="test-id",
            app_key="test-key",
            fetch_page_fn=fake_fetch_page,
        )

    def test_fetch_maps_one_page_of_results_to_raw_jobs(self) -> None:
        """A single short page yields one RawJob per result, then stops."""
        connector = self._make_connector([[_result(1, "Data Engineer")]])
        jobs = list(
            connector.fetch(
                AdzunaQuery(keywords="data engineer", country="gb"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "adzuna")
        self.assertEqual(jobs[0].source_job_id, "1")
        self.assertEqual(jobs[0].run_id, "run-1")
        self.assertEqual(len(self.calls), 1)

    def test_fetch_paginates_until_a_short_page(self) -> None:
        """A full page triggers a second fetch; a short page stops pagination."""
        full_page = [_result(i, f"Job {i}") for i in range(50)]
        short_page = [_result(100, "Last Job")]
        connector = self._make_connector([full_page, short_page])
        jobs = list(
            connector.fetch(
                AdzunaQuery(keywords="data engineer", country="gb"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(len(jobs), 51)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["page"], 1)
        self.assertEqual(self.calls[1]["page"], 2)

    def test_fetch_stops_at_max_pages_even_with_full_pages(self) -> None:
        """max_pages caps the number of API calls, even if every page is full."""
        full_page = [_result(i, f"Job {i}") for i in range(50)]
        connector = self._make_connector([full_page] * 10)
        jobs = list(
            connector.fetch(
                AdzunaQuery(keywords="x", country="gb", max_pages=2),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(jobs), 100)

    def test_fetch_stops_immediately_on_empty_first_page(self) -> None:
        """Zero results on page 1 yields nothing and makes exactly one call."""
        connector = self._make_connector([[]])
        jobs = list(
            connector.fetch(
                AdzunaQuery(keywords="nonexistent role xyz", country="gb"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(jobs, [])
        self.assertEqual(len(self.calls), 1)

    def test_since_is_translated_into_max_days_old(self) -> None:
        """A `since` datetime becomes a positive integer max_days_old."""
        connector = self._make_connector([[_result(1, "Job")]])
        since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=3)
        list(
            connector.fetch(
                AdzunaQuery(keywords="x", country="gb"), since, run_id="run-1"
            )
        )
        self.assertIn(self.calls[0]["max_days_old"], (3, 4))

    def test_payload_sha256_changes_when_result_content_changes(self) -> None:
        """Two fetches of the same job id with different titles hash differently."""
        connector_v1 = self._make_connector([[_result(1, "Data Engineer")]])
        connector_v2 = self._make_connector([[_result(1, "Senior Data Engineer")]])
        job_v1 = list(
            connector_v1.fetch(
                AdzunaQuery(keywords="x", country="gb"), None, run_id="run-1"
            )
        )[0]
        job_v2 = list(
            connector_v2.fetch(
                AdzunaQuery(keywords="x", country="gb"), None, run_id="run-1"
            )
        )[0]
        self.assertNotEqual(job_v1.payload_sha256, job_v2.payload_sha256)


if __name__ == "__main__":
    unittest.main()
