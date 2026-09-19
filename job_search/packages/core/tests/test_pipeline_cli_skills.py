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

from app.cli import main  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
