"""Tests for core.cv.jobs — the in-memory extraction job registry and
the pipeline runner that drives it. Docling/LLM/DB calls are patched
at the module level (`core.cv.jobs.docling_to_markdown`, etc.) rather
than run for real: this module tests orchestration (step ordering,
timing, failure attribution), not Docling/Ollama/Postgres themselves —
those are covered by test_cv_extract.py and the router's integration
tests.
"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from docling.exceptions import BaseError as DoclingError

from core.cv.jobs import STEP_NAMES, StepStatus, create_job, get_job, run_extraction_job
from core.cv.schema import CVTruthBase

_ADAPTERS: dict = {}
_ENGINE = object()
_USER_ID = uuid.uuid4()


class TestCreateJob(unittest.TestCase):
    def test_new_job_starts_queued_with_pending_steps_in_order(self) -> None:
        job_id = create_job()
        job = get_job(job_id)
        self.assertEqual(job.status, "queued")
        self.assertEqual([step.name for step in job.steps], list(STEP_NAMES))
        for step in job.steps:
            self.assertEqual(step.status, StepStatus.PENDING)
            self.assertIsNone(step.started_at)
            self.assertIsNone(step.duration_seconds)

    def test_unknown_job_id_returns_none(self) -> None:
        self.assertIsNone(get_job(uuid.uuid4()))


class TestRunExtractionJob(unittest.TestCase):
    @patch("core.cv.jobs.write_truth_base")
    @patch("core.cv.jobs.extract_truth_base")
    @patch("core.cv.jobs.docling_to_markdown")
    def test_successful_run_marks_all_steps_done_and_records_durations(
        self, mock_docling, mock_extract, mock_write
    ) -> None:
        mock_docling.return_value = "# Jane Doe"
        mock_extract.return_value = CVTruthBase(
            identity="Jane Doe", headline="Engineer"
        )
        mock_write.return_value = 3

        job_id = create_job()
        run_extraction_job(
            job_id,
            b"bytes",
            "cv.pdf",
            adapters=_ADAPTERS,
            engine=_ENGINE,
            user_id=_USER_ID,
        )

        job = get_job(job_id)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.result_version, 3)
        for step in job.steps:
            self.assertEqual(step.status, StepStatus.DONE)
            self.assertIsNotNone(step.duration_seconds)
            self.assertGreaterEqual(step.duration_seconds, 0.0)
        mock_write.assert_called_once_with(
            _ENGINE, _USER_ID, "# Jane Doe", mock_extract.return_value
        )

    @patch("core.cv.jobs.docling_to_markdown")
    def test_docling_failure_marks_job_failed_at_parsing_document(
        self, mock_docling
    ) -> None:
        mock_docling.side_effect = DoclingError("boom")

        job_id = create_job()
        run_extraction_job(
            job_id,
            b"bytes",
            "cv.pdf",
            adapters=_ADAPTERS,
            engine=_ENGINE,
            user_id=_USER_ID,
        )

        job = get_job(job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.failed_step, "parsing_document")
        self.assertIn("boom", job.error)
        steps_by_name = {step.name: step for step in job.steps}
        self.assertEqual(steps_by_name["parsing_document"].status, StepStatus.FAILED)
        self.assertEqual(steps_by_name["extracting_fields"].status, StepStatus.PENDING)
        self.assertEqual(steps_by_name["saving"].status, StepStatus.PENDING)

    @patch("core.cv.jobs.extract_truth_base")
    @patch("core.cv.jobs.docling_to_markdown")
    def test_llm_parse_failure_marks_job_failed_at_extracting_fields(
        self, mock_docling, mock_extract
    ) -> None:
        mock_docling.return_value = "# Jane Doe"
        mock_extract.side_effect = ValueError("bad json")

        job_id = create_job()
        run_extraction_job(
            job_id,
            b"bytes",
            "cv.pdf",
            adapters=_ADAPTERS,
            engine=_ENGINE,
            user_id=_USER_ID,
        )

        job = get_job(job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.failed_step, "extracting_fields")
        self.assertIn("bad json", job.error)
        steps_by_name = {step.name: step for step in job.steps}
        self.assertEqual(steps_by_name["parsing_document"].status, StepStatus.DONE)
        self.assertEqual(steps_by_name["extracting_fields"].status, StepStatus.FAILED)
        self.assertEqual(steps_by_name["saving"].status, StepStatus.PENDING)

    @patch("core.cv.jobs.write_truth_base")
    @patch("core.cv.jobs.extract_truth_base")
    @patch("core.cv.jobs.docling_to_markdown")
    def test_saving_failure_marks_job_failed_at_saving(
        self, mock_docling, mock_extract, mock_write
    ) -> None:
        mock_docling.return_value = "# Jane Doe"
        mock_extract.return_value = CVTruthBase(
            identity="Jane Doe", headline="Engineer"
        )
        mock_write.side_effect = RuntimeError("db down")

        job_id = create_job()
        run_extraction_job(
            job_id,
            b"bytes",
            "cv.pdf",
            adapters=_ADAPTERS,
            engine=_ENGINE,
            user_id=_USER_ID,
        )

        job = get_job(job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.failed_step, "saving")
        self.assertEqual(job.error, "db down")
        steps_by_name = {step.name: step for step in job.steps}
        self.assertEqual(steps_by_name["parsing_document"].status, StepStatus.DONE)
        self.assertEqual(steps_by_name["extracting_fields"].status, StepStatus.DONE)
        self.assertEqual(steps_by_name["saving"].status, StepStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
