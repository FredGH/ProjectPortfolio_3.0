"""Unit tests for the intel API router (database and services patched)."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.dependencies import (  # noqa: E402
    get_app_db_engine,
    get_fetcher,
    get_llm_adapters,
)
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.company_intel_fixtures import make_job, make_source  # noqa: E402

from core.company_intel.schema import (  # noqa: E402
    AngleAdvantage,
    AngleContent,
    Competitor,
    IntelContent,
    Resolution,
    StoredAngle,
    StoredIntel,
)
from core.company_intel.service import IntelResult  # noqa: E402

_NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
_RES = Resolution(
    "zzfixture-intel-job", "Zz", "employer", "Zz Co", "https://zz.example", "employer"
)


def _intel(version: int = 2) -> StoredIntel:
    """Build a stored intel with a competitor.

    Args:
        version: The version number.

    Returns:
        A `StoredIntel`.
    """
    return StoredIntel(
        company_key="k",
        version=version,
        display_name="Zz Co",
        status="ok",
        researched_at=_NOW,
        content=IntelContent(
            competitors=[Competitor(name="Rival", basis="model_knowledge")]
        ),
        pages=[{"url": "https://zz.example", "status": "ok", "error": None}],
        resolved_from_job="zzfixture-intel-job",
        model="m",
        prompt_version="claude.v1",
        usage={},
    )


def _angle() -> StoredAngle:
    """Build a stored angle.

    Returns:
        A `StoredAngle`.
    """
    return StoredAngle(
        "zzfixture-intel-job",
        "k",
        2,
        AngleContent(
            transformation="T",
            advantages=[AngleAdvantage(competitor="Rival", point="P")],
            caveats=["C"],
        ),
        "m",
        "claude.v1",
        {},
        _NOW,
    )


class TestIntelRouter(unittest.TestCase):
    """GET / POST / PUT under /intel."""

    def setUp(self) -> None:
        """Override the collaborators and patch the data access."""
        app.dependency_overrides[get_app_db_engine] = lambda: object()
        app.dependency_overrides[get_llm_adapters] = lambda: {}
        app.dependency_overrides[get_fetcher] = lambda: object()
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        patches = {
            "load_job_context": mock.patch(
                "app.routers.intel.load_job_context", return_value=make_job()
            ),
            "load_job_source": mock.patch(
                "app.routers.intel.load_job_source", return_value=make_source()
            ),
            "read_resolution": mock.patch(
                "app.routers.intel.store.read_resolution", return_value=_RES
            ),
            "read_latest_intel": mock.patch(
                "app.routers.intel.store.read_latest_intel", return_value=_intel()
            ),
            "read_intel_version": mock.patch(
                "app.routers.intel.store.read_intel_version", return_value=_intel(1)
            ),
            "list_intel_versions": mock.patch(
                "app.routers.intel.store.list_intel_versions",
                return_value=[(2, _NOW, "ok"), (1, _NOW, "ok")],
            ),
            "read_latest_angle": mock.patch(
                "app.routers.intel.store.read_latest_angle", return_value=_angle()
            ),
        }
        self.mocks = {}
        for name, patch in patches.items():
            self.mocks[name] = patch.start()
            self.addCleanup(patch.stop)

    def test_get_returns_resolution_intel_versions_and_angle(self) -> None:
        body = self.client.get("/intel/jobs/zzfixture-intel-job").json()
        self.assertEqual(body["resolution"]["end_client"], "Zz Co")
        self.assertEqual(body["intel"]["version"], 2)
        self.assertEqual(body["intel"]["content"]["competitors"][0]["name"], "Rival")
        self.assertEqual([v["version"] for v in body["versions"]], [2, 1])
        self.assertEqual(
            body["angle"]["content"]["advantages"][0]["competitor"], "Rival"
        )

    def test_a_version_query_reads_that_version(self) -> None:
        body = self.client.get("/intel/jobs/zzfixture-intel-job?version=1").json()
        self.assertEqual(body["intel"]["version"], 1)

    def test_an_unknown_job_is_404(self) -> None:
        self.mocks["load_job_context"].return_value = None
        self.assertEqual(self.client.get("/intel/jobs/nope").status_code, 404)

    def test_a_job_with_no_resolution_has_null_parts(self) -> None:
        self.mocks["read_resolution"].return_value = None
        body = self.client.get("/intel/jobs/zzfixture-intel-job").json()
        self.assertIsNone(body["resolution"])
        self.assertIsNone(body["intel"])
        self.assertEqual(body["versions"], [])

    def test_post_research_runs_the_service_and_returns_the_view(self) -> None:
        result = IntelResult("refreshed", _RES, _intel(), "Researched.")
        with (
            mock.patch(
                "app.routers.intel.get_or_refresh_intel", return_value=result
            ) as service,
            mock.patch(
                "app.routers.intel.get_or_create_angle", return_value=_angle()
            ) as angle,
        ):
            response = self.client.post(
                "/intel/jobs/zzfixture-intel-job/research", json={"force": True}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            (body["outcome"], body["message"]), ("refreshed", "Researched.")
        )
        self.assertTrue(service.call_args.kwargs["force"])
        angle.assert_called_once()

    def test_post_research_skips_the_angle_when_there_is_no_intel(self) -> None:
        result = IntelResult("end_client_unknown", _RES, None, "Set the end client.")
        with (
            mock.patch("app.routers.intel.get_or_refresh_intel", return_value=result),
            mock.patch("app.routers.intel.get_or_create_angle") as angle,
        ):
            body = self.client.post(
                "/intel/jobs/zzfixture-intel-job/research", json={}
            ).json()
        self.assertEqual(body["outcome"], "end_client_unknown")
        angle.assert_not_called()

    def test_put_resolution_stores_a_manual_override(self) -> None:
        with mock.patch("app.routers.intel.resolve_company") as resolve:
            response = self.client.put(
                "/intel/jobs/zzfixture-intel-job/resolution",
                json={"end_client": "Real Bank", "website_url": "https://bank.example"},
            )
        self.assertEqual(response.status_code, 200)
        override = resolve.call_args.kwargs["override"]
        self.assertEqual(
            (override.end_client, override.website_url),
            ("Real Bank", "https://bank.example"),
        )

    def test_put_resolution_with_an_unusable_website_is_422(self) -> None:
        with mock.patch("app.routers.intel.resolve_company") as resolve:
            response = self.client.put(
                "/intel/jobs/zzfixture-intel-job/resolution",
                json={"end_client": "Real Bank", "website_url": "not a url"},
            )
        self.assertEqual(response.status_code, 422)
        resolve.assert_not_called()

    def test_put_resolution_accepts_a_bare_domain(self) -> None:
        with mock.patch("app.routers.intel.resolve_company") as resolve:
            response = self.client.put(
                "/intel/jobs/zzfixture-intel-job/resolution",
                json={"website_url": "bank.example"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            resolve.call_args.kwargs["override"].website_url, "bank.example"
        )

    def test_put_resolution_with_nothing_is_422(self) -> None:
        response = self.client.put(
            "/intel/jobs/zzfixture-intel-job/resolution", json={}
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
