"""Tests for ReedConnector."""

from __future__ import annotations

import datetime
import unittest

import httpx

from core.ingestion.reed_connector import ReedConnector, ReedQuery


def _result(job_id: int, title: str, date: str) -> dict[str, object]:
    return {
        "jobId": job_id,
        "jobTitle": title,
        "jobUrl": f"https://www.reed.co.uk/jobs/{title.lower()}/{job_id}",
        "employerName": "Acme",
        "locationName": "London",
        "date": date,
        "expirationDate": "31/12/2026",
        "jobDescription": "A great role...",
    }


class TestReedConnector(unittest.TestCase):
    """Unit tests — every HTTP call is injected, no real network."""

    def setUp(self) -> None:
        """Provide a real (but unused) httpx.Client for the connector."""
        self.http_client = httpx.Client()
        self.calls: list[dict[str, object]] = []

    def tearDown(self) -> None:
        """Close the httpx.Client."""
        self.http_client.close()

    def _make_connector(self, pages: list[list[dict[str, object]]]) -> ReedConnector:
        """Build a connector whose fetch_page_fn returns `pages` in order,
        recording every call's kwargs into self.calls."""

        def fake_fetch_page(
            http_client,
            *,
            api_key,
            keywords,
            location,
            results_to_skip,
            results_to_take,
        ):
            self.calls.append(
                {
                    "keywords": keywords,
                    "location": location,
                    "results_to_skip": results_to_skip,
                }
            )
            index = results_to_skip // results_to_take
            results = pages[index] if index < len(pages) else []
            return {"results": results, "totalResults": sum(len(p) for p in pages)}

        return ReedConnector(
            http_client=self.http_client,
            api_key="test-key",
            fetch_page_fn=fake_fetch_page,
        )

    def test_fetch_maps_one_page_of_results_to_raw_jobs(self) -> None:
        """A single short page yields one RawJob per result, then stops."""
        connector = self._make_connector([[_result(1, "Data Engineer", "01/09/2026")]])
        jobs = list(
            connector.fetch(ReedQuery(keywords="data engineer"), None, run_id="run-1")
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "reed")
        self.assertEqual(jobs[0].source_job_id, "1")
        self.assertEqual(jobs[0].run_id, "run-1")
        self.assertEqual(len(self.calls), 1)

    def test_fetch_paginates_via_results_to_skip(self) -> None:
        """A full page triggers a second fetch at the next offset."""
        full_page = [_result(i, f"Job {i}", "01/09/2026") for i in range(50)]
        short_page = [_result(100, "Last Job", "01/09/2026")]
        connector = self._make_connector([full_page, short_page])
        jobs = list(
            connector.fetch(ReedQuery(keywords="data engineer"), None, run_id="run-1")
        )
        self.assertEqual(len(jobs), 51)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["results_to_skip"], 0)
        self.assertEqual(self.calls[1]["results_to_skip"], 50)

    def test_fetch_stops_at_max_pages_even_with_full_pages(self) -> None:
        """max_pages caps the number of API calls, even if every page is full."""
        full_page = [_result(i, f"Job {i}", "01/09/2026") for i in range(50)]
        connector = self._make_connector([full_page] * 10)
        jobs = list(
            connector.fetch(ReedQuery(keywords="x", max_pages=2), None, run_id="run-1")
        )
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(jobs), 100)

    def test_location_is_passed_through_when_given(self) -> None:
        """query.location reaches fetch_page_fn unchanged."""
        connector = self._make_connector([[_result(1, "Job", "01/09/2026")]])
        list(
            connector.fetch(
                ReedQuery(keywords="x", location="Manchester"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(self.calls[0]["location"], "Manchester")

    def test_since_filters_out_jobs_posted_before_it(self) -> None:
        """A job whose `date` is before `since` is excluded."""
        connector = self._make_connector(
            [[_result(1, "Old", "01/01/2026"), _result(2, "New", "01/09/2026")]]
        )
        since = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
        jobs = list(connector.fetch(ReedQuery(keywords="x"), since, run_id="run-1"))
        self.assertEqual([j.source_job_id for j in jobs], ["2"])

    def test_payload_sha256_changes_when_result_content_changes(self) -> None:
        """Two fetches of the same jobId with different titles hash differently."""
        connector_v1 = self._make_connector(
            [[_result(1, "Data Engineer", "01/09/2026")]]
        )
        connector_v2 = self._make_connector(
            [[_result(1, "Senior Data Engineer", "01/09/2026")]]
        )
        job_v1 = list(
            connector_v1.fetch(ReedQuery(keywords="x"), None, run_id="run-1")
        )[0]
        job_v2 = list(
            connector_v2.fetch(ReedQuery(keywords="x"), None, run_id="run-1")
        )[0]
        self.assertNotEqual(job_v1.payload_sha256, job_v2.payload_sha256)

    def test_malformed_result_is_skipped_and_fetch_continues(self) -> None:
        """A result missing a required key is skipped, not raised."""
        good_1 = _result(1, "Good Job", "01/09/2026")
        broken = {"jobId": 2, "jobTitle": "Broken Job"}  # missing jobUrl
        good_2 = _result(3, "Another Good Job", "01/09/2026")
        connector = self._make_connector([[good_1, broken, good_2]])
        jobs = list(connector.fetch(ReedQuery(keywords="x"), None, run_id="run-1"))
        self.assertEqual([j.source_job_id for j in jobs], ["1", "3"])


if __name__ == "__main__":
    unittest.main()
