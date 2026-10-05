"""Integration tests for the tailoring loop (real Postgres, fake LLMs)."""

from __future__ import annotations

import contextlib
import json
import re
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.tailoring_fixtures import (
    bullet_id,
    forbid_real_ollama,
    make_truth_base,
    write_pinned_task_config,
)

from core.cv.store import write_truth_base
from core.llm.types import LLMResponse
from core.tailoring import loop as loop_mod
from core.tailoring.checks import Problem
from core.tailoring.loop import (
    CriticUnavailableError,
    NoCvError,
    NoTargetTitleError,
    UnknownBackendError,
    UnknownJobError,
    _orphan_drafts,
    start_tailoring,
)
from core.tailoring.store import cancel_run, create_run, finish_run, read_run

_JOB = "zzfixture-tlr-loop-1"
_NO_TITLE_JOB = "zzfixture-tlr-loop-2"

_RELEASE = threading.Event()
"""Set in `addCleanup` so a deliberately blocked fake call ends with its test."""


@contextlib.contextmanager
def _injected_ollama(adapters: dict):
    """Make the loop's per-run Ollama adapter be the test's fake one.

    A local backend builds its own adapter around a dedicated client; tests
    that inject a fake `ollama` adapter swap that construction out. (The
    backend tests in `TestTailoringBackends` do not use this: they run the
    real adapter over a mock transport.)

    Args:
        adapters: The adapters the test passes to the loop.

    Yields:
        None, while the patches are active.
    """
    fake = adapters.get("ollama")
    real = loop_mod.OllamaAdapter
    with (
        mock.patch.object(loop_mod, "_new_ollama_client", lambda: httpx.Client()),
        mock.patch.object(loop_mod, "_unload_in_background"),
        mock.patch.object(
            loop_mod,
            "OllamaAdapter",
            lambda **kw: fake if fake is not None else real(**kw),
        ),
    ):
        yield


def execute_tailoring(*args, adapters, **kwargs):
    with _injected_ollama(adapters):
        return loop_mod.execute_tailoring(*args, adapters=adapters, **kwargs)


def run_tailoring(*args, adapters, **kwargs):
    with _injected_ollama(adapters):
        return loop_mod.run_tailoring(*args, adapters=adapters, **kwargs)


class _Tailor:
    """Replays tailor replies in order (the last repeats) and records prompts."""

    def __init__(self, replies: list[str], tokens: tuple[int, int] = (1, 1)) -> None:
        self.replies = list(replies)
        self.tokens = tokens
        self.prompts: list[str] = []

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LLMResponse(
            text=reply,
            provider="ollama",
            model=model,
            input_tokens=self.tokens[0],
            output_tokens=self.tokens[1],
        )


class _Critic:
    """Marks the given item ids unsupported and everything else supported."""

    def __init__(
        self,
        unsupported: set[str] | None = None,
        reply: str | None = None,
        tokens: tuple[int, int] = (1, 1),
    ):
        self.unsupported = unsupported or set()
        self.reply = reply
        self.tokens = tokens
        self.calls = 0

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.calls += 1
        if self.reply is not None:
            return LLMResponse(
                text=self.reply,
                provider="anthropic",
                model=model,
                input_tokens=self.tokens[0],
                output_tokens=self.tokens[1],
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
            input_tokens=self.tokens[0],
            output_tokens=self.tokens[1],
        )


class _LoopFixtures(unittest.TestCase):
    """Shared fixtures: a user, a CV, a job and fake LLM plumbing."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        forbid_real_ollama(self)
        _RELEASE.clear()
        self.addCleanup(_RELEASE.set)
        config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(config_dir.cleanup)
        self.config_path = write_pinned_task_config(config_dir.name)
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
            **{"config_path": self.config_path, **kwargs},
        )


class TestTailoringLoop(_LoopFixtures):
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
        self.assertEqual(run.tailor_prompt_version, "local.v2")
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
        # A failed run logs its traceback so the failing line can be found.
        with self.assertLogs("core.tailoring.loop", level="ERROR") as logged:
            outcome = self._run(tailor, _Critic())
        self.assertTrue(any("Traceback" in line for line in logged.output))
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
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
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
            config_path=self.config_path,
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
            config_path=self.config_path,
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
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
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
            config_path=self.config_path,
        )
        self.assertEqual(outcome.status, "failed")
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.error_message, "finished elsewhere")
        self.assertEqual(run.orphans, [])
        self.assertIsNone(run.document)

    # --- live progress ---------------------------------------------------

    def _recording(self, run_id, seen, replies, unsupported=None):
        """Build adapters that record the run's stored progress mid-call."""
        app_engine, user_id = self.app_engine, self.user_id

        class _RecTailor(_Tailor):
            def complete(self, **kwargs: object) -> LLMResponse:
                run = read_run(app_engine, user_id, run_id)
                seen.append(("tailor", run.progress))
                return super().complete(**kwargs)

        class _RecCritic(_Critic):
            def complete(self, **kwargs: object) -> LLMResponse:
                run = read_run(app_engine, user_id, run_id)
                seen.append(("critic", run.progress))
                return super().complete(**kwargs)

        return {
            "ollama": _RecTailor(replies),
            "anthropic": _RecCritic(unsupported),
        }

    def test_progress_is_visible_during_each_tailor_and_critic_call(self) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
        bad = self._clean_bullets() + [
            {"text": "Led a team of 12", "evidence_refs": []}
        ]
        seen: list = []
        adapters = self._recording(
            run_id, seen, [self._reply(bad), self._reply(self._clean_bullets())]
        )
        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters=adapters,
            config_path=self.config_path,
        )
        self.assertEqual((outcome.status, outcome.attempts), ("approved", 2))
        tailor_seen = [p for who, p in seen if who == "tailor"]
        self.assertEqual([p["phase"] for p in tailor_seen], ["tailoring"] * 2)
        self.assertEqual([p["attempt"] for p in tailor_seen], [1, 2])
        self.assertEqual(tailor_seen[0]["max_attempts"], 3)
        self.assertIn("Attempt 2 of 3", tailor_seen[1]["message"])
        self.assertIn("phase_started_at", tailor_seen[0])
        critic_seen = [p for who, p in seen if who == "critic"]
        self.assertEqual([p["phase"] for p in critic_seen], ["critic"] * 2)
        # Attempt 1 had an orphan but no code problem, so the critic ran;
        # its message names how many lines it will judge.
        self.assertRegex(critic_seen[1]["message"], r"fact-checking \d+ line\(s\)")
        self.assertEqual(
            critic_seen[1]["history"],
            ["Attempt 1: 1 point(s) to fix — trying again"],
        )
        # The history accumulated across the retry.
        self.assertEqual(tailor_seen[1]["history"], critic_seen[1]["history"])

    def test_the_last_progress_before_finishing_is_saving(self) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
        seen: list = []
        adapters = self._recording(run_id, seen, [self._reply(self._clean_bullets())])
        execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters=adapters,
            config_path=self.config_path,
        )
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.status, "approved")
        self.assertEqual(run.progress["phase"], "saving")
        self.assertEqual(run.progress["history"], ["Attempt 1: clean"])

    def test_a_surfaced_orphan_is_recorded_in_the_history(self) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
        bad = self._clean_bullets() + [
            {"text": "Led a team of 12", "evidence_refs": []}
        ]
        seen: list = []
        adapters = self._recording(run_id, seen, [self._reply(bad)])
        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters=adapters,
            config_path=self.config_path,
        )
        self.assertEqual(outcome.status, "needs_review")
        history = read_run(self.app_engine, self.user_id, run_id).progress["history"]
        self.assertEqual(
            history,
            [
                "Attempt 1: 1 point(s) to fix — trying again",
                "Attempt 2: 1 point(s) to fix — trying again",
                "Attempt 3: 1 line(s) still need your decision",
            ],
        )

    def test_an_unusable_reply_is_recorded_in_the_history(self) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
        seen: list = []
        adapters = self._recording(
            run_id, seen, ["not json", self._reply(self._clean_bullets())]
        )
        execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters=adapters,
            config_path=self.config_path,
        )
        history = read_run(self.app_engine, self.user_id, run_id).progress["history"]
        self.assertEqual(
            history,
            [
                "Attempt 1: the Tailor's reply could not be used — trying again",
                "Attempt 2: clean",
            ],
        )

    def test_the_late_critic_names_the_attempt_it_is_judging(self) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
        # Attempt 1 has a code problem (critic skipped); attempts 2 and 3
        # are unusable, so attempt 1 is judged after the loop.
        bad = [
            {
                "text": "Built dbt models powering risk reporting",
                "evidence_refs": [self.ref0],
            },
            {"text": "Invented", "evidence_refs": ["nope"]},
        ]
        seen: list = []
        adapters = self._recording(run_id, seen, [self._reply(bad), "not json"])
        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters=adapters,
            config_path=self.config_path,
        )
        self.assertEqual(outcome.attempts, 3)
        critic_seen = [p for who, p in seen if who == "critic"]
        self.assertEqual(len(critic_seen), 1)
        self.assertEqual(critic_seen[0]["phase"], "critic")
        self.assertEqual(critic_seen[0]["attempt"], 1)
        self.assertIn("Attempt 1 of 3", critic_seen[0]["message"])

    def test_a_progress_write_failure_does_not_fail_the_run(self) -> None:
        self._store_cv()
        with mock.patch(
            "core.tailoring.loop.set_progress", side_effect=RuntimeError("db down")
        ) as patched:
            outcome = self._run(
                _Tailor([self._reply(self._clean_bullets())]), _Critic()
            )
        self.assertGreaterEqual(patched.call_count, 3)
        self.assertEqual((outcome.status, outcome.attempts), ("approved", 1))

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
                self.app_engine,
                self.user_id,
                _JOB,
                adapters={"ollama": tailor},
                config_path=self.config_path,
            )
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))
        self.assertEqual(tailor.prompts, [])
        self.assertEqual(self._run_count(), 0)

    def test_executing_without_an_anthropic_adapter_fails_with_a_clear_message(
        self,
    ) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
        tailor = _Tailor([self._reply(self._clean_bullets())])
        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={"ollama": tailor},
            config_path=self.config_path,
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
            start_tailoring(
                self.app_engine, self.user_id, _JOB, config_path=self.config_path
            )

    def test_an_unknown_job_cannot_start(self) -> None:
        self._store_cv()
        with self.assertRaises(UnknownJobError):
            start_tailoring(
                self.app_engine,
                self.user_id,
                "zzfixture-tlr-loop-nope",
                config_path=self.config_path,
            )

    def test_a_job_with_no_title_cannot_start(self) -> None:
        # Review Focus 3.
        self._store_cv()
        with self.assertRaises(NoTargetTitleError):
            start_tailoring(
                self.app_engine,
                self.user_id,
                _NO_TITLE_JOB,
                config_path=self.config_path,
            )

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
            start_tailoring(
                self.app_engine, self.user_id, _JOB, config_path=self.config_path
            )

    def test_start_creates_a_generating_run_and_execute_finishes_it(self) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
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
            config_path=self.config_path,
        )
        self.assertEqual(outcome.run_id, run_id)
        self.assertEqual(
            read_run(self.app_engine, self.user_id, run_id).status, "approved"
        )

    def test_the_run_uses_the_truth_base_version_it_started_with(self) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
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
            config_path=self.config_path,
        )
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.document.experience[0].company, "Acme Bank")


class _ClaudeBoth:
    """One Anthropic adapter serving the Tailor and the critic (backend claude)."""

    def __init__(self, tailor: _Tailor, critic: _Critic) -> None:
        self.tailor = tailor
        self.critic = critic

    def complete(self, **kwargs: object) -> LLMResponse:
        side = self.critic if "verdicts" in kwargs["prompt"] else self.tailor
        response = side.complete(**kwargs)
        return LLMResponse(
            text=response.text,
            provider="anthropic",
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )


class TestTailoringUsage(_LoopFixtures):
    """Tokens and estimated cost per run."""

    _TAILOR = (3300, 4000)
    _CRITIC = (1000, 200)
    _CRITIC_COST = (1000 * 2.0 + 200 * 10.0) / 1e6
    _TAILOR_COST = (3300 * 2.0 + 4000 * 10.0) / 1e6

    def _usage(self, run_id):
        return read_run(self.app_engine, self.user_id, run_id).usage

    def test_a_local_run_records_free_tailor_and_priced_critic_calls(self) -> None:
        self._store_cv()
        outcome = self._run(
            _Tailor([self._reply(self._clean_bullets())], self._TAILOR),
            _Critic(tokens=self._CRITIC),
        )
        usage = self._usage(outcome.run_id)
        self.assertEqual(
            [
                (c["task"], c["input_tokens"], c["output_tokens"])
                for c in usage["calls"]
            ],
            [("cv_tailoring", 3300, 4000), ("fabrication_critic", 1000, 200)],
        )
        self.assertEqual(usage["calls"][0]["cost_usd"], 0.0)
        self.assertEqual(usage["calls"][0]["model"], "llama3.1:8b")
        self.assertEqual(usage["calls"][1]["model"], "claude-sonnet-5")
        self.assertAlmostEqual(usage["calls"][1]["cost_usd"], self._CRITIC_COST)
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (4300, 4200))
        self.assertAlmostEqual(usage["cost_usd"], self._CRITIC_COST)

    def test_a_claude_run_prices_the_tailor_too(self) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine,
            self.user_id,
            _JOB,
            backend="claude",
            config_path=self.config_path,
        )
        both = _ClaudeBoth(
            _Tailor([self._reply(self._clean_bullets())], self._TAILOR),
            _Critic(tokens=self._CRITIC),
        )
        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={"anthropic": both},
            config_path=self.config_path,
        )
        self.assertEqual(outcome.status, "approved")
        usage = self._usage(run_id)
        self.assertAlmostEqual(usage["calls"][0]["cost_usd"], self._TAILOR_COST)
        self.assertAlmostEqual(usage["cost_usd"], self._TAILOR_COST + self._CRITIC_COST)

    def test_a_retry_run_records_every_attempts_calls(self) -> None:
        self._store_cv()
        bad = [{"text": "Invented", "evidence_refs": ["nope"]}]
        outcome = self._run(
            _Tailor([self._reply(bad), self._reply(self._clean_bullets())], (10, 20)),
            _Critic(tokens=(5, 6)),
        )
        self.assertEqual(outcome.attempts, 2)
        usage = self._usage(outcome.run_id)
        # Attempt 1 skipped the critic (code problem): tailor, tailor, critic.
        self.assertEqual(
            [c["task"] for c in usage["calls"]],
            ["cv_tailoring", "cv_tailoring", "fabrication_critic"],
        )
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (25, 46))

    def test_an_unusable_tailor_reply_still_counts_its_tokens(self) -> None:
        self._store_cv()
        outcome = self._run(_Tailor(["not json"], (7, 9)), _Critic())
        self.assertEqual(outcome.status, "failed")
        usage = self._usage(outcome.run_id)
        self.assertEqual(len(usage["calls"]), 3)
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (21, 27))
        self.assertEqual(usage["cost_usd"], 0.0)

    def test_a_failed_run_keeps_its_usage(self) -> None:
        self._store_cv()
        outcome = self._run(
            _Tailor([self._reply(self._clean_bullets())], self._TAILOR),
            _Critic(reply="lgtm", tokens=self._CRITIC),
        )
        self.assertEqual(outcome.status, "failed")
        usage = self._usage(outcome.run_id)
        self.assertEqual(
            [c["task"] for c in usage["calls"]],
            ["cv_tailoring", "fabrication_critic"],
        )
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (4300, 4200))

    def test_an_unknown_model_makes_the_total_cost_null(self) -> None:
        self._store_cv()
        with tempfile.TemporaryDirectory() as tmp:
            config = write_pinned_task_config(tmp, critic_model="claude-mystery-1")
            outcome = self._run(
                _Tailor([self._reply(self._clean_bullets())], self._TAILOR),
                _Critic(tokens=self._CRITIC),
                config_path=config,
            )
        usage = self._usage(outcome.run_id)
        self.assertIsNone(usage["calls"][1]["cost_usd"])
        self.assertIsNone(usage["cost_usd"])
        self.assertEqual(usage["input_tokens"], 4300)

    def test_a_cancelled_run_keeps_the_usage_written_so_far(self) -> None:
        self._store_cv()
        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
        critic = _CancellingCritic(
            lambda: cancel_run(self.app_engine, self.user_id, run_id)
        )
        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={
                "ollama": _Tailor([self._reply(self._clean_bullets())], self._TAILOR),
                "anthropic": critic,
            },
            config_path=self.config_path,
        )
        self.assertEqual(outcome.status, "cancelled")
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(len(run.usage["calls"]), 1)
        self.assertEqual(run.usage["input_tokens"], 3300)

    def test_a_usage_write_failure_does_not_fail_the_run(self) -> None:
        self._store_cv()
        with mock.patch(
            "core.tailoring.loop.set_usage", side_effect=RuntimeError("db down")
        ) as patched:
            outcome = self._run(
                _Tailor([self._reply(self._clean_bullets())]), _Critic()
            )
        self.assertEqual(patched.call_count, 2)
        self.assertEqual((outcome.status, outcome.attempts), ("approved", 1))
        # The final write still carries everything the loop saw.
        self.assertEqual(len(self._usage(outcome.run_id)["calls"]), 2)

    def test_the_finished_run_has_usage_while_generating_has_the_running_total(
        self,
    ) -> None:
        self._store_cv()
        seen: list[dict | None] = []

        class _Peek(_Critic):
            def complete(peek, **kwargs):  # noqa: N805
                seen.append(self._usage(run_id))
                return super().complete(**kwargs)

        run_id = start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )
        execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={
                "ollama": _Tailor([self._reply(self._clean_bullets())], self._TAILOR),
                "anthropic": _Peek(),
            },
            config_path=self.config_path,
        )
        self.assertEqual(seen[0]["input_tokens"], 3300)


class _CancellingTailor(_Tailor):
    """Cancels its own run from inside its `cancel_on`-th call."""

    def __init__(self, replies, cancel, cancel_on: int = 1) -> None:
        super().__init__(replies)
        self._cancel = cancel
        self._cancel_on = cancel_on

    def complete(self, **kwargs: object) -> LLMResponse:
        if len(self.prompts) + 1 == self._cancel_on:
            self._cancel()
        return super().complete(**kwargs)


class _BlockingTailor(_Tailor):
    """Blocks until the test ends, as a slow local model would."""

    def complete(self, **kwargs: object) -> LLMResponse:
        self.prompts.append(kwargs["prompt"])
        _RELEASE.wait(30)
        return super().complete(**kwargs)


class _CancellingCritic(_Critic):
    """Cancels the run from inside the critic call, then blocks or answers."""

    def __init__(self, cancel, *, block: bool = True) -> None:
        super().__init__()
        self._cancel = cancel
        self._block = block

    def complete(self, **kwargs: object) -> LLMResponse:
        self._cancel()
        if self._block:
            _RELEASE.wait(30)
        return super().complete(**kwargs)


class TestTailoringCancel(_LoopFixtures):
    """Cancelling a run."""

    def _start(self) -> uuid.UUID:
        self._store_cv()
        return start_tailoring(
            self.app_engine, self.user_id, _JOB, config_path=self.config_path
        )

    def _cancel(self, run_id) -> None:
        cancel_run(self.app_engine, self.user_id, run_id)

    def _execute(self, run_id, tailor, critic):
        return execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={"ollama": tailor, "anthropic": critic},
            config_path=self.config_path,
        )

    def _assert_nothing_persisted(self, run_id) -> None:
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(run.orphans, [])
        self.assertIsNone(run.document)
        self.assertIsNone(run.progress)
        with self.owner.connect() as conn:
            message = conn.execute(
                text("SELECT error_message FROM tailoring.tailored_cv WHERE id = :r"),
                {"r": run_id},
            ).scalar_one()
        self.assertIsNone(message)

    def test_cancel_during_the_tailor_call_stops_before_the_critic(self) -> None:
        run_id = self._start()
        tailor = _CancellingTailor(
            [self._reply(self._clean_bullets())], lambda: self._cancel(run_id)
        )
        critic = _Critic()
        outcome = self._execute(run_id, tailor, critic)
        self.assertEqual((outcome.status, outcome.attempts), ("cancelled", 1))
        self.assertEqual(critic.calls, 0)
        self._assert_nothing_persisted(run_id)

    def test_cancel_while_the_tailor_blocks_returns_quickly(self) -> None:
        run_id = self._start()
        timer = threading.Timer(1.0, self._cancel, (run_id,))
        self.addCleanup(timer.cancel)
        timer.start()
        critic = _Critic()
        started = time.monotonic()
        outcome = self._execute(
            run_id, _BlockingTailor([self._reply(self._clean_bullets())]), critic
        )
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((outcome.status, outcome.attempts), ("cancelled", 1))
        self.assertEqual(critic.calls, 0)
        self._assert_nothing_persisted(run_id)

    def test_cancel_before_the_run_executes_makes_no_tailor_call(self) -> None:
        run_id = self._start()
        self._cancel(run_id)
        tailor = _Tailor([self._reply(self._clean_bullets())])
        critic = _Critic()
        outcome = self._execute(run_id, tailor, critic)
        self.assertEqual((outcome.status, outcome.attempts), ("cancelled", 0))
        self.assertEqual(tailor.prompts, [])
        self.assertEqual(critic.calls, 0)
        self._assert_nothing_persisted(run_id)

    def test_cancel_during_the_critic_call_ends_cancelled(self) -> None:
        run_id = self._start()
        critic = _CancellingCritic(lambda: self._cancel(run_id))
        started = time.monotonic()
        outcome = self._execute(
            run_id, _Tailor([self._reply(self._clean_bullets())]), critic
        )
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((outcome.status, outcome.attempts), ("cancelled", 1))
        self._assert_nothing_persisted(run_id)

    def test_cancel_on_a_non_final_attempt_with_problems_stops_after_one_prompt(
        self,
    ) -> None:
        run_id = self._start()
        bad = [{"text": "Invented", "evidence_refs": ["nope"]}]
        tailor = _CancellingTailor(
            [self._reply(bad), self._reply(self._clean_bullets())],
            lambda: self._cancel(run_id),
        )
        critic = _Critic()
        outcome = self._execute(run_id, tailor, critic)
        self.assertEqual((outcome.status, outcome.attempts), ("cancelled", 1))
        self.assertEqual(len(tailor.prompts), 1)
        self.assertEqual(critic.calls, 0)
        self._assert_nothing_persisted(run_id)

    def test_cancel_before_the_late_fallback_critic_makes_no_critic_call(
        self,
    ) -> None:
        run_id = self._start()
        bad = [{"text": "Invented", "evidence_refs": ["nope"]}]
        # Attempt 1 has a code problem (critic skipped); attempts 2 and 3 are
        # unusable, so the loop falls back to judging attempt 1 late. The
        # cancel lands in the third Tailor call, just before that judgement.
        tailor = _CancellingTailor(
            [self._reply(bad), "not json"], lambda: self._cancel(run_id), cancel_on=3
        )
        critic = _Critic()
        outcome = self._execute(run_id, tailor, critic)
        self.assertEqual((outcome.status, outcome.attempts), ("cancelled", 3))
        self.assertEqual(len(tailor.prompts), 3)
        self.assertEqual(critic.calls, 0)
        self._assert_nothing_persisted(run_id)

    def test_cancel_before_saving_persists_nothing(self) -> None:
        run_id = self._start()
        critic = _CancellingCritic(lambda: self._cancel(run_id), block=False)
        outcome = self._execute(
            run_id, _Tailor([self._reply(self._clean_bullets())]), critic
        )
        self.assertEqual((outcome.status, outcome.attempts), ("cancelled", 1))
        self.assertEqual(critic.calls, 1)
        self._assert_nothing_persisted(run_id)


class TestTailoringBackends(_LoopFixtures):
    """The Tailor backend selector, through a mock Ollama transport."""

    def _start(self, backend: str | None = None) -> uuid.UUID:
        self._store_cv()
        return start_tailoring(
            self.app_engine,
            self.user_id,
            _JOB,
            backend=backend,
            config_path=self.config_path,
        )

    def _mock_client(self, handler) -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def _execute(self, run_id, client: httpx.Client, critic=None):
        self.unloads = mock.Mock()
        self.abort_spy = mock.Mock(wraps=loop_mod.abort_client)
        with (
            mock.patch.object(loop_mod, "_new_ollama_client", lambda: client),
            mock.patch.object(loop_mod, "_unload_in_background", self.unloads),
            mock.patch.object(loop_mod, "abort_client", self.abort_spy),
        ):
            return loop_mod.execute_tailoring(
                self.app_engine,
                self.user_id,
                run_id,
                adapters={"anthropic": critic or _Critic()},
                config_path=self.config_path,
            )

    def _run_count(self) -> int:
        with self.owner.connect() as conn:
            return conn.execute(
                text("SELECT count(*) FROM tailoring.tailored_cv WHERE user_id = :u"),
                {"u": self.user_id},
            ).scalar_one()

    def test_the_chosen_backend_is_stored_and_the_default_is_resolved(self) -> None:
        run_id = self._start("native")
        self.assertEqual(
            read_run(self.app_engine, self.user_id, run_id).tailor_backend, "native"
        )
        # The pinned config routes the Tailor to ollama, so the default is
        # the Docker service.
        default_run = self._start()
        self.assertEqual(
            read_run(self.app_engine, self.user_id, default_run).tailor_backend,
            "docker",
        )

    def test_an_unknown_backend_is_refused_before_any_run_or_call(self) -> None:
        self._store_cv()
        with self.assertRaises(UnknownBackendError):
            start_tailoring(
                self.app_engine,
                self.user_id,
                _JOB,
                backend="bogus",
                config_path=self.config_path,
            )
        self.assertEqual(self._run_count(), 0)

    def test_a_legacy_run_without_a_backend_still_runs(self) -> None:
        self._store_cv()
        run_id = create_run(
            self.app_engine,
            self.user_id,
            job_group_id=_JOB,
            truth_base_version=1,
            target_title="Lead Data Engineer",
        )
        client = self._mock_client(
            lambda request: httpx.Response(
                200, json={"response": self._reply(self._clean_bullets())}
            )
        )
        outcome = self._execute(run_id, client)
        self.assertEqual(outcome.status, "approved")
        self.assertIsNone(
            read_run(self.app_engine, self.user_id, run_id).tailor_backend
        )

    def test_a_local_backend_run_uses_the_local_prompt_model_and_url(self) -> None:
        run_id = self._start("docker")
        seen: list[tuple[str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((str(request.url), json.loads(request.content)))
            return httpx.Response(
                200, json={"response": self._reply(self._clean_bullets())}
            )

        client = self._mock_client(handler)
        outcome = self._execute(run_id, client)
        self.assertEqual(outcome.status, "approved")
        url, body = seen[0]
        self.assertEqual(url, "http://ollama:11434/api/generate")
        self.assertEqual(body["model"], "llama3.1:8b")
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.tailor_prompt_version, "local.v2")
        self.assertEqual(run.tailor_model, "llama3.1:8b")
        self.assertTrue(client.is_closed)
        self.unloads.assert_not_called()

    def test_the_native_backend_calls_the_host_url(self) -> None:
        run_id = self._start("native")
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            return httpx.Response(
                200, json={"response": self._reply(self._clean_bullets())}
            )

        self._execute(run_id, self._mock_client(handler))
        self.assertEqual(urls, ["http://host.docker.internal:11434/api/generate"])

    def test_cancelling_a_claude_run_never_aborts_a_client(self) -> None:
        run_id = self._start("claude")
        spy = mock.Mock(wraps=loop_mod.abort_client)
        timer = threading.Timer(
            1.0, cancel_run, (self.app_engine, self.user_id, run_id)
        )
        self.addCleanup(timer.cancel)
        timer.start()
        with (
            mock.patch.object(loop_mod, "abort_client", spy),
            mock.patch.object(loop_mod, "_unload_in_background") as unload,
        ):
            outcome = loop_mod.execute_tailoring(
                self.app_engine,
                self.user_id,
                run_id,
                adapters={
                    "anthropic": _BlockingTailor([self._reply(self._clean_bullets())])
                },
                config_path=self.config_path,
            )
        self.assertEqual(outcome.status, "cancelled")
        spy.assert_not_called()
        unload.assert_not_called()

    def test_cancel_closes_the_local_connection_and_returns_quickly(self) -> None:
        run_id = self._start("docker")

        def handler(request: httpx.Request) -> httpx.Response:
            _RELEASE.wait(30)
            return httpx.Response(200, json={"response": "{}"})

        client = self._mock_client(handler)
        timer = threading.Timer(
            1.0, cancel_run, (self.app_engine, self.user_id, run_id)
        )
        self.addCleanup(timer.cancel)
        timer.start()
        critic = _Critic()
        started = time.monotonic()
        outcome = self._execute(run_id, client, critic)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((outcome.status, outcome.attempts), ("cancelled", 1))
        self.assertTrue(client.is_closed)
        # ... and the model is unloaded so the runner stops computing.
        self.abort_spy.assert_called_once_with(client)
        self.unloads.assert_called_once()
        self.assertEqual(self.unloads.call_args.args[0].id, "docker")
        self.assertEqual(critic.calls, 0)
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.status, "cancelled")
        self.assertIsNone(run.document)
        self.assertEqual(run.attempts, 1)


if __name__ == "__main__":
    unittest.main()
