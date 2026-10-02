"""Integration tests for the tailoring loop (real Postgres, fake LLMs)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.cv.store import write_truth_base
from core.llm.types import LLMResponse
from core.tailoring.checks import Problem
from core.tailoring.loop import (
    CriticUnavailableError,
    NoCvError,
    NoTargetTitleError,
    UnknownJobError,
    _orphan_drafts,
    execute_tailoring,
    run_tailoring,
    start_tailoring,
)
from core.tailoring.store import finish_run, read_run

_JOB = "zzfixture-tlr-loop-1"
_NO_TITLE_JOB = "zzfixture-tlr-loop-2"


class _Tailor:
    """Replays tailor replies in order (the last repeats) and records prompts."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LLMResponse(
            text=reply, provider="ollama", model=model, input_tokens=1, output_tokens=1
        )


class _Critic:
    """Marks the given item ids unsupported and everything else supported."""

    def __init__(self, unsupported: set[str] | None = None, reply: str | None = None):
        self.unsupported = unsupported or set()
        self.reply = reply
        self.calls = 0

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.calls += 1
        if self.reply is not None:
            return LLMResponse(
                text=self.reply,
                provider="anthropic",
                model=model,
                input_tokens=1,
                output_tokens=1,
            )
        ids = re.findall(r'"id": "([^"]+)"', prompt)
        verdicts = [
            {
                "id": i,
                "supported": i not in self.unsupported,
                "issue": "adds a claim" if i in self.unsupported else "",
            }
            for i in ids
        ]
        return LLMResponse(
            text=json.dumps(
                {"verdicts": verdicts, "stretch": {"is_stretch": False, "reason": ""}}
            ),
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestTailoringLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        self.ref1 = bullet_id(self.truth_base, 0, 1)
        self.ref_old = bullet_id(self.truth_base, 1, 0)
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture loop user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job "
                    "(job_group_id, title_for_display, company, description) VALUES "
                    "(:j1, 'Lead Data Engineer', 'Gamma', 'Own the data platform.'), "
                    "(:j2, NULL, 'NoTitleCo', 'No title here.')"
                ),
                {"j1": _JOB, "j2": _NO_TITLE_JOB},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__bridge_job_skill "
                    "(job_group_id, skill_id, requirement_level, mention_count) "
                    "VALUES (:j, 'zzfixture-skill-sql', 'must_have', 2), "
                    "(:j, 'zzfixture-skill-k8s', 'must_have', 1)"
                ),
                {"j": _JOB},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__skill "
                    "(skill_id, canonical_label, source) "
                    "VALUES ('zzfixture-skill-sql', 'SQL', 'custom'), "
                    "('zzfixture-skill-k8s', 'Kubernetes', 'custom')"
                )
            )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM tailoring.tailored_cv WHERE user_id = :u"),
                {"u": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :u"),
                {"u": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :u"),
                {"u": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__bridge_job_skill "
                    "WHERE job_group_id LIKE 'zzfixture-tlr-loop-%'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__skill "
                    "WHERE skill_id LIKE 'zzfixture-skill-%'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id LIKE 'zzfixture-tlr-loop-%'"
                )
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :u"), {"u": self.user_id}
            )

    def _store_cv(self) -> None:
        write_truth_base(
            self.app_engine, self.user_id, "zzfixture markdown", self.truth_base
        )

    def _reply(
        self,
        bullets: list[dict],
        skills: list[str] | None = None,
        *,
        drop_older_role: bool = False,
    ) -> str:
        """Build a Tailor reply.

        Args:
            bullets: The bullets for role 0.
            skills: Skill names to show; defaults to all three.
            drop_older_role: Also send role 1 with no bullets. An omitted
                role keeps its original bullets (one mentions SQL), so a
                test about SQL being *missing* must empty it explicitly.

        Returns:
            The reply as JSON text.
        """
        experience = [{"truth_index": 0, "bullets": bullets}]
        if drop_older_role:
            experience.append({"truth_index": 1, "bullets": []})
        return json.dumps(
            {
                "summary": {"text": "Data engineer.", "evidence_refs": [self.ref0]},
                "experience": experience,
                "skills": skills if skills is not None else ["dbt", "Airflow", "SQL"],
            }
        )

    def _clean_bullets(self) -> list[dict]:
        return [
            {
                "text": "Built dbt models powering risk reporting",
                "evidence_refs": [self.ref0],
            },
            {
                "text": "Migrated nightly batch jobs to Airflow",
                "evidence_refs": [self.ref1],
            },
        ]

    def _run(self, tailor, critic, **kwargs):
        return run_tailoring(
            self.app_engine,
            self.user_id,
            _JOB,
            adapters={"ollama": tailor, "anthropic": critic},
            **kwargs,
        )

    # --- happy path ------------------------------------------------------

    def test_a_clean_first_attempt_is_approved(self) -> None:
        self._store_cv()
        tailor = _Tailor([self._reply(self._clean_bullets())])
        critic = _Critic()
        outcome = self._run(tailor, critic)
        self.assertEqual((outcome.status, outcome.attempts), ("approved", 1))
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(run.orphans, [])
        self.assertEqual(run.document.headline, "Lead Data Engineer")
        self.assertEqual(run.tailor_prompt_version, "local.v1")
        self.assertEqual(run.critic_prompt_version, "claude.v1")
        self.assertEqual(len(tailor.prompts), 1)
        self.assertEqual(critic.calls, 1)

    def test_keyword_coverage_is_recorded_and_gaps_alone_never_block(self) -> None:
        self._store_cv()
        outcome = self._run(
            _Tailor([self._reply(self._clean_bullets(), skills=["dbt"])]), _Critic()
        )
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(
            run.document.keyword_coverage.missing_unevidenced, ["Kubernetes"]
        )
        self.assertEqual(run.status, "approved")

    def test_untraced_lines_never_count_as_keyword_coverage(self) -> None:
        # I5: an orphan line and a critic-rejected line both mention
        # Kubernetes; neither may make it "covered" in the persisted run.
        self._store_cv()
        bullets = [
            {"text": "Ran Kubernetes for dbt models", "evidence_refs": [self.ref0]},
            {"text": "Led Kubernetes migrations", "evidence_refs": []},
        ]
        outcome = self._run(
            _Tailor([self._reply(bullets, skills=["dbt"])]),
            _Critic(unsupported={"e0b0"}),
        )
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(sorted(o.kind for o in run.orphans), ["orphan", "unsupported"])
        coverage = run.document.keyword_coverage
        self.assertNotIn("Kubernetes", coverage.covered)
        self.assertEqual(coverage.missing_unevidenced, ["Kubernetes"])

    # --- retries ---------------------------------------------------------

    def test_an_uncited_bullet_is_retried_with_feedback_then_approved(self) -> None:
        self._store_cv()
        bad = self._clean_bullets() + [
            {"text": "Led a team of 12", "evidence_refs": []}
        ]
        tailor = _Tailor([self._reply(bad), self._reply(self._clean_bullets())])
        outcome = self._run(tailor, _Critic())
        self.assertEqual((outcome.status, outcome.attempts), ("approved", 2))
        self.assertIn("Fix these problems", tailor.prompts[1])
        self.assertIn("Led a team of 12", tailor.prompts[1])

    def test_an_evidenced_missing_keyword_is_fed_back(self) -> None:
        self._store_cv()
        first = self._reply(self._clean_bullets(), skills=["dbt"], drop_older_role=True)
        second = self._reply(self._clean_bullets(), skills=["dbt", "SQL"])
        tailor = _Tailor([first, second])
        outcome = self._run(tailor, _Critic())
        self.assertEqual(outcome.attempts, 2)
        # The whole prompt lists the job's skills; only the feedback block
        # (after "Fix these problems") must name SQL and never Kubernetes.
        feedback_block = tailor.prompts[1].split("Fix these problems")[1]
        self.assertIn("SQL", feedback_block)
        self.assertNotIn("Kubernetes", feedback_block)

    def test_the_critic_is_skipped_on_a_non_final_attempt_with_code_problems(
        self,
    ) -> None:
        self._store_cv()
        # An unknown id is a code problem (a bullet with no refs at all is
        # only an orphan *origin*, which does not stop the critic running).
        bad = [{"text": "Invented", "evidence_refs": ["nope"]}]
        tailor = _Tailor([self._reply(bad), self._reply(self._clean_bullets())])
        critic = _Critic()
        self._run(tailor, critic)
        self.assertEqual(critic.calls, 1)  # only on the clean attempt

    # --- surfaced, never approved ----------------------------------------

    def test_an_orphan_that_survives_every_retry_is_surfaced_not_approved(self) -> None:
        self._store_cv()
        bad = self._clean_bullets() + [
            {"text": "Led a team of 12", "evidence_refs": []}
        ]
        tailor = _Tailor([self._reply(bad)])
        outcome = self._run(tailor, _Critic())
        self.assertEqual((outcome.status, outcome.attempts), ("needs_review", 3))
        self.assertEqual(len(tailor.prompts), 3)  # first try + two retries, no more
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(
            [(o.kind, o.text, o.status) for o in run.orphans],
            [("orphan", "Led a team of 12", "pending")],
        )
        self.assertEqual(
            (run.orphans[0].experience_index, run.orphans[0].bullet_index), (0, 2)
        )

    def test_a_critic_rejection_becomes_an_unsupported_orphan(self) -> None:
        self._store_cv()
        tailor = _Tailor([self._reply(self._clean_bullets())])
        outcome = self._run(tailor, _Critic(unsupported={"e0b0"}))
        self.assertEqual(outcome.status, "needs_review")
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(
            [(o.kind, o.issue) for o in run.orphans], [("unsupported", "adds a claim")]
        )
        self.assertEqual(run.orphans[0].claimed_refs, [self.ref0])

    def test_a_cross_role_citation_survives_as_an_orphan(self) -> None:
        # Review Focus 1.
        self._store_cv()
        bad = self._clean_bullets() + [
            {"text": "Wrote SQL reports", "evidence_refs": [self.ref_old]}
        ]
        outcome = self._run(_Tailor([self._reply(bad)]), _Critic())
        self.assertEqual(outcome.status, "needs_review")
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual([o.text for o in run.orphans], ["Wrote SQL reports"])
        self.assertIn("another role", run.orphans[0].issue)

    def test_the_stretch_judgement_is_stored_on_the_document(self) -> None:
        self._store_cv()
        critic = _Critic(
            reply=json.dumps(
                {
                    "verdicts": [
                        {"id": "summary", "supported": True, "issue": ""},
                        {"id": "e0b0", "supported": True, "issue": ""},
                        {"id": "e0b1", "supported": True, "issue": ""},
                    ],
                    "stretch": {"is_stretch": True, "reason": "senior title"},
                }
            )
        )
        outcome = self._run(_Tailor([self._reply(self._clean_bullets())]), critic)
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertTrue(run.document.stretch.is_stretch)
        self.assertEqual(run.status, "approved")  # advisory, not a failure

    # --- failures fail closed --------------------------------------------

    def test_a_critic_that_omits_a_verdict_does_not_approve(self) -> None:
        # Review Focus 2.
        self._store_cv()
        # The summary and a reworded bullet are judged; the critic answers none.
        critic = _Critic(
            reply=json.dumps(
                {"verdicts": [], "stretch": {"is_stretch": False, "reason": ""}}
            )
        )
        outcome = self._run(_Tailor([self._reply(self._clean_bullets())]), critic)
        self.assertEqual(outcome.status, "needs_review")
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual([o.kind for o in run.orphans], ["unsupported", "unsupported"])

    def test_an_unparseable_critic_reply_fails_the_run(self) -> None:
        self._store_cv()
        outcome = self._run(
            _Tailor([self._reply(self._clean_bullets())]), _Critic(reply="lgtm")
        )
        self.assertEqual((outcome.status, outcome.attempts), ("failed", 1))
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(run.attempts, 1)
        self.assertIsNone(run.document)
        self.assertIn("critic", run.error_message)

    def test_malformed_tailor_output_on_every_attempt_fails_the_run(self) -> None:
        # Review Focus 4.
        self._store_cv()
        tailor = _Tailor(["this is not json"])
        outcome = self._run(tailor, _Critic())
        self.assertEqual((outcome.status, outcome.attempts), ("failed", 3))
        self.assertEqual(
            read_run(self.app_engine, self.user_id, outcome.run_id).attempts, 3
        )
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertIn("unusable Tailor reply", run.error_message)
        self.assertEqual(run.orphans, [])

    def test_a_tailor_that_recovers_after_a_malformed_reply_succeeds(self) -> None:
        self._store_cv()
        tailor = _Tailor(["not json", self._reply(self._clean_bullets())])
        outcome = self._run(tailor, _Critic())
        self.assertEqual((outcome.status, outcome.attempts), ("approved", 2))

    # --- an earlier usable attempt is never thrown away (I1, I2) ----------

    def test_a_final_unparseable_reply_persists_the_earlier_attempt(self) -> None:
        self._store_cv()
        # Attempt 1 has an orphan (so it is retried); attempts 2 and 3 are
        # unparseable. The run must persist attempt 1, judged, not fail.
        bad = self._clean_bullets() + [
            {"text": "Led a team of 12", "evidence_refs": []}
        ]
        tailor = _Tailor([self._reply(bad), "not json", "not json"])
        critic = _Critic()
        outcome = self._run(tailor, critic)
        self.assertEqual((outcome.status, outcome.attempts), ("needs_review", 3))
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertIsNotNone(run.document)
        self.assertEqual(run.attempts, 3)
        self.assertEqual(
            [(o.kind, o.text) for o in run.orphans], [("orphan", "Led a team of 12")]
        )
        self.assertEqual(critic.calls, 1)

    def test_an_unjudged_earlier_attempt_is_judged_before_it_is_persisted(
        self,
    ) -> None:
        self._store_cv()
        # Attempt 1 has a code problem, so the critic is skipped on it; the
        # later replies are unusable. Attempt 1's reworded line must still
        # be judged (here: rejected) before it is persisted.
        bad = [
            {
                "text": "Built dbt models powering risk reporting",
                "evidence_refs": [self.ref0],
            },
            {"text": "Invented", "evidence_refs": ["nope"]},
        ]
        tailor = _Tailor([self._reply(bad), "not json"])
        critic = _Critic(unsupported={"e0b0"})
        outcome = self._run(tailor, critic)
        self.assertEqual((outcome.status, outcome.attempts), ("needs_review", 3))
        self.assertEqual(critic.calls, 1)
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(
            [(o.bullet_index, o.kind) for o in run.orphans],
            [(0, "unsupported"), (1, "orphan")],
        )
        self.assertEqual(run.critic_prompt_version, "claude.v1")

    def test_a_clean_attempt_is_kept_when_the_keyword_retry_adds_an_orphan(
        self,
    ) -> None:
        self._store_cv()
        first = self._reply(self._clean_bullets(), skills=["dbt"], drop_older_role=True)
        worse = self._reply(
            self._clean_bullets() + [{"text": "Led a team of 12", "evidence_refs": []}],
            skills=["dbt", "SQL"],
        )
        tailor = _Tailor([first, worse])
        outcome = self._run(tailor, _Critic())
        self.assertEqual(outcome.status, "approved")
        self.assertGreaterEqual(len(tailor.prompts), 2)  # the keyword retry ran
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(run.orphans, [])
        texts = [b.text for b in run.document.experience[0].bullets]
        self.assertNotIn("Led a team of 12", texts)
        self.assertEqual(run.document.experience[1].bullets, [])  # attempt 1's

    def test_a_clean_attempt_is_kept_when_the_keyword_retry_is_unparseable(
        self,
    ) -> None:
        self._store_cv()
        first = self._reply(self._clean_bullets(), skills=["dbt"], drop_older_role=True)
        tailor = _Tailor([first, "not json"])
        outcome = self._run(tailor, _Critic())
        self.assertEqual(outcome.status, "approved")
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertIsNotNone(run.document)
        self.assertEqual(run.orphans, [])
        self.assertEqual(
            run.document.keyword_coverage.missing_evidenced, ["SQL"]
        )  # attempt 1's document

    def test_a_critic_routed_to_a_weaker_model_fails_the_run_without_a_call(
        self,
    ) -> None:
        self._store_cv()
        tailor = _Tailor([self._reply(self._clean_bullets())])
        critic = _Critic()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.yml"
            path.write_text(
                "tasks:\n"
                "  cv_tailoring:\n    provider: ollama\n    model: m\n"
                "    prompt_family: local\n"
                "  fabrication_critic:\n    provider: ollama\n    model: m\n"
                "    prompt_family: claude\n"
            )
            outcome = self._run(tailor, critic, config_path=path)
        self.assertEqual((outcome.status, outcome.attempts), ("failed", 1))
        self.assertEqual(critic.calls, 0)
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(run.attempts, 1)
        self.assertIn("anthropic", run.error_message)

    # --- the critic always runs on the final attempt ----------------------

    def test_the_critic_runs_on_the_final_attempt_despite_code_problems(self) -> None:
        self._store_cv()
        # Every attempt: e0b1 has an unknown id (code problem); e0b0 is a
        # reworded line the critic rejects. The critic must still be asked,
        # once, on the final attempt only.
        bad = [
            {
                "text": "Built dbt models powering risk reporting",
                "evidence_refs": [self.ref0],
            },
            {"text": "Invented", "evidence_refs": ["nope"]},
        ]
        critic = _Critic(unsupported={"e0b0"})
        outcome = self._run(_Tailor([self._reply(bad)]), critic)
        self.assertEqual((outcome.status, outcome.attempts), ("needs_review", 3))
        self.assertEqual(critic.calls, 1)
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(
            [(o.bullet_index, o.kind) for o in run.orphans],
            [(0, "unsupported"), (1, "orphan")],
        )

    def test_a_critic_rejection_wins_over_a_code_problem_on_the_same_line(self) -> None:
        self._store_cv()
        bad = [
            {
                "text": "Built dbt models powering risk reporting",
                "evidence_refs": [self.ref0, "nope"],
            },
            {
                "text": "Migrated nightly batch jobs to Airflow",
                "evidence_refs": [self.ref1],
            },
        ]
        outcome = self._run(_Tailor([self._reply(bad)]), _Critic(unsupported={"e0b0"}))
        self.assertEqual(outcome.status, "needs_review")
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(
            [(o.bullet_index, o.kind) for o in run.orphans], [(0, "unsupported")]
        )

    def test_a_problem_on_an_unresolvable_location_raises_not_drops(self) -> None:
        doc = SimpleNamespace(summary=None, experience=[])
        for location in ("headline", "e0", "e5b0", "summary"):
            with self.assertRaises(RuntimeError):
                _orphan_drafts(doc, [Problem("evidence_missing", "m", location)], None)

    def test_a_failure_before_any_tailor_call_records_zero_attempts(self) -> None:
        self._store_cv()
        run_id = start_tailoring(self.app_engine, self.user_id, _JOB)
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM silver.silver__bridge_job_skill "
                    "WHERE job_group_id = :j"
                ),
                {"j": _JOB},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = :j"), {"j": _JOB}
            )
        tailor = _Tailor([self._reply(self._clean_bullets())])
        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={"ollama": tailor, "anthropic": _Critic()},
        )
        self.assertEqual((outcome.status, outcome.attempts), ("failed", 0))
        self.assertEqual(tailor.prompts, [])
        self.assertEqual(read_run(self.app_engine, self.user_id, run_id).attempts, 0)

    # --- a finished run is never re-run or overwritten (M-finish) --------

    def test_executing_an_already_finished_run_does_not_re_tailor(self) -> None:
        self._store_cv()
        outcome = self._run(_Tailor([self._reply(self._clean_bullets())]), _Critic())
        before = read_run(self.app_engine, self.user_id, outcome.run_id)
        tailor = _Tailor([self._reply(self._clean_bullets())])
        critic = _Critic()
        again = execute_tailoring(
            self.app_engine,
            self.user_id,
            outcome.run_id,
            adapters={"ollama": tailor, "anthropic": critic},
        )
        self.assertEqual((again.status, again.attempts), ("approved", 1))
        self.assertEqual((tailor.prompts, critic.calls), ([], 0))
        after = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(
            (after.status, after.document, after.orphans),
            (before.status, before.document, before.orphans),
        )

    def test_a_run_finished_meanwhile_is_not_overwritten(self) -> None:
        # Another worker finishes the run while this one is tailoring: the
        # late finish is refused, nothing is overwritten or duplicated, and
        # the background task does not crash.
        self._store_cv()
        run_id = start_tailoring(self.app_engine, self.user_id, _JOB)
        bad = self._clean_bullets() + [
            {"text": "Led a team of 12", "evidence_refs": []}
        ]
        app_engine, user_id = self.app_engine, self.user_id

        class _RacingTailor(_Tailor):
            def complete(self, **kwargs: object) -> LLMResponse:
                if not self.prompts:
                    finish_run(
                        app_engine,
                        user_id,
                        run_id,
                        status="failed",
                        document=None,
                        orphans=[],
                        attempts=0,
                        error_message="finished elsewhere",
                    )
                return super().complete(**kwargs)

        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={
                "ollama": _RacingTailor([self._reply(bad)]),
                "anthropic": _Critic(),
            },
        )
        self.assertEqual(outcome.status, "failed")
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.error_message, "finished elsewhere")
        self.assertEqual(run.orphans, [])
        self.assertIsNone(run.document)

    # --- no Anthropic key (I3) -------------------------------------------

    def _run_count(self) -> int:
        with self.owner.begin() as conn:
            return conn.execute(
                text("SELECT count(*) FROM tailoring.tailored_cv WHERE user_id = :u"),
                {"u": self.user_id},
            ).scalar_one()

    def test_no_anthropic_adapter_refuses_to_start_without_a_tailor_call(
        self,
    ) -> None:
        self._store_cv()
        tailor = _Tailor([self._reply(self._clean_bullets())])
        with self.assertRaises(CriticUnavailableError) as ctx:
            run_tailoring(
                self.app_engine, self.user_id, _JOB, adapters={"ollama": tailor}
            )
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))
        self.assertEqual(tailor.prompts, [])
        self.assertEqual(self._run_count(), 0)

    def test_executing_without_an_anthropic_adapter_fails_with_a_clear_message(
        self,
    ) -> None:
        self._store_cv()
        run_id = start_tailoring(self.app_engine, self.user_id, _JOB)
        tailor = _Tailor([self._reply(self._clean_bullets())])
        outcome = execute_tailoring(
            self.app_engine, self.user_id, run_id, adapters={"ollama": tailor}
        )
        self.assertEqual((outcome.status, outcome.attempts), ("failed", 0))
        self.assertEqual(tailor.prompts, [])
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertIn(
            "the fabrication critic needs an Anthropic API key "
            "(set ANTHROPIC_API_KEY)",
            run.error_message,
        )

    # --- preconditions ---------------------------------------------------

    def test_a_user_with_no_cv_cannot_start(self) -> None:
        # Review Focus 4.
        with self.assertRaises(NoCvError):
            start_tailoring(self.app_engine, self.user_id, _JOB)

    def test_an_unknown_job_cannot_start(self) -> None:
        self._store_cv()
        with self.assertRaises(UnknownJobError):
            start_tailoring(self.app_engine, self.user_id, "zzfixture-tlr-loop-nope")

    def test_a_job_with_no_title_cannot_start(self) -> None:
        # Review Focus 3.
        self._store_cv()
        with self.assertRaises(NoTargetTitleError):
            start_tailoring(self.app_engine, self.user_id, _NO_TITLE_JOB)

    def test_a_blank_title_cannot_start(self) -> None:
        self._store_cv()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "UPDATE gold.dim_job SET title_for_display = '   ' "
                    "WHERE job_group_id = :j"
                ),
                {"j": _JOB},
            )
        with self.assertRaises(NoTargetTitleError):
            start_tailoring(self.app_engine, self.user_id, _JOB)

    def test_start_creates_a_generating_run_and_execute_finishes_it(self) -> None:
        self._store_cv()
        run_id = start_tailoring(self.app_engine, self.user_id, _JOB)
        self.assertEqual(
            read_run(self.app_engine, self.user_id, run_id).status, "generating"
        )
        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={
                "ollama": _Tailor([self._reply(self._clean_bullets())]),
                "anthropic": _Critic(),
            },
        )
        self.assertEqual(outcome.run_id, run_id)
        self.assertEqual(
            read_run(self.app_engine, self.user_id, run_id).status, "approved"
        )

    def test_the_run_uses_the_truth_base_version_it_started_with(self) -> None:
        self._store_cv()
        run_id = start_tailoring(self.app_engine, self.user_id, _JOB)
        edited = make_truth_base()
        edited.experience[0].company = "Changed After Start"
        write_truth_base(self.app_engine, self.user_id, "zzfixture markdown", edited)
        execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={
                "ollama": _Tailor([self._reply(self._clean_bullets())]),
                "anthropic": _Critic(),
            },
        )
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.document.experience[0].company, "Acme Bank")


if __name__ == "__main__":
    unittest.main()
