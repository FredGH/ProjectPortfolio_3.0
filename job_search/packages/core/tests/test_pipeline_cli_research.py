"""Unit tests for the `research-company` CLI subcommand (service patched out)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402
from tests.company_intel_fixtures import make_job, make_source  # noqa: E402

from core.company_intel.schema import (  # noqa: E402
    IntelContent,
    Resolution,
    StoredIntel,
)
from core.company_intel.service import IntelResult  # noqa: E402

_ARGS = ["research-company", "--job-group-id", "zzfixture-intel-job"]


def _intel() -> StoredIntel:
    """Build a stored intel.

    Returns:
        A version-3 intel.
    """
    return StoredIntel(
        company_key="k",
        version=3,
        display_name="Zz Co",
        status="ok",
        researched_at=datetime(2026, 10, 14, tzinfo=UTC),
        content=IntelContent(),
        pages=[],
        resolved_from_job="j",
        model="m",
        prompt_version="claude.v1",
        usage={},
    )


def _result(outcome: str, intel: StoredIntel | None = None) -> IntelResult:
    """Build a service result.

    Args:
        outcome: The outcome name.
        intel: The intel, if any.

    Returns:
        An `IntelResult`.
    """
    res = Resolution("zzfixture-intel-job", "Zz", "employer", "Zz Co", None, "employer")
    return IntelResult(outcome, res, intel, "a message")  # type: ignore[arg-type]


def _run(argv: list[str], result: IntelResult, job=make_job()):  # type: ignore[no-untyped-def]
    """Run the CLI with the database and service patched.

    Args:
        argv: CLI arguments.
        result: What the service returns.
        job: The job context returned by the loader (None = unknown job).

    Returns:
        `(exit code, stdout, service mock, angle mock)`.
    """
    out = io.StringIO()
    with (
        mock.patch("app.cli.build_engine"),
        mock.patch("app.cli.load_job_context", return_value=job),
        mock.patch("app.cli.load_job_source", return_value=make_source()),
        mock.patch("app.cli.get_or_refresh_intel", return_value=result) as service,
        mock.patch("app.cli.get_or_create_angle", return_value=object()) as angle,
        mock.patch("app.cli._build_llm_adapters", return_value={}),
        contextlib.redirect_stdout(out),
    ):
        code = main(argv)
    return code, out.getvalue(), service, angle


class TestResearchCompany(unittest.TestCase):
    """The research-company subcommand."""

    def test_a_fresh_research_prints_the_version_and_makes_the_angle(self) -> None:
        code, text, _service, angle = _run(_ARGS, _result("refreshed", _intel()))
        self.assertEqual(code, 0)
        self.assertIn("research-company refreshed", text)
        self.assertIn("v3", text)
        self.assertIn("competitive angle: ready", text)
        angle.assert_called_once()

    def test_reused_intel_exits_zero(self) -> None:
        code, text, _s, _a = _run(_ARGS, _result("reused", _intel()))
        self.assertEqual(code, 0)
        self.assertIn("reused", text)

    def test_no_angle_flag_skips_it(self) -> None:
        code, text, _s, angle = _run(
            [*_ARGS, "--no-angle"], _result("refreshed", _intel())
        )
        self.assertEqual(code, 0)
        angle.assert_not_called()
        self.assertIn("competitive angle: skipped", text)

    def test_unknown_end_client_and_unverified_website_exit_one(self) -> None:
        for outcome in ("end_client_unknown", "website_unverified", "failed"):
            code, text, _s, angle = _run(_ARGS, _result(outcome))
            self.assertEqual(code, 1, outcome)
            self.assertIn(outcome, text)
            angle.assert_not_called()

    def test_flags_reach_the_service(self) -> None:
        _c, _t, service, _a = _run(
            [
                *_ARGS,
                "--force",
                "--end-client",
                "Real Bank",
                "--website",
                "https://bank.example",
            ],
            _result("refreshed", _intel()),
        )
        kwargs = service.call_args.kwargs
        self.assertTrue(kwargs["force"])
        self.assertEqual(kwargs["override"].end_client, "Real Bank")
        self.assertEqual(kwargs["override"].website_url, "https://bank.example")

    def test_an_unusable_website_is_rejected_before_any_research(self) -> None:
        code, text, service, _a = _run(
            [*_ARGS, "--website", "not a url"], _result("refreshed", _intel())
        )
        self.assertEqual(code, 1)
        self.assertIn("website", text)
        service.assert_not_called()

    def test_no_override_flags_means_no_override(self) -> None:
        _c, _t, service, _a = _run(_ARGS, _result("reused", _intel()))
        self.assertIsNone(service.call_args.kwargs["override"])

    def test_an_unknown_job_exits_one(self) -> None:
        code, text, service, _a = _run(_ARGS, _result("reused"), job=None)
        self.assertEqual(code, 1)
        self.assertIn("no such job", text)
        service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
