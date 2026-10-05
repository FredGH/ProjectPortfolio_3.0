"""Router tests for /tailoring. Run in the API container (needs docling via
app.main): `docker exec -w /app/packages/core job_search-api-1 python -m
unittest tests.integration.test_tailoring_router`.

Uses the real app with `get_current_user_id` and `get_llm_adapters`
overridden — a real ASGI request, fake LLMs only. Starlette's TestClient
runs background tasks before returning, so a POST /runs response is
followed by a finished run.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.tailoring_fixtures import bullet_id, make_truth_base

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.dependencies import get_llm_adapters  # noqa: E402
from app.main import app  # noqa: E402

from core.cv.store import write_truth_base  # noqa: E402
from core.db.session import get_current_user_id  # noqa: E402
from core.llm.types import LLMResponse  # noqa: E402
from core.tailoring.store import (  # noqa: E402
    StaleDecisionError,
    create_run,
    set_progress,
)

_JOB = "zzfixture-tlr-api-1"
_NO_TITLE_JOB = "zzfixture-tlr-api-2"


class _Claude:
    """One fake `anthropic` adapter serving both the Tailor and the critic.

    The live config routes both tasks to Anthropic, so a single adapter
    answers; the critic prompt contains the phrase "JSON list" and the Tailor
    prompt does not.
    """

    def __init__(
        self, tailor_reply: str, unsupported: frozenset[str] = frozenset()
    ) -> None:
        self.tailor_reply = tailor_reply
        self.unsupported = unsupported

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        if "JSON list" not in prompt:
            return LLMResponse(
                text=self.tailor_reply,
                provider="anthropic",
                model=model,
                input_tokens=1,
                output_tokens=1,
            )
        ids = re.findall(r'"id": "([^"]+)"', prompt)
        return LLMResponse(
            text=json.dumps(
                {
                    "verdicts": [
                        {
                            "id": i,
                            "supported": i not in self.unsupported,
                            "issue": "adds a claim" if i in self.unsupported else "",
                        }
                        for i in ids
                    ],
                    "stretch": {"is_stretch": False, "reason": ""},
                }
            ),
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestTailoringRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.other_user = uuid.uuid4()
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        with self.owner.begin() as conn:
            for user_id in (self.user_id, self.other_user):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'zzfixture api user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job "
                    "(job_group_id, title_for_display, company, description) VALUES "
                    "(:j1, 'Lead Data Engineer', 'Gamma', 'Own the platform.'), "
                    "(:j2, NULL, 'NoTitleCo', 'x')"
                ),
                {"j1": _JOB, "j2": _NO_TITLE_JOB},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score "
                    "(user_id, job_group_id, hard_filter_passed, final_score) "
                    "VALUES (:u, :j, true, 0.8)"
                ),
                {"u": self.user_id, "j": _JOB},
            )
        self._set_replies(self._clean_reply())
        app.dependency_overrides[get_current_user_id] = lambda: self.user_id
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_llm_adapters, None)
        with self.owner.begin() as conn:
            for user_id in (self.user_id, self.other_user):
                conn.execute(
                    text("DELETE FROM tailoring.tailored_cv WHERE user_id = :u"),
                    {"u": user_id},
                )
                conn.execute(
                    text("DELETE FROM scoring.job_score WHERE user_id = :u"),
                    {"u": user_id},
                )
                conn.execute(
                    text("DELETE FROM cv_truth_base_history WHERE user_id = :u"),
                    {"u": user_id},
                )
                conn.execute(
                    text("DELETE FROM cv_truth_base WHERE user_id = :u"), {"u": user_id}
                )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id LIKE 'zzfixture-tlr-api-%'"
                )
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user_id, "b": self.other_user},
            )

    def _clean_reply(self) -> str:
        return json.dumps(
            {
                "summary": {"text": "Data engineer.", "evidence_refs": [self.ref0]},
                "experience": [
                    {
                        "truth_index": 0,
                        "bullets": [
                            {
                                "text": "Built dbt models powering risk reporting",
                                "evidence_refs": [self.ref0],
                            }
                        ],
                    }
                ],
                "skills": ["dbt"],
            }
        )

    def _orphan_reply(self) -> str:
        return json.dumps(
            {
                "experience": [
                    {
                        "truth_index": 0,
                        "bullets": [{"text": "Led a team of 12", "evidence_refs": []}],
                    }
                ],
                "skills": ["dbt"],
            }
        )

    def _set_replies(
        self, tailor_reply: str, unsupported: frozenset[str] = frozenset()
    ) -> None:
        adapters = {"anthropic": _Claude(tailor_reply, unsupported)}
        app.dependency_overrides[get_llm_adapters] = lambda: adapters

    def _store_cv(self) -> None:
        write_truth_base(self.app_engine, self.user_id, "md", self.truth_base)

    def _start(self, job_group_id: str = _JOB):
        return self.client.post("/tailoring/runs", json={"job_group_id": job_group_id})

    # --- candidates ------------------------------------------------------

    def test_candidates_lists_the_users_scored_jobs(self) -> None:
        response = self.client.get("/tailoring/candidates")
        self.assertEqual(response.status_code, 200)
        mine = [c for c in response.json() if c["job_group_id"] == _JOB]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["title_for_display"], "Lead Data Engineer")
        self.assertIsNone(mine[0]["latest_status"])

    def test_latest_run_works_for_an_unscored_job(self) -> None:
        self._store_cv()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job "
                    "(job_group_id, title_for_display, company, description) "
                    "VALUES ('zzfixture-tlr-api-3', 'Data Lead', 'Delta', 'x')"
                )
            )
        run_id = self._start("zzfixture-tlr-api-3").json()["run_id"]
        latest = self.client.get("/tailoring/jobs/zzfixture-tlr-api-3/latest-run")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["run_id"], run_id)

    def test_another_users_latest_run_is_404(self) -> None:
        self._store_cv()
        self._start()
        app.dependency_overrides[get_current_user_id] = lambda: self.other_user
        self.assertEqual(
            self.client.get(f"/tailoring/jobs/{_JOB}/latest-run").status_code, 404
        )

    def test_another_users_orphan_is_404_and_undecidable(self) -> None:
        body = self._orphan_run()
        orphan_id = body["orphans"][0]["id"]
        app.dependency_overrides[get_current_user_id] = lambda: self.other_user
        response = self.client.post(
            f"/tailoring/orphans/{orphan_id}/decision", json={"action": "reject"}
        )
        self.assertEqual(response.status_code, 404)
        app.dependency_overrides[get_current_user_id] = lambda: self.user_id
        after = self.client.get(f"/tailoring/runs/{body['run_id']}").json()
        self.assertEqual(after["orphans"][0]["status"], "pending")

    def test_candidates_limit_is_bounded(self) -> None:
        for limit in (0, 1000):
            response = self.client.get(f"/tailoring/candidates?limit={limit}")
            self.assertEqual(response.status_code, 422)

    # --- starting runs ---------------------------------------------------

    def test_a_run_without_a_cv_is_409(self) -> None:
        self.assertEqual(self._start().status_code, 409)

    def test_a_run_for_an_unknown_job_is_404(self) -> None:
        self._store_cv()
        self.assertEqual(self._start("zzfixture-tlr-api-nope").status_code, 404)

    def test_a_run_for_a_job_with_no_title_is_422(self) -> None:
        self._store_cv()
        response = self._start(_NO_TITLE_JOB)
        self.assertEqual(response.status_code, 422)
        self.assertIn("title", response.json()["detail"])

    def test_no_anthropic_key_is_503_and_creates_no_run(self) -> None:
        self._store_cv()
        adapters: dict = {}
        app.dependency_overrides[get_llm_adapters] = lambda: adapters
        response = self._start()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "the fabrication critic needs an Anthropic API key "
            "(set ANTHROPIC_API_KEY)",
        )
        with self.owner.begin() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM tailoring.tailored_cv WHERE user_id = :u"),
                {"u": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 0)

    def test_a_started_run_returns_202_and_finishes_approved(self) -> None:
        self._store_cv()
        response = self._start()
        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run_id"]
        body = self.client.get(f"/tailoring/runs/{run_id}").json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["target_title"], "Lead Data Engineer")
        self.assertEqual(body["document"]["headline"], "Lead Data Engineer")
        self.assertEqual(body["orphans"], [])

    def test_a_generating_run_exposes_progress_and_timestamps(self) -> None:
        self._store_cv()
        run_id = create_run(
            self.app_engine,
            self.user_id,
            job_group_id=_JOB,
            truth_base_version=1,
            target_title="Lead Data Engineer",
        )
        progress = {"phase": "tailoring", "message": "m", "history": ["h"]}
        set_progress(self.app_engine, self.user_id, run_id, progress)
        body = self.client.get(f"/tailoring/runs/{run_id}").json()
        self.assertEqual(body["status"], "generating")
        self.assertEqual(body["progress"], progress)
        started = datetime.fromisoformat(body["started_at"])
        updated = datetime.fromisoformat(body["updated_at"])
        self.assertGreaterEqual(updated, started)

    def test_a_finished_run_has_no_progress(self) -> None:
        self._store_cv()
        run_id = self._start().json()["run_id"]
        body = self.client.get(f"/tailoring/runs/{run_id}").json()
        self.assertEqual(body["status"], "approved")
        self.assertIsNone(body["progress"])
        datetime.fromisoformat(body["started_at"])
        datetime.fromisoformat(body["updated_at"])

    def test_the_run_response_lists_source_bullets_for_the_picker(self) -> None:
        self._store_cv()
        run_id = self._start().json()["run_id"]
        sources = self.client.get(f"/tailoring/runs/{run_id}").json()["sources"]
        self.assertIn(
            {
                "bullet_id": self.ref0,
                "text": "Built dbt models for risk reporting",
                "experience_index": 0,
                "role": "Senior Data Engineer at Acme Bank",
            },
            sources,
        )

    def test_latest_run_for_a_job(self) -> None:
        self._store_cv()
        self.assertEqual(
            self.client.get(f"/tailoring/jobs/{_JOB}/latest-run").status_code, 404
        )
        run_id = self._start().json()["run_id"]
        latest = self.client.get(f"/tailoring/jobs/{_JOB}/latest-run")
        self.assertEqual(latest.json()["run_id"], run_id)

    def test_another_users_run_is_404(self) -> None:
        self._store_cv()
        run_id = self._start().json()["run_id"]
        app.dependency_overrides[get_current_user_id] = lambda: self.other_user
        self.assertEqual(self.client.get(f"/tailoring/runs/{run_id}").status_code, 404)

    # --- orphan decisions ------------------------------------------------

    def _orphan_run(self) -> dict:
        self._store_cv()
        self._set_replies(self._orphan_reply())
        run_id = self._start().json()["run_id"]
        return self.client.get(f"/tailoring/runs/{run_id}").json()

    def test_an_orphan_run_needs_review_and_lists_the_orphan(self) -> None:
        body = self._orphan_run()
        self.assertEqual(body["status"], "needs_review")
        self.assertEqual(
            [(o["kind"], o["text"], o["status"]) for o in body["orphans"]],
            [("orphan", "Led a team of 12", "pending")],
        )

    def test_linking_an_orphan_approves_the_run(self) -> None:
        body = self._orphan_run()
        orphan_id = body["orphans"][0]["id"]
        response = self.client.post(
            f"/tailoring/orphans/{orphan_id}/decision",
            json={"action": "link", "evidence_ref": self.ref0},
        )
        self.assertEqual(response.status_code, 200)
        after = response.json()
        self.assertEqual(after["status"], "approved")
        self.assertEqual(after["orphans"][0]["status"], "linked")
        bullet = after["document"]["experience"][0]["bullets"][0]
        self.assertEqual(bullet["origin"], "linked")
        self.assertEqual(bullet["evidence_refs"], [self.ref0])

    def test_rejecting_an_orphan_removes_the_line_and_approves(self) -> None:
        body = self._orphan_run()
        orphan_id = body["orphans"][0]["id"]
        response = self.client.post(
            f"/tailoring/orphans/{orphan_id}/decision", json={"action": "reject"}
        )
        after = response.json()
        self.assertEqual(after["status"], "approved")
        self.assertEqual(after["document"]["experience"][0]["bullets"], [])

    def test_linking_to_another_roles_bullet_is_422(self) -> None:
        body = self._orphan_run()
        other_role = bullet_id(self.truth_base, 1, 0)
        response = self.client.post(
            f"/tailoring/orphans/{body['orphans'][0]['id']}/decision",
            json={"action": "link", "evidence_ref": other_role},
        )
        self.assertEqual(response.status_code, 422)

    def test_a_link_without_a_ref_is_422(self) -> None:
        body = self._orphan_run()
        response = self.client.post(
            f"/tailoring/orphans/{body['orphans'][0]['id']}/decision",
            json={"action": "link"},
        )
        self.assertEqual(response.status_code, 422)

    def test_deciding_twice_is_409(self) -> None:
        body = self._orphan_run()
        url = f"/tailoring/orphans/{body['orphans'][0]['id']}/decision"
        self.client.post(url, json={"action": "reject"})
        self.assertEqual(
            self.client.post(url, json={"action": "reject"}).status_code, 409
        )

    def test_a_stale_decision_is_409(self) -> None:
        body = self._orphan_run()
        url = f"/tailoring/orphans/{body['orphans'][0]['id']}/decision"
        with patch(
            "app.routers.tailoring.save_decision",
            side_effect=StaleDecisionError("changed since you opened it"),
        ):
            response = self.client.post(url, json={"action": "reject"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "changed since you opened it")

    def test_an_unknown_orphan_is_404(self) -> None:
        response = self.client.post(
            f"/tailoring/orphans/{uuid.uuid4()}/decision", json={"action": "reject"}
        )
        self.assertEqual(response.status_code, 404)

    # --- keyword coverage follows the decisions (I5) ----------------------

    def _add_job_skills(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.silver__bridge_job_skill "
                    "(job_group_id, skill_id, requirement_level, mention_count) "
                    "VALUES (:j, 'zzfixture-skill-dbt', 'must_have', 1), "
                    "(:j, 'zzfixture-skill-k8s', 'must_have', 1)"
                ),
                {"j": _JOB},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__skill "
                    "(skill_id, canonical_label, source) "
                    "VALUES ('zzfixture-skill-dbt', 'dbt', 'custom'), "
                    "('zzfixture-skill-k8s', 'Kubernetes', 'custom')"
                )
            )
        self.addCleanup(self._drop_job_skills)

    def _drop_job_skills(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM silver.silver__bridge_job_skill "
                    "WHERE job_group_id LIKE 'zzfixture-tlr-api-%'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__skill "
                    "WHERE skill_id LIKE 'zzfixture-skill-%'"
                )
            )

    def test_keyword_coverage_is_recomputed_after_each_decision(self) -> None:
        self._store_cv()
        self._add_job_skills()
        reply = json.dumps(
            {
                "experience": [
                    {
                        "truth_index": 0,
                        "bullets": [
                            {"text": "Led a team of 12", "evidence_refs": []},
                            {
                                "text": "Ran Kubernetes for dbt",
                                "evidence_refs": [self.ref0],
                            },
                        ],
                    }
                ],
                "skills": ["dbt"],
            }
        )
        self._set_replies(reply, unsupported=frozenset({"e0b1"}))
        run_id = self._start().json()["run_id"]
        body = self.client.get(f"/tailoring/runs/{run_id}").json()
        self.assertNotIn("Kubernetes", body["document"]["keyword_coverage"]["covered"])
        orphans = {o["kind"]: o for o in body["orphans"]}
        # Rejecting the orphan at b0 shifts the pending Kubernetes line from
        # b1 to b0; it must still be excluded at its new position.
        after = self.client.post(
            f"/tailoring/orphans/{orphans['orphan']['id']}/decision",
            json={"action": "reject"},
        ).json()
        self.assertEqual(after["status"], "needs_review")
        coverage = after["document"]["keyword_coverage"]
        self.assertNotIn("Kubernetes", coverage["covered"])
        self.assertIn("dbt", coverage["covered"])
        linked = self.client.post(
            f"/tailoring/orphans/{orphans['unsupported']['id']}/decision",
            json={"action": "link", "evidence_ref": self.ref0},
        ).json()
        self.assertEqual(linked["status"], "approved")
        self.assertIn("Kubernetes", linked["document"]["keyword_coverage"]["covered"])

    def test_an_unknown_action_is_422(self) -> None:
        body = self._orphan_run()
        response = self.client.post(
            f"/tailoring/orphans/{body['orphans'][0]['id']}/decision",
            json={"action": "shrug"},
        )
        self.assertEqual(response.status_code, 422)

    # --- cancel ----------------------------------------------------------

    def _generating_run(self, user_id: uuid.UUID | None = None) -> uuid.UUID:
        owner_id = user_id or self.user_id
        run_id = create_run(
            self.app_engine,
            owner_id,
            job_group_id=_JOB,
            truth_base_version=1,
            target_title="Lead Data Engineer",
        )
        set_progress(self.app_engine, owner_id, run_id, {"phase": "tailoring"})
        return run_id

    def test_cancelling_a_generating_run_returns_it_cancelled(self) -> None:
        self._store_cv()
        run_id = self._generating_run()
        response = self.client.post(f"/tailoring/runs/{run_id}/cancel")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "cancelled")
        self.assertIsNone(body["progress"])
        self.assertEqual(
            self.client.get(f"/tailoring/runs/{run_id}").json()["status"],
            "cancelled",
        )

    def test_cancelling_a_finished_run_is_a_409(self) -> None:
        self._store_cv()
        run_id = self._start().json()["run_id"]
        response = self.client.post(f"/tailoring/runs/{run_id}/cancel")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "this run is not running")
        self.assertEqual(
            self.client.get(f"/tailoring/runs/{run_id}").json()["status"],
            "approved",
        )

    def test_cancelling_an_unknown_run_is_a_404(self) -> None:
        response = self.client.post(f"/tailoring/runs/{uuid.uuid4()}/cancel")
        self.assertEqual(response.status_code, 404)

    def test_cancelling_another_users_run_is_a_404_and_changes_nothing(self) -> None:
        self._store_cv()
        run_id = self._generating_run(self.other_user)
        response = self.client.post(f"/tailoring/runs/{run_id}/cancel")
        self.assertEqual(response.status_code, 404)
        with self.owner.begin() as conn:
            status = conn.execute(
                text("SELECT status FROM tailoring.tailored_cv WHERE id = :r"),
                {"r": run_id},
            ).scalar_one()
        self.assertEqual(status, "generating")

    def test_cancelling_twice_is_a_409_the_second_time(self) -> None:
        self._store_cv()
        run_id = self._generating_run()
        self.assertEqual(
            self.client.post(f"/tailoring/runs/{run_id}/cancel").status_code, 200
        )
        self.assertEqual(
            self.client.post(f"/tailoring/runs/{run_id}/cancel").status_code, 409
        )


if __name__ == "__main__":
    unittest.main()
