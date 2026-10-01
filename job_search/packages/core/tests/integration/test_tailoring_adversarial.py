"""Adversarial tests for the fabrication guard (PLAN.md Step 17 "Done
when": a deliberate prompt toward exaggeration is caught by the critic and
surfaced rather than emitted).

The Tailor is always a fake that exaggerates on purpose. The critic is
(a) a deterministic stand-in applying the stated rule, always run; and
(b) the REAL Claude critic, only with RUN_PAID_TESTS=1 — real, paid calls.
"""

from __future__ import annotations

import json
import os
import re
import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.cv.store import write_truth_base
from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.types import LLMResponse
from core.settings import get_settings
from core.tailoring.decisions import apply_decision
from core.tailoring.loop import run_tailoring
from core.tailoring.store import read_orphan, read_run, save_decision

_JOB = "zzfixture-tlr-adv-1"
_HONEST = "Built dbt models powering risk reporting"
_EXAGGERATED = (
    "Led a team of 12 engineers building dbt models that cut reporting costs by 40%"
)


class _ExaggeratingTailor:
    """A Tailor deliberately pushed toward exaggeration: it cites a real
    source bullet but invents a team size and a saving."""

    def __init__(self, truth_base, *, honest_second: bool = False) -> None:
        self.ref0 = bullet_id(truth_base, 0, 0)
        self.ref1 = bullet_id(truth_base, 0, 1)
        self.honest_second = honest_second

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        bullets = [
            {"text": _EXAGGERATED, "evidence_refs": [self.ref0]},
            {
                "text": "Migrated nightly batch jobs to Airflow",
                "evidence_refs": [self.ref1],
            },
        ]
        if self.honest_second:
            bullets.append({"text": _HONEST, "evidence_refs": [self.ref0]})
        return LLMResponse(
            text=json.dumps(
                {
                    "experience": [{"truth_index": 0, "bullets": bullets}],
                    "skills": ["dbt", "Airflow"],
                }
            ),
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class _RuleApplyingCritic:
    """Deterministic stand-in for the critic: a line is unsupported if it
    contains a digit that none of its sources contain."""

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        # The items are the first JSON value after "JSON list". Decode just
        # that value: the prompt's trailing format example contains "]" too.
        start = prompt.index("[", prompt.index("JSON list"))
        items, _ = json.JSONDecoder().raw_decode(prompt[start:])
        verdicts = []
        for item in items:
            source_digits = set(re.findall(r"\d+", " ".join(item["sources"])))
            digits = set(re.findall(r"\d+", item["text"]))
            extra = digits - source_digits
            verdicts.append(
                {
                    "id": item["id"],
                    "supported": not extra,
                    "issue": (
                        f"adds numbers not in the source: {sorted(extra)}"
                        if extra
                        else ""
                    ),
                }
            )
        return LLMResponse(
            text=json.dumps(
                {"verdicts": verdicts, "stretch": {"is_stretch": False, "reason": ""}}
            ),
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.truth_base = make_truth_base()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture adversarial user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job "
                    "(job_group_id, title_for_display, company, description) VALUES "
                    "(:j, 'Lead Data Engineer', 'Gamma', 'Own the data platform.')"
                ),
                {"j": _JOB},
            )
        write_truth_base(self.app_engine, self.user_id, "md", self.truth_base)

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
                text("DELETE FROM gold.dim_job WHERE job_group_id = :j"), {"j": _JOB}
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :u"), {"u": self.user_id}
            )

    def _run(self, tailor, critic):
        outcome = run_tailoring(
            self.app_engine,
            self.user_id,
            _JOB,
            adapters={"ollama": tailor, "anthropic": critic},
        )
        return outcome, read_run(self.app_engine, self.user_id, outcome.run_id)

    def _assert_exaggeration_was_surfaced(
        self, outcome, run, expected_issue_fragment: str | None = None
    ) -> None:
        self.assertEqual(outcome.status, "needs_review")
        flagged = [o for o in run.orphans if o.kind == "unsupported"]
        self.assertEqual([o.text for o in flagged], [_EXAGGERATED])
        self.assertEqual(flagged[0].status, "pending")
        # The verdict path ran: the critic gave a reason (free-form text).
        self.assertTrue(flagged[0].issue and flagged[0].issue.strip())
        if expected_issue_fragment is not None:
            self.assertIn(expected_issue_fragment, flagged[0].issue)
        # Never emitted as approved: the run is not approved and the
        # exaggerated line is still waiting for a decision.
        self.assertNotEqual(run.status, "approved")

    def _reject_and_check_reversion(self, run) -> None:
        orphan = read_orphan(self.app_engine, self.user_id, run.orphans[0].id)
        result = apply_decision(
            run.document,
            orphan,
            action="reject",
            evidence_ref=None,
            truth_base=self.truth_base,
        )
        save_decision(
            self.app_engine,
            self.user_id,
            orphan=orphan,
            status="rejected",
            evidence_ref=None,
            document=result.document,
            base_document=run.document,
            removed_position=result.removed_position,
        )
        after = read_run(self.app_engine, self.user_id, run.id)
        texts = [b.text for b in after.document.experience[0].bullets]
        self.assertNotIn(_EXAGGERATED, texts)
        self.assertIn("Built dbt models for risk reporting", texts)  # reverted
        self.assertEqual(after.status, "approved")
        reverted = [
            b
            for b in after.document.experience[0].bullets
            if b.text == "Built dbt models for risk reporting"
        ]
        self.assertEqual(len(reverted), 1)
        self.assertEqual(reverted[0].origin, "original")
        self.assertEqual(reverted[0].evidence_refs, [bullet_id(self.truth_base, 0, 0)])


class TestExaggerationIsCaughtOffline(_Base):
    def test_an_exaggerated_bullet_is_surfaced_and_never_approved(self) -> None:
        outcome, run = self._run(
            _ExaggeratingTailor(self.truth_base), _RuleApplyingCritic()
        )
        self._assert_exaggeration_was_surfaced(
            outcome, run, expected_issue_fragment="adds numbers not in the source"
        )

    def test_rejecting_it_reverts_to_the_users_own_wording_and_approves(self) -> None:
        _, run = self._run(_ExaggeratingTailor(self.truth_base), _RuleApplyingCritic())
        self._reject_and_check_reversion(run)

    def test_an_honest_rewording_next_to_it_is_not_flagged(self) -> None:
        outcome, run = self._run(
            _ExaggeratingTailor(self.truth_base, honest_second=True),
            _RuleApplyingCritic(),
        )
        self.assertEqual(outcome.status, "needs_review")
        self.assertEqual([o.text for o in run.orphans], [_EXAGGERATED])
        honest = [b for b in run.document.experience[0].bullets if b.text == _HONEST]
        self.assertEqual(len(honest), 1)
        self.assertEqual(honest[0].origin, "reworded")


@unittest.skipUnless(
    os.environ.get("RUN_PAID_TESTS") == "1",
    "calls the real Claude critic (paid); set RUN_PAID_TESTS=1 to run",
)
class TestExaggerationIsCaughtByRealClaude(_Base):
    def _real_critic(self):
        import anthropic

        settings = get_settings()
        if not settings.anthropic_api_key:
            self.skipTest("ANTHROPIC_API_KEY is not configured")
        return AnthropicAdapter(
            api_key=settings.anthropic_api_key,
            client=anthropic.Anthropic(api_key=settings.anthropic_api_key),
        )

    def test_the_real_critic_catches_a_deliberate_exaggeration(self) -> None:
        outcome, run = self._run(
            _ExaggeratingTailor(self.truth_base), self._real_critic()
        )
        self._assert_exaggeration_was_surfaced(outcome, run)

    def test_the_real_critic_does_not_flag_an_honest_rewording(self) -> None:
        outcome, run = self._run(
            _ExaggeratingTailor(self.truth_base, honest_second=True),
            self._real_critic(),
        )
        # A failed run has no orphans, which would pass vacuously: require
        # that the critic really judged the lines.
        self.assertEqual(outcome.status, "needs_review")
        self.assertEqual([o.text for o in run.orphans], [_EXAGGERATED])


if __name__ == "__main__":
    unittest.main()
