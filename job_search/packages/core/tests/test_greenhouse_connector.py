"""Tests for GreenhouseConnector."""

from __future__ import annotations

import datetime
import unittest
import uuid

import httpx

from core.db.target_company import TargetCompany
from core.ingestion.greenhouse_connector import GreenhouseConnector, GreenhouseQuery


def _fake_company(name: str, board_slug: str) -> TargetCompany:
    return TargetCompany(
        id=uuid.uuid4(),
        name=name,
        ats_provider="greenhouse",
        board_slug=board_slug,
        active=True,
        added_at=datetime.datetime.now(datetime.UTC),
    )


def _job(job_id: int, title: str, updated_at: str) -> dict[str, object]:
    return {
        "id": job_id,
        "title": title,
        "updated_at": updated_at,
        "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{job_id}",
        "location": {"name": "Remote"},
    }


class TestGreenhouseConnector(unittest.TestCase):
    """Unit tests — every HTTP/DB call is injected, no real network or DB."""

    def setUp(self) -> None:
        """Provide a real (but unused) httpx.Client for the connector."""
        self.http_client = httpx.Client()

    def tearDown(self) -> None:
        """Close the httpx.Client."""
        self.http_client.close()

    def test_fetch_with_explicit_board_slugs_skips_the_registry(self) -> None:
        """Passing board_slugs bypasses list_companies_fn entirely."""

        def fake_fetch_board(http_client, board_slug):
            self.assertEqual(board_slug, "acme")
            return [_job(1, "Engineer", "2026-09-01T00:00:00Z")]

        def fake_list_companies(database_url, *, ats_provider):
            self.fail("list_companies_fn should not be called")

        connector = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fake_fetch_board,
            list_companies_fn=fake_list_companies,
        )
        jobs = list(
            connector.fetch(GreenhouseQuery(board_slugs=["acme"]), None, run_id="run-1")
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "greenhouse")
        self.assertEqual(jobs[0].source_job_id, "1")
        self.assertEqual(jobs[0].run_id, "run-1")

    def test_fetch_with_no_board_slugs_reads_the_active_registry(self) -> None:
        """query.board_slugs=None reads every active company via list_companies_fn."""
        companies = [_fake_company("Acme", "acme"), _fake_company("Beta", "beta")]

        def fake_fetch_board(http_client, board_slug):
            return [_job(1, f"Job at {board_slug}", "2026-09-01T00:00:00Z")]

        def fake_list_companies(database_url, *, ats_provider):
            self.assertEqual(ats_provider, "greenhouse")
            return companies

        connector = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fake_fetch_board,
            list_companies_fn=fake_list_companies,
        )
        jobs = list(connector.fetch(GreenhouseQuery(), None, run_id="run-1"))
        self.assertEqual(len(jobs), 2)
        self.assertEqual({j.payload["_board_slug"] for j in jobs}, {"acme", "beta"})

    def test_since_filters_out_jobs_updated_before_it(self) -> None:
        """A job whose updated_at is before `since` is excluded."""

        def fake_fetch_board(http_client, board_slug):
            return [
                _job(1, "Old", "2026-01-01T00:00:00Z"),
                _job(2, "New", "2026-09-01T00:00:00Z"),
            ]

        connector = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fake_fetch_board,
            list_companies_fn=lambda *a, **k: self.fail("unused"),
        )
        since = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
        jobs = list(
            connector.fetch(
                GreenhouseQuery(board_slugs=["acme"]), since, run_id="run-1"
            )
        )
        self.assertEqual([j.source_job_id for j in jobs], ["2"])

    def test_empty_board_returns_no_jobs(self) -> None:
        """A board with zero open roles yields nothing, not an error."""

        def fake_fetch_board(http_client, board_slug):
            return []

        connector = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fake_fetch_board,
            list_companies_fn=lambda *a, **k: self.fail("unused"),
        )
        jobs = list(
            connector.fetch(GreenhouseQuery(board_slugs=["acme"]), None, run_id="run-1")
        )
        self.assertEqual(jobs, [])

    def test_payload_sha256_changes_when_job_content_changes(self) -> None:
        """Two fetches of the same job_id with different content hash differently."""

        def fetch_v1(http_client, board_slug):
            return [_job(1, "Engineer", "2026-09-01T00:00:00Z")]

        def fetch_v2(http_client, board_slug):
            return [_job(1, "Senior Engineer", "2026-09-02T00:00:00Z")]

        connector_v1 = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fetch_v1,
            list_companies_fn=lambda *a, **k: self.fail("unused"),
        )
        connector_v2 = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fetch_v2,
            list_companies_fn=lambda *a, **k: self.fail("unused"),
        )
        job_v1 = list(
            connector_v1.fetch(
                GreenhouseQuery(board_slugs=["acme"]), None, run_id="run-1"
            )
        )[0]
        job_v2 = list(
            connector_v2.fetch(
                GreenhouseQuery(board_slugs=["acme"]), None, run_id="run-1"
            )
        )[0]
        self.assertNotEqual(job_v1.payload_sha256, job_v2.payload_sha256)


if __name__ == "__main__":
    unittest.main()
