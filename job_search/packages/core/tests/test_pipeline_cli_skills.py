"""Unit tests for the Step 14 pipeline CLI subcommands.

Only argument parsing and error handling — the underlying functions have
their own integration tests.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

# See test_pipeline_cli_run_evals.py for why sys.modules needs no
# snapshot/restore here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import _esco_duplicates_note, _extraction_exit_code, main  # noqa: E402

from core.skills.esco_load import EscoLoadCounts  # noqa: E402
from core.skills.write_job_skills import WriteSummary  # noqa: E402


class TestLoadEscoSubcommand(unittest.TestCase):
    def test_reports_a_missing_release_directory_and_exits_one(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exit_code = main(["load-esco", "/nonexistent/esco/release"])
        self.assertEqual(exit_code, 1)
        self.assertIn("skills_en.csv", out.getvalue())


class TestSkillSubcommandsAreRegistered(unittest.TestCase):
    def _help_exits_zero(self, command: str) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main([command, "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_embed_esco_is_registered(self) -> None:
        self._help_exits_zero("embed-esco")

    def test_map_skills_is_registered(self) -> None:
        self._help_exits_zero("map-skills")

    def test_map_skills_offers_remap_all_auto(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                main(["map-skills", "--help"])
        self.assertIn("--remap-all-auto", out.getvalue())
        self.assertIn("--remap-unresolved", out.getvalue())

    def test_map_skills_rejects_both_remap_flags_together(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                main(["map-skills", "--remap-unresolved", "--remap-all-auto"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("not allowed with", err.getvalue())

    def test_extract_job_skills_is_registered(self) -> None:
        self._help_exits_zero("extract-job-skills")

    def test_map_cv_skills_is_registered(self) -> None:
        self._help_exits_zero("map-cv-skills")

    def test_map_cv_skills_rejects_a_malformed_user_id(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["map-cv-skills", "--user-id", "not-a-uuid"])
        self.assertEqual(ctx.exception.code, 2)


class TestExtractionExitCode(unittest.TestCase):
    def test_success_when_something_was_extracted(self) -> None:
        self.assertEqual(_extraction_exit_code(WriteSummary(3, 20, 0)), 0)

    def test_success_when_there_was_nothing_to_do(self) -> None:
        self.assertEqual(_extraction_exit_code(WriteSummary(0, 0, 0)), 0)

    def test_partial_failure_is_still_success_because_failed_jobs_are_retried(
        self,
    ) -> None:
        self.assertEqual(_extraction_exit_code(WriteSummary(2, 9, 5)), 0)

    def test_failing_every_job_is_an_error(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = _extraction_exit_code(WriteSummary(0, 0, 7))
        self.assertEqual(code, 1)
        self.assertIn("7", out.getvalue())


class TestEscoDuplicatesNote(unittest.TestCase):
    def _counts(self, dupes: tuple[str, ...], differing: tuple[str, ...]):
        return EscoLoadCounts(10, 20, 3, 4, 0, dupes, differing)

    def test_no_note_without_duplicates(self) -> None:
        self.assertIsNone(_esco_duplicates_note(self._counts((), ())))

    def test_the_note_counts_duplicates_and_says_which_row_was_kept(self) -> None:
        note = _esco_duplicates_note(self._counts(("a", "b"), ("b",)))
        self.assertIn("2 skill concept id", note)
        self.assertIn("1 with differing content", note)
        self.assertIn("last row", note)

    def test_the_note_lists_at_most_five_ids(self) -> None:
        ids = tuple(f"id{i}" for i in range(9))
        note = _esco_duplicates_note(self._counts(ids, ()))
        self.assertIn("id4", note)
        self.assertNotIn("id5", note)
        self.assertIn("9 skill concept id", note)


if __name__ == "__main__":
    unittest.main()
