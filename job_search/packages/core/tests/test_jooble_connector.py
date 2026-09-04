"""Tests for JoobleConnector."""

from __future__ import annotations

import datetime
import unittest

import httpx

from core.ingestion.jooble_connector import JoobleConnector, JoobleQuery


def _result(job_id: int, title: str, updated: str) -> dict[str, object]:
    return {
        "id": job_id,
        "title": title,
        "link": f"https://jooble.org/jdp/{job_id}",
        "company": "Acme",
        "location": "United Kingdom",
        "updated": updated,
    }


class TestJoobleConnector(unittest.TestCase):
    """Unit tests — every HTTP call is injected, no real network."""

    def setUp(self) -> None:
        """Provide a real (but unused) httpx.Client for the connector."""
        self.http_client = httpx.Client()
        self.calls: list[dict[str, object]] = []

    def tearDown(self) -> None:
        """Close the httpx.Client."""
        self.http_client.close()

    def _make_connector(self, pages: list[list[dict[str, object]]]) -> JoobleConnector:
        """Build a connector whose fetch_page_fn returns `pages` in order,
        recording every call's kwargs into self.calls."""

        def fake_fetch_page(http_client, *, api_key, keywords, location, page):
            self.calls.append(
                {"keywords": keywords, "location": location, "page": page}
            )
            index = page - 1
            results = pages[index] if index < len(pages) else []
            return {"jobs": results, "totalCount": sum(len(p) for p in pages)}

        return JoobleConnector(
            http_client=self.http_client,
            api_key="test-key",
            fetch_page_fn=fake_fetch_page,
        )

    def test_fetch_maps_one_page_of_results_to_raw_jobs(self) -> None:
        """A single short page yields one RawJob per result, then stops."""
        connector = self._make_connector(
            [[_result(1, "Data Engineer", "2026-09-01T00:00:00.0000000")]]
        )
        jobs = list(
            connector.fetch(JoobleQuery(keywords="data engineer"), None, run_id="run-1")
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "jooble")
        self.assertEqual(jobs[0].source_job_id, "1")
        self.assertEqual(jobs[0].run_id, "run-1")
        self.assertEqual(len(self.calls), 1)

    def test_fetch_paginates_until_a_short_page(self) -> None:
        """A full 30-result page triggers a second fetch; a short page stops."""
        full_page = [
            _result(i, f"Job {i}", "2026-09-01T00:00:00.0000000") for i in range(30)
        ]
        short_page = [_result(100, "Last Job", "2026-09-01T00:00:00.0000000")]
        connector = self._make_connector([full_page, short_page])
        jobs = list(
            connector.fetch(JoobleQuery(keywords="data engineer"), None, run_id="run-1")
        )
        self.assertEqual(len(jobs), 31)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["page"], 1)
        self.assertEqual(self.calls[1]["page"], 2)

    def test_fetch_stops_at_max_pages_even_with_full_pages(self) -> None:
        """max_pages caps the number of API calls, even if every page is full."""
        full_page = [
            _result(i, f"Job {i}", "2026-09-01T00:00:00.0000000") for i in range(30)
        ]
        connector = self._make_connector([full_page] * 10)
        jobs = list(
            connector.fetch(
                JoobleQuery(keywords="x", max_pages=2), None, run_id="run-1"
            )
        )
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(jobs), 60)

    def test_fetch_stops_immediately_on_empty_first_page(self) -> None:
        """Zero results on page 1 yields nothing and makes exactly one call."""
        connector = self._make_connector([[]])
        jobs = list(
            connector.fetch(
                JoobleQuery(keywords="nonexistent role xyz"), None, run_id="run-1"
            )
        )
        self.assertEqual(jobs, [])
        self.assertEqual(len(self.calls), 1)

    def test_location_is_passed_through_when_given(self) -> None:
        """query.location reaches fetch_page_fn unchanged."""
        connector = self._make_connector(
            [[_result(1, "Job", "2026-09-01T00:00:00.0000000")]]
        )
        list(
            connector.fetch(
                JoobleQuery(keywords="x", location="Manchester"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(self.calls[0]["location"], "Manchester")

    def test_since_filters_out_jobs_updated_before_it(self) -> None:
        """A job whose `updated` is before `since` is excluded."""
        connector = self._make_connector(
            [
                [
                    _result(1, "Old", "2026-01-01T00:00:00.0000000"),
                    _result(2, "New", "2026-09-01T00:00:00.0000000"),
                ]
            ]
        )
        since = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
        jobs = list(connector.fetch(JoobleQuery(keywords="x"), since, run_id="run-1"))
        self.assertEqual([j.source_job_id for j in jobs], ["2"])

    def test_negative_job_id_is_handled_correctly(self) -> None:
        """Jooble ids can be negative — str() must still produce a clean id."""
        connector = self._make_connector(
            [[_result(-471887246143262713, "Job", "2026-09-01T00:00:00.0000000")]]
        )
        jobs = list(connector.fetch(JoobleQuery(keywords="x"), None, run_id="run-1"))
        self.assertEqual(jobs[0].source_job_id, "-471887246143262713")

    def test_malformed_result_is_skipped_and_fetch_continues(self) -> None:
        """A result missing `link` is skipped; surrounding good results still yield."""
        good1 = _result(1, "Good One", "2026-09-01T00:00:00.0000000")
        bad = _result(2, "Bad", "2026-09-01T00:00:00.0000000")
        del bad["link"]
        good2 = _result(3, "Good Two", "2026-09-01T00:00:00.0000000")
        connector = self._make_connector([[good1, bad, good2]])
        jobs = list(connector.fetch(JoobleQuery(keywords="x"), None, run_id="run-1"))
        self.assertEqual([j.source_job_id for j in jobs], ["1", "3"])

    def test_payload_sha256_changes_when_result_content_changes(self) -> None:
        """Two fetches of the same id with different titles hash differently."""
        connector_v1 = self._make_connector(
            [[_result(1, "Data Engineer", "2026-09-01T00:00:00.0000000")]]
        )
        connector_v2 = self._make_connector(
            [[_result(1, "Senior Data Engineer", "2026-09-01T00:00:00.0000000")]]
        )
        job_v1 = list(
            connector_v1.fetch(JoobleQuery(keywords="x"), None, run_id="run-1")
        )[0]
        job_v2 = list(
            connector_v2.fetch(JoobleQuery(keywords="x"), None, run_id="run-1")
        )[0]
        self.assertNotEqual(job_v1.payload_sha256, job_v2.payload_sha256)


if __name__ == "__main__":
    unittest.main()
