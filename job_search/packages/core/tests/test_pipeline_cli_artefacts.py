"""Unit tests for the artefact CLI subcommands (service and database patched out)."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402
from tests.artefacts_fixtures import (  # noqa: E402
    TODAY,
    letter_reply_json,
    make_pack,
    pitch_reply_json,
)
from tests.company_intel_fixtures import make_job  # noqa: E402

from core.artefacts.assemble import (  # noqa: E402
    build_letter,
    build_pitch,
    parse_writer_reply,
)
from core.artefacts.service import NoCvError  # noqa: E402

_USER = str(uuid.uuid4())
_ARGS = ["--user-id", _USER, "--job-group-id", "zzfixture-intel-job"]
_CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def _stored(kind: str, status: str = "approved", pending: int = 0):  # type: ignore[no-untyped-def]
    """Build a stand-in stored artefact.

    Args:
        kind: `cover_letter` or `pitch`.
        status: The status.
        pending: How many open sentences are pending.

    Returns:
        A namespace shaped like `StoredArtefact`.
    """
    pack = make_pack()
    if kind == "cover_letter":
        doc = build_letter(
            pack, parse_writer_reply(letter_reply_json(), "cover_letter"), today=TODAY
        )
    else:
        doc = build_pitch(pack, parse_writer_reply(pitch_reply_json(), "pitch"))
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind=kind,
        status=status,
        content=doc.model_dump(),
        target_title="Data Engineer",
        created_at=_CREATED,
        error_message=None,
        open_sentences=[SimpleNamespace(status="pending")] * pending,
    )


def _run(argv, *, job=None, generated=None, latest=None, tailored=None):  # type: ignore[no-untyped-def]
    """Run the CLI with everything outside it patched.

    Args:
        argv: CLI arguments.
        job: The job context the loader returns (None = unknown job).
        generated: What `generate_artefact` returns (or an exception to raise).
        latest: Map of kind to the stored artefact `read_latest` returns.
        tailored: The tailored-CV run `read_run` returns, if any.

    Returns:
        `(exit code, stdout, mocks)`.
    """
    out = io.StringIO()
    latest = latest or {}
    failing = isinstance(generated, Exception)
    with (
        mock.patch("app.cli.build_engine"),
        mock.patch("app.cli.load_job_context", return_value=job),
        mock.patch("app.cli._build_llm_adapters", return_value={}),
        mock.patch(
            "app.cli.generate_artefact",
            side_effect=generated if failing else None,
            return_value=None if failing else generated,
        ) as gen,
        mock.patch(
            "app.cli.read_latest_artefact", side_effect=lambda e, u, k, j: latest.get(k)
        ),
        mock.patch(
            "app.cli.latest_run_id", return_value=uuid.uuid4() if tailored else None
        ),
        mock.patch("app.cli.read_run", return_value=tailored),
        contextlib.redirect_stdout(out),
    ):
        code = main(argv)
    return code, out.getvalue(), SimpleNamespace(generate=gen)


class TestGenerate(unittest.TestCase):
    """generate-cover-letter and generate-pitch."""

    def test_an_approved_letter_prints_its_status_and_exits_zero(self) -> None:
        code, text, mocks = _run(
            ["generate-cover-letter", *_ARGS],
            job=make_job(),
            generated=_stored("cover_letter"),
        )
        self.assertEqual(code, 0)
        self.assertIn("cover_letter approved", text)
        self.assertEqual(mocks.generate.call_args.args[2], "cover_letter")

    def test_a_pitch_needing_review_reports_the_pending_count(self) -> None:
        code, text, mocks = _run(
            ["generate-pitch", *_ARGS],
            job=make_job(),
            generated=_stored("pitch", "needs_review", pending=2),
        )
        self.assertEqual(code, 0)
        self.assertIn("2 sentence(s) awaiting your decision", text)
        self.assertEqual(mocks.generate.call_args.args[2], "pitch")

    def test_a_failed_run_exits_one_with_its_error(self) -> None:
        stored = _stored("cover_letter", "failed")
        stored.error_message = "the fact-checker's reply was unusable"
        code, text, _m = _run(
            ["generate-cover-letter", *_ARGS], job=make_job(), generated=stored
        )
        self.assertEqual(code, 1)
        self.assertIn("unusable", text)

    def test_an_unknown_job_and_no_cv_exit_one_without_spending(self) -> None:
        code, text, mocks = _run(["generate-cover-letter", *_ARGS], job=None)
        self.assertEqual(code, 1)
        self.assertIn("no such job", text)
        mocks.generate.assert_not_called()
        code, text, _m = _run(
            ["generate-pitch", *_ARGS],
            job=make_job(),
            generated=NoCvError("add your CV first"),
        )
        self.assertEqual(code, 1)
        self.assertIn("add your CV first", text)


class TestRender(unittest.TestCase):
    """render-artefacts writes the approved artefacts beside the CV."""

    def test_both_approved_artefacts_are_written_into_the_cv_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, text, _m = _run(
                ["render-artefacts", *_ARGS, "--out-dir", tmp, "--ats-only"],
                job=make_job(company="Zzfixture Intel Ltd"),
                latest={
                    "cover_letter": _stored("cover_letter"),
                    "pitch": _stored("pitch"),
                },
            )
            folder = (
                Path(tmp)
                / "zz_fixture"
                / "2026-10"
                / "data_engineer"
                / "zzfixture_intel_ltd"
            )
            names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(code, 0)
        self.assertIn("render-artefacts complete", text)
        self.assertEqual(
            names,
            [
                "Fixture_Data_Engineer_Zzfixture_Intel_Ltd_cover_letter.docx",
                "Fixture_Data_Engineer_Zzfixture_Intel_Ltd_cover_letter.txt",
                "Fixture_Data_Engineer_Zzfixture_Intel_Ltd_pitch.txt",
            ],
        )

    def test_the_folder_month_follows_the_approved_cv_run_when_there_is_one(
        self,
    ) -> None:
        tailored = SimpleNamespace(
            status="approved", created_at=datetime(2026, 9, 3, tzinfo=UTC)
        )
        with tempfile.TemporaryDirectory() as tmp:
            code, _t, _m = _run(
                ["render-artefacts", *_ARGS, "--out-dir", tmp, "--ats-only"],
                job=make_job(company="Zzfixture Intel Ltd"),
                latest={"cover_letter": _stored("cover_letter")},
                tailored=tailored,
            )
            self.assertTrue((Path(tmp) / "zz_fixture" / "2026-09").is_dir())
        self.assertEqual(code, 0)

    def test_an_artefact_that_is_not_approved_is_skipped_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, text, _m = _run(
                ["render-artefacts", *_ARGS, "--out-dir", tmp, "--ats-only"],
                job=make_job(),
                latest={
                    "cover_letter": _stored("cover_letter", "needs_review", pending=1),
                    "pitch": _stored("pitch"),
                },
            )
            files = sorted(p.suffix for p in Path(tmp).rglob("*") if p.is_file())
        self.assertEqual(code, 0)
        self.assertIn("cover_letter is needs_review", text)
        self.assertEqual(files, [".txt"])

    def test_nothing_to_render_exits_one(self) -> None:
        code, text, _m = _run(["render-artefacts", *_ARGS], job=make_job(), latest={})
        self.assertEqual(code, 1)
        self.assertIn("nothing to render", text)


if __name__ == "__main__":
    unittest.main()
