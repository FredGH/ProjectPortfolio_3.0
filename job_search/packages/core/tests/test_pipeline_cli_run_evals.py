"""Unit tests for the `run-evals` pipeline CLI subcommand.

These tests mock only `run_eval` itself — never the database — so they
verify argument parsing and output-formatting/exit-code behavior for
`apps/pipeline/app/cli.py`'s `run-evals` subcommand in isolation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from core.evals.runner import EvalRunResult

# Insert pipeline app path and import. Unlike tests/test_pipeline_cli.py,
# no snapshot/restore of sys.modules["app"] is needed here: apps/api has
# no cli.py, so there is no competing "app.cli" module to collide with —
# whichever test file (this one or test_pipeline_cli.py) imports
# app.cli first leaves the same module object cached in sys.modules for
# the other to share, which is required for `@mock.patch("app.cli.
# run_eval")` to patch the exact module object `main()` executes against.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402


class TestRunEvalsSubcommand(unittest.TestCase):
    """Tests for `pipeline run-evals`."""

    @mock.patch("app.cli.run_eval")
    def test_reports_ok_result_and_exits_zero(self, mock_run_eval: mock.Mock) -> None:
        """A non-regressed "ok" result exits 0 and calls run_eval once.

        Args:
            mock_run_eval: The patched `app.cli.run_eval`.
        """
        mock_run_eval.return_value = EvalRunResult(
            task="job_categorisation",
            provider="target",
            status="ok",
            score=0.95,
            case_count=25,
            previous_score=0.90,
            delta=0.05,
            regressed=False,
        )
        exit_code = main(
            ["run-evals", "--task", "job_categorisation", "--provider", "target"]
        )
        self.assertEqual(exit_code, 0)
        mock_run_eval.assert_called_once()
        self.assertEqual(mock_run_eval.call_args.args, ("job_categorisation", "target"))

    @mock.patch("app.cli.run_eval")
    def test_exits_non_zero_on_a_flagged_regression(
        self, mock_run_eval: mock.Mock
    ) -> None:
        """A flagged regression exits 1.

        Args:
            mock_run_eval: The patched `app.cli.run_eval`.
        """
        mock_run_eval.return_value = EvalRunResult(
            task="job_categorisation",
            provider="target",
            status="ok",
            score=0.5,
            case_count=25,
            previous_score=0.95,
            delta=-0.45,
            regressed=True,
        )
        exit_code = main(
            ["run-evals", "--task", "job_categorisation", "--provider", "target"]
        )
        self.assertEqual(exit_code, 1)

    @mock.patch("app.cli.run_eval")
    def test_all_flag_runs_every_configured_task(
        self, mock_run_eval: mock.Mock
    ) -> None:
        """`--all` iterates over every configured eval task.

        Args:
            mock_run_eval: The patched `app.cli.run_eval`.
        """
        mock_run_eval.return_value = EvalRunResult(
            task="job_categorisation",
            provider="target",
            status="insufficient_data",
            score=None,
            case_count=0,
        )
        exit_code = main(["run-evals", "--all", "--provider", "target"])
        self.assertEqual(exit_code, 0)
        self.assertGreaterEqual(mock_run_eval.call_count, 1)

    @mock.patch("app.cli.run_eval")
    def test_missing_adapter_prints_a_clear_message_and_exits_non_zero(
        self, mock_run_eval: mock.Mock
    ) -> None:
        """`run_eval` raising `KeyError` (no adapter configured for the
        resolved provider, e.g. no `ANTHROPIC_API_KEY`) must not crash
        `run-evals` with a raw traceback — it should print one clear
        line naming the task/provider and the missing key, and the
        command must still exit non-zero since this task/provider
        combination never completed.

        Args:
            mock_run_eval: The patched `app.cli.run_eval`, configured
                to raise `KeyError("anthropic")` as `core.llm.gateway.
                complete` does when the resolved provider has no
                matching adapter.

        Returns:
            None.
        """
        mock_run_eval.side_effect = KeyError("anthropic")
        with mock.patch("builtins.print") as mock_print:
            exit_code = main(
                ["run-evals", "--task", "job_categorisation", "--provider", "target"]
            )
        self.assertEqual(exit_code, 1)
        printed_lines = [call.args[0] for call in mock_print.call_args_list]
        self.assertTrue(
            any(
                "job_categorisation" in line
                and "target" in line
                and "anthropic" in line
                for line in printed_lines
            ),
            f"expected a clear error line naming task/provider/key, "
            f"got: {printed_lines}",
        )


if __name__ == "__main__":
    unittest.main()
