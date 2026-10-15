"""Unit tests for the `render-cv` pipeline CLI subcommand (database reads are
patched out; the render itself is real and writes to a temp directory)."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402
from tests.render_fixtures import make_tailored_document  # noqa: E402

from core.tailoring.schema import JobContext, JobSource  # noqa: E402

_ARGS = ["--user-id", str(uuid.uuid4()), "--job-group-id", "j1"]
_CREATED = datetime(2026, 10, 13, 8, 30)


def _job(company: str | None) -> JobContext:
    return JobContext(
        job_group_id="j1",
        title_for_display="Lead Data Engineer",
        company=company,
        description="Build things.",
        skills=[],
    )


def _approved() -> SimpleNamespace:
    return SimpleNamespace(
        status="approved", document=make_tailored_document(), created_at=_CREATED
    )


def _cv_dir(root: str) -> Path:
    return Path(root) / "zz_fixture" / "2026-10" / "lead_data_engineer" / "acme_bank"


def _run(
    argv: list[str], run: object, company: str | None = "Acme Bank"
) -> tuple[int, str]:
    out = io.StringIO()
    with (
        mock.patch("app.cli.build_engine"),
        mock.patch("app.cli.latest_run_id", return_value=uuid.uuid4() if run else None),
        mock.patch("app.cli.read_run", return_value=run),
        mock.patch("app.cli.load_job_context", return_value=_job(company)),
        mock.patch("app.cli.load_job_source", return_value=JobSource()),
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

    def test_an_approved_run_writes_docx_txt_and_pdf_by_default(self) -> None:
        run = _approved()
        with tempfile.TemporaryDirectory() as tmp:
            code, text = _run(["render-cv", *_ARGS, "--out-dir", tmp], run)
            names = sorted(p.name for p in _cv_dir(tmp).iterdir())
        self.assertEqual(code, 0)
        base = "Fixture_Lead_Data_Engineer_Acme_Bank"
        self.assertEqual(
            names, [f"{base}.docx", f"{base}.pdf", f"{base}.txt", "job_spec.md"]
        )
        self.assertIn("render-cv complete", text)
        self.assertIn(".pdf", text)

    def test_ats_only_skips_the_pdf(self) -> None:
        run = _approved()
        with tempfile.TemporaryDirectory() as tmp:
            code, text = _run(
                ["render-cv", *_ARGS, "--out-dir", tmp, "--ats-only"], run
            )
            names = sorted(p.suffix for p in _cv_dir(tmp).iterdir())
        self.assertEqual(code, 0)
        self.assertEqual(names, [".docx", ".md", ".txt"])
        self.assertNotIn(".pdf", text)

    def test_a_blank_title_prints_the_reason_instead_of_a_traceback(self) -> None:
        run = SimpleNamespace(
            status="approved",
            document=make_tailored_document(target_title=" "),
            created_at=_CREATED,
        )
        with tempfile.TemporaryDirectory() as tmp:
            code, text = _run(["render-cv", *_ARGS, "--out-dir", tmp], run)
        self.assertEqual(code, 1)
        self.assertIn("render-cv:", text)

    def test_warnings_from_the_render_are_printed(self) -> None:
        document = make_tailored_document()
        document.experience[0].bullets[0].text = "Built 日本語 dashboards"
        run = SimpleNamespace(status="approved", document=document, created_at=_CREATED)
        with tempfile.TemporaryDirectory() as tmp:
            code, text = _run(["render-cv", *_ARGS, "--out-dir", tmp], run)
        self.assertEqual(code, 0)
        self.assertIn("render-cv warning:", text)

    def test_the_latest_approved_run_is_rendered_when_a_newer_one_was_cancelled(
        self,
    ) -> None:
        approved_id, cancelled_id = uuid.uuid4(), uuid.uuid4()
        runs = {
            approved_id: _approved(),
            cancelled_id: SimpleNamespace(status="cancelled", document=None),
        }

        def lookup(engine, user_id, job_group_id, status=None):  # type: ignore[no-untyped-def]
            """Return the newest run id, or the newest approved one if asked."""
            return approved_id if status == "approved" else cancelled_id

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("app.cli.build_engine"),
                mock.patch("app.cli.latest_run_id", side_effect=lookup),
                mock.patch("app.cli.read_run", side_effect=lambda e, u, rid: runs[rid]),
                mock.patch("app.cli.load_job_context", return_value=_job("Acme Bank")),
                mock.patch("app.cli.load_job_source", return_value=JobSource()),
                contextlib.redirect_stdout(out),
            ):
                code = main(["render-cv", *_ARGS, "--out-dir", tmp, "--ats-only"])
            names = sorted(p.suffix for p in _cv_dir(tmp).iterdir())
        self.assertEqual(code, 0)
        self.assertEqual(names, [".docx", ".md", ".txt"])

    def test_the_job_spec_is_written_beside_the_cv_with_its_links(self) -> None:
        source = JobSource(source_urls=("https://www.reed.co.uk/jobs/x/1",))
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("app.cli.build_engine"),
                mock.patch("app.cli.latest_run_id", return_value=uuid.uuid4()),
                mock.patch("app.cli.read_run", return_value=_approved()),
                mock.patch("app.cli.load_job_context", return_value=_job("Acme Bank")),
                mock.patch("app.cli.load_job_source", return_value=source),
                mock.patch.dict("os.environ", {"UI_BASE_URL": "http://ui.test:9"}),
                contextlib.redirect_stdout(out),
            ):
                code = main(["render-cv", *_ARGS, "--out-dir", tmp, "--ats-only"])
            spec = (_cv_dir(tmp) / "job_spec.md").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn("https://www.reed.co.uk/jobs/x/1", spec)
        self.assertIn("http://ui.test:9/Tailored_CV_Review?job=j1", spec)
        self.assertIn("http://ui.test:9/Company_Intel?job=j1", spec)
        self.assertIn("job_spec=", out.getvalue())


if __name__ == "__main__":
    unittest.main()
