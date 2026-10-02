"""Unit tests for the `tailor-cv` pipeline CLI subcommand (parsing and
error handling only — the loop has its own integration tests)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# See test_pipeline_cli_run_evals.py for why sys.modules needs no
# snapshot/restore here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402

from core.tailoring.loop import (  # noqa: E402
    CriticUnavailableError,
    NoCvError,
    TailoringOutcome,
)


class TestTailorCvSubcommand(unittest.TestCase):
    def test_is_registered(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["tailor-cv", "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_requires_both_arguments(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["tailor-cv", "--user-id", str(uuid.uuid4())])
        self.assertEqual(ctx.exception.code, 2)

    def test_a_precondition_failure_prints_the_reason_and_exits_one(self) -> None:
        out = io.StringIO()
        with (
            mock.patch(
                "app.cli.run_tailoring", side_effect=NoCvError("this user has no CV")
            ),
            mock.patch("app.cli.build_engine"),
            mock.patch("app.cli._build_llm_adapters"),
            contextlib.redirect_stdout(out),
        ):
            exit_code = main(
                ["tailor-cv", "--user-id", str(uuid.uuid4()), "--job-group-id", "j1"]
            )
        self.assertEqual(exit_code, 1)
        self.assertIn("this user has no CV", out.getvalue())

    def test_no_anthropic_key_prints_the_reason_and_exits_one(self) -> None:
        out = io.StringIO()
        message = (
            "the fabrication critic needs an Anthropic API key (set ANTHROPIC_API_KEY)"
        )
        with (
            mock.patch(
                "app.cli.run_tailoring", side_effect=CriticUnavailableError(message)
            ),
            mock.patch("app.cli.build_engine"),
            mock.patch("app.cli._build_llm_adapters"),
            contextlib.redirect_stdout(out),
        ):
            exit_code = main(
                ["tailor-cv", "--user-id", str(uuid.uuid4()), "--job-group-id", "j1"]
            )
        self.assertEqual(exit_code, 1)
        self.assertIn(message, out.getvalue())

    def _stored(self, status: str, error: str | None, orphan_statuses: list[str]):
        return SimpleNamespace(
            status=status,
            error_message=error,
            orphans=[SimpleNamespace(status=s) for s in orphan_statuses],
        )

    def _main_with(self, outcome: TailoringOutcome, stored) -> tuple[int, str]:
        out = io.StringIO()
        with (
            mock.patch("app.cli.run_tailoring", return_value=outcome),
            mock.patch("app.cli.read_run", return_value=stored),
            mock.patch("app.cli.build_engine"),
            mock.patch("app.cli._build_llm_adapters"),
            contextlib.redirect_stdout(out),
        ):
            exit_code = main(
                ["tailor-cv", "--user-id", str(uuid.uuid4()), "--job-group-id", "j1"]
            )
        return exit_code, out.getvalue()

    def test_a_failed_run_prints_its_error_message(self) -> None:
        outcome = TailoringOutcome(run_id=uuid.uuid4(), status="failed", attempts=3)
        exit_code, out = self._main_with(
            outcome, self._stored("failed", "CriticError: unusable critic reply", [])
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("CriticError: unusable critic reply", out)

    def test_a_review_run_prints_how_many_lines_await_a_decision(self) -> None:
        outcome = TailoringOutcome(
            run_id=uuid.uuid4(), status="needs_review", attempts=3
        )
        exit_code, out = self._main_with(
            outcome,
            self._stored("needs_review", None, ["pending", "pending", "linked"]),
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("pending_orphans=2", out)

    def test_a_failed_run_exits_one_and_a_review_run_exits_zero(self) -> None:
        run_id = uuid.uuid4()
        for status, expected in (("failed", 1), ("needs_review", 0), ("approved", 0)):
            out = io.StringIO()
            with (
                mock.patch(
                    "app.cli.run_tailoring",
                    return_value=TailoringOutcome(
                        run_id=run_id, status=status, attempts=2
                    ),
                ),
                mock.patch(
                    "app.cli.read_run", return_value=self._stored(status, None, [])
                ),
                mock.patch("app.cli.build_engine"),
                mock.patch("app.cli._build_llm_adapters"),
                contextlib.redirect_stdout(out),
            ):
                exit_code = main(
                    [
                        "tailor-cv",
                        "--user-id",
                        str(uuid.uuid4()),
                        "--job-group-id",
                        "j1",
                    ]
                )
            self.assertEqual(exit_code, expected, status)
            self.assertIn(status, out.getvalue())
            self.assertIn(str(run_id), out.getvalue())


if __name__ == "__main__":
    unittest.main()
