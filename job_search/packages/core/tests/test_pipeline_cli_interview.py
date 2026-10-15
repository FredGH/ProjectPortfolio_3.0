"""Unit tests for the interview CLI subcommands (services patched out)."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402

from core.interview.config import WorkItem  # noqa: E402
from core.interview.service import TopicResult  # noqa: E402

_USER = str(uuid.uuid4())
_WORK = [WorkItem("data_engineer", "SQL", "technical", 30)]


def _run(argv, *, work=None, unconfigured=(), results=None, stdin="", questions=None):  # type: ignore[no-untyped-def]
    """Run the CLI with the services patched.

    Args:
        argv: CLI arguments.
        work: What `pending_work` returns.
        unconfigured: Categories without topics.
        results: What `generate_work` returns.
        stdin: Text typed at the prompt.
        questions: What `list_questions` returns.

    Returns:
        `(exit code, stdout, mocks)`.
    """
    out = io.StringIO()
    with (
        mock.patch("app.cli.build_engine"),
        mock.patch("app.cli._build_llm_adapters", return_value={}),
        mock.patch(
            "app.cli.interview_service.pending_work",
            return_value=(work or [], list(unconfigured)),
        ),
        mock.patch(
            "app.cli.interview_service.generate_work", return_value=results or []
        ) as gen,
        mock.patch("app.cli.interview_store.reject_topic", return_value=4) as reject,
        mock.patch(
            "app.cli.interview_store.list_questions", return_value=questions or []
        ),
        mock.patch("app.cli.list_interview_progress", return_value={}),
        mock.patch("builtins.input", return_value=stdin),
        contextlib.redirect_stdout(out),
    ):
        code = main(argv)
    return code, out.getvalue(), SimpleNamespace(generate=gen, reject=reject)


class TestGenerateQuestionBank(unittest.TestCase):
    """generate-question-bank asks before spending."""

    def test_yes_prints_the_estimate_and_generates(self) -> None:
        results = [
            TopicResult("data_engineer", "SQL", "technical", approved=30, rounds=3)
        ]
        code, text, mocks = _run(
            ["generate-question-bank", "--category", "data_engineer", "--yes"],
            work=_WORK,
            results=results,
        )
        self.assertEqual(code, 0)
        self.assertIn("estimated cost about $0.24", text)
        self.assertIn("30 approved", text)
        mocks.generate.assert_called_once()

    def test_answering_no_spends_nothing(self) -> None:
        code, text, mocks = _run(["generate-question-bank"], work=_WORK, stdin="n")
        self.assertEqual(code, 1)
        self.assertIn("nothing spent", text)
        mocks.generate.assert_not_called()

    def test_nothing_to_generate_exits_zero_without_a_call(self) -> None:
        code, text, mocks = _run(["generate-question-bank"], work=[])
        self.assertEqual(code, 0)
        self.assertIn("nothing to generate", text)
        mocks.generate.assert_not_called()

    def test_unconfigured_categories_are_reported(self) -> None:
        _c, text, _m = _run(["generate-question-bank"], unconfigured=["zzz"])
        self.assertIn("no topics configured for zzz", text)

    def test_a_topic_that_errors_makes_the_exit_code_one(self) -> None:
        results = [
            TopicResult("c", "t", "technical", rounds=1, error="reviewer unusable")
        ]
        code, text, _m = _run(
            ["generate-question-bank", "--yes"], work=_WORK, results=results
        )
        self.assertEqual(code, 1)
        self.assertIn("reviewer unusable", text)

    def test_force_topic_needs_a_category_and_topic_and_rejects_first(self) -> None:
        code, text, mocks = _run(["generate-question-bank", "--force-topic"])
        self.assertEqual(code, 1)
        self.assertIn("needs --category and --topic", text)
        mocks.reject.assert_not_called()
        _c, text, mocks = _run(
            [
                "generate-question-bank",
                "--force-topic",
                "--category",
                "c",
                "--topic",
                "t",
            ]
        )
        mocks.reject.assert_called_once()
        self.assertIn("marked 4 existing", text)


class TestOtherCommands(unittest.TestCase):
    """score-question-gaps and export-anki."""

    def test_export_with_no_questions_exits_one(self) -> None:
        code, text, _m = _run(["export-anki", "--user-id", _USER])
        self.assertEqual(code, 1)
        self.assertIn("no approved questions", text)

    def test_export_writes_the_file(self) -> None:
        from datetime import UTC, datetime

        from core.interview.store import StoredQuestion

        q = StoredQuestion(
            uuid.uuid4(),
            "c",
            "T",
            "technical",
            3,
            "Q?",
            "A.",
            [],
            "generated",
            "approved",
            None,
            datetime(2026, 10, 15, tzinfo=UTC),
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bank.txt"
            code, text, _m = _run(
                ["export-anki", "--user-id", _USER, "--out", str(out)], questions=[q]
            )
            content = out.read_text()
        self.assertEqual(code, 0)
        self.assertIn("Q?\tA.\tT", content)
        self.assertIn("1 note(s)", text)

    def test_score_gaps_reports_the_count_and_a_missing_cv(self) -> None:
        with (
            mock.patch("app.cli.build_engine"),
            mock.patch("app.cli.interview_practice.refresh_gaps", return_value=7),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            code = main(["score-question-gaps", "--user-id", _USER])
        self.assertEqual((code, "7 question(s) scored" in out.getvalue()), (0, True))
        from core.artefacts.service import NoCvError

        with (
            mock.patch("app.cli.build_engine"),
            mock.patch(
                "app.cli.interview_practice.refresh_gaps",
                side_effect=NoCvError("add your CV first"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            code = main(["score-question-gaps", "--user-id", _USER])
        self.assertEqual(code, 1)
        self.assertIn("add your CV first", out.getvalue())


if __name__ == "__main__":
    unittest.main()
