"""Unit tests for the `render-cv` pipeline CLI subcommand (database reads are
patched out; the render itself is real and writes to a temp directory)."""

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
from tests.render_fixtures import make_tailored_document  # noqa: E402

_ARGS = ["--user-id", str(uuid.uuid4()), "--job-group-id", "j1"]


def _run(
    argv: list[str], run: object, company: str | None = "Acme Bank"
) -> tuple[int, str]:
    out = io.StringIO()
    with (
        mock.patch("app.cli.build_engine"),
        mock.patch("app.cli.latest_run_id", return_value=uuid.uuid4() if run else None),
        mock.patch("app.cli.read_run", return_value=run),
        mock.patch(
            "app.cli.load_job_context", return_value=SimpleNamespace(company=company)
        ),
        contextlib.redirect_stdout(out),
    ):
        return main(argv), out.getvalue()


class TestRenderCvSubcommand(unittest.TestCase):
    def test_is_registered(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["render-cv", "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_no_run_for_the_job_exits_one(self) -> None:
        code, text = _run(["render-cv", *_ARGS], None)
        self.assertEqual(code, 1)
        self.assertIn("run tailor-cv first", text)

    def test_a_run_awaiting_review_is_not_rendered(self) -> None:
        run = SimpleNamespace(status="needs_review", document=make_tailored_document())
        code, text = _run(["render-cv", *_ARGS], run)
        self.assertEqual(code, 1)
        self.assertIn("needs_review", text)

    def test_an_approved_run_writes_both_files(self) -> None:
        run = SimpleNamespace(status="approved", document=make_tailored_document())
        with tempfile.TemporaryDirectory() as tmp:
            code, text = _run(["render-cv", *_ARGS, "--out-dir", tmp], run)
            names = sorted(p.name for p in Path(tmp).iterdir())
        self.assertEqual(code, 0)
        self.assertEqual(
            names,
            [
                "Fixture_Lead_Data_Engineer_Acme_Bank.docx",
                "Fixture_Lead_Data_Engineer_Acme_Bank.txt",
            ],
        )
        self.assertIn("render-cv complete", text)


if __name__ == "__main__":
    unittest.main()
