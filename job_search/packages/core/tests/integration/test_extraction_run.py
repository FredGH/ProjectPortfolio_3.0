"""Integration tests for core.skills.extraction_run's lifecycle functions
(everything except run_loop, covered separately in
test_extraction_run_loop.py) against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import (
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)

from core.skills.extraction_run import (
    RunAlreadyActive,
    RunNotFound,
    get_active_run,
    get_run,
    list_filter_options,
    request_cancel,
    start_run,
)

_FIXTURE_SOURCES = ["zzfixture-source"]


class TestStartRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)

    def tearDown(self) -> None:
        purge_fixtures(self.owner)

    def test_starts_a_running_row_when_jobs_are_pending(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run1', 'Python required.', "
                    "'zzfixture-source', 'src-1', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
        run_id, total_pending = start_run(
            self.app, sources=_FIXTURE_SOURCES, countries=None
        )
        self.assertGreaterEqual(total_pending, 1)
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "running")
        self.assertEqual(status.sources, _FIXTURE_SOURCES)
        self.assertEqual(status.total_pending, total_pending)
        self.assertEqual(status.extracted_count, 0)
        self.assertFalse(status.cancel_requested)

    def test_starts_already_completed_when_nothing_is_pending(self) -> None:
        run_id, total_pending = start_run(
            self.app, sources=["zzfixture-nonexistent"], countries=None
        )
        self.assertEqual(total_pending, 0)
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "completed")
        self.assertIsNotNone(status.finished_at)

    def test_a_second_start_while_one_is_active_raises(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run2', 'Python required.', "
                    "'zzfixture-source', 'src-2', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
        start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        with self.assertRaises(RunAlreadyActive):
            start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)

    def test_get_active_run_returns_none_when_nothing_is_running(self) -> None:
        self.assertIsNone(get_active_run(self.app))

    def test_get_active_run_returns_the_running_row(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run3', 'Python required.', "
                    "'zzfixture-source', 'src-3', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        active = get_active_run(self.app)
        self.assertEqual(active.run_id, run_id)


class TestRequestCancel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)

    def tearDown(self) -> None:
        purge_fixtures(self.owner)

    def test_sets_cancel_requested_on_a_running_run(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run4', 'Python required.', "
                    "'zzfixture-source', 'src-4', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        request_cancel(self.app, run_id)
        self.assertTrue(get_run(self.app, run_id).cancel_requested)

    def test_raises_for_an_unknown_run_id(self) -> None:
        with self.assertRaises(RunNotFound):
            request_cancel(self.app, uuid.uuid4())

    def test_raises_for_a_run_that_already_finished(self) -> None:
        run_id, _ = start_run(
            self.app, sources=["zzfixture-nonexistent"], countries=None
        )
        with self.assertRaises(RunNotFound):
            request_cancel(self.app, run_id)


class TestListFilterOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)

    def tearDown(self) -> None:
        purge_fixtures(self.owner)

    def test_lists_sources_and_countries_of_pending_jobs_only(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run5', 'Python required.', "
                    "'zzfixture-source', 'src-5', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, country_iso) "
                    "VALUES ('fixture-job-run5', 'ZZ')"
                )
            )
        options = list_filter_options(self.app)
        self.assertIn("zzfixture-source", options.sources)
        self.assertIn("ZZ", options.countries)


if __name__ == "__main__":
    unittest.main()
