"""Integration tests for tailoring persistence (real Postgres)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.tailoring.assemble import assemble
from core.tailoring.decisions import apply_decision
from core.tailoring.schema import TailorBullet, TailorExperience, TailorOutput
from core.tailoring.store import (
    OrphanDraft,
    RunAlreadyFinishedError,
    StaleDecisionError,
    cancel_run,
    create_run,
    finish_run,
    is_cancelled,
    latest_run_id,
    read_orphan,
    read_run,
    save_decision,
    set_progress,
    set_usage,
)


class TestTailoringStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        self.user_a = uuid.uuid4()
        self.user_b = uuid.uuid4()
        with self.owner.begin() as conn:
            for user_id in (self.user_a, self.user_b):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'zzfixture store user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM tailoring.tailored_cv WHERE user_id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )

    _PROGRESS = {
        "attempt": 1,
        "max_attempts": 3,
        "phase": "tailoring",
        "message": "Attempt 1 of 3: the Tailor is rewriting your CV…",
        "phase_started_at": "2026-10-04T10:00:00+00:00",
        "history": [],
    }

    def _new_run(self) -> uuid.UUID:
        return create_run(
            self.app_engine,
            self.user_a,
            job_group_id="zzfixture-job",
            truth_base_version=1,
            target_title="Zz Title",
        )

    def test_a_new_run_has_no_progress_and_an_updated_at(self) -> None:
        run = read_run(self.app_engine, self.user_a, self._new_run())
        self.assertIsNone(run.progress)
        self.assertIsNotNone(run.updated_at)

    def test_set_progress_writes_while_generating(self) -> None:
        run_id = self._new_run()
        before = read_run(self.app_engine, self.user_a, run_id)
        set_progress(self.app_engine, self.user_a, run_id, self._PROGRESS)
        run = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(run.progress, self._PROGRESS)
        self.assertGreaterEqual(run.updated_at, before.updated_at)

    def test_set_progress_is_ignored_once_the_run_is_finished(self) -> None:
        run_id = self._new_run()
        finish_run(
            self.app_engine,
            self.user_a,
            run_id,
            status="failed",
            document=None,
            orphans=[],
            attempts=1,
            error_message="boom",
        )
        before = read_run(self.app_engine, self.user_a, run_id)
        set_progress(self.app_engine, self.user_a, run_id, self._PROGRESS)
        after = read_run(self.app_engine, self.user_a, run_id)
        self.assertIsNone(after.progress)
        self.assertEqual(after.updated_at, before.updated_at)

    def test_another_user_cannot_write_progress(self) -> None:
        run_id = self._new_run()
        set_progress(self.app_engine, self.user_b, run_id, self._PROGRESS)
        self.assertIsNone(read_run(self.app_engine, self.user_a, run_id).progress)

    _USAGE = {
        "calls": [
            {
                "task": "cv_tailoring",
                "model": "claude-sonnet-5",
                "input_tokens": 3300,
                "output_tokens": 4000,
                "cost_usd": 0.0466,
            }
        ],
        "input_tokens": 3300,
        "output_tokens": 4000,
        "cost_usd": 0.0466,
    }

    def test_a_new_run_has_no_usage(self) -> None:
        self.assertIsNone(read_run(self.app_engine, self.user_a, self._new_run()).usage)

    def test_set_usage_writes_while_generating(self) -> None:
        run_id = self._new_run()
        set_usage(self.app_engine, self.user_a, run_id, self._USAGE)
        self.assertEqual(
            read_run(self.app_engine, self.user_a, run_id).usage, self._USAGE
        )

    def test_set_usage_is_ignored_once_the_run_is_finished(self) -> None:
        run_id = self._new_run()
        finish_run(
            self.app_engine,
            self.user_a,
            run_id,
            status="failed",
            document=None,
            orphans=[],
            attempts=1,
            error_message="boom",
        )
        set_usage(self.app_engine, self.user_a, run_id, self._USAGE)
        self.assertIsNone(read_run(self.app_engine, self.user_a, run_id).usage)

    def test_another_user_cannot_write_usage(self) -> None:
        run_id = self._new_run()
        set_usage(self.app_engine, self.user_b, run_id, self._USAGE)
        self.assertIsNone(read_run(self.app_engine, self.user_a, run_id).usage)

    def test_finish_run_writes_usage_even_for_a_failed_run(self) -> None:
        run_id = self._new_run()
        finish_run(
            self.app_engine,
            self.user_a,
            run_id,
            status="failed",
            document=None,
            orphans=[],
            attempts=1,
            error_message="boom",
            usage=self._USAGE,
        )
        self.assertEqual(
            read_run(self.app_engine, self.user_a, run_id).usage, self._USAGE
        )

    def test_finish_run_without_usage_keeps_what_was_recorded(self) -> None:
        run_id = self._new_run()
        set_usage(self.app_engine, self.user_a, run_id, self._USAGE)
        finish_run(
            self.app_engine,
            self.user_a,
            run_id,
            status="failed",
            document=None,
            orphans=[],
            attempts=1,
            error_message="boom",
        )
        self.assertEqual(
            read_run(self.app_engine, self.user_a, run_id).usage, self._USAGE
        )

    def test_cancel_run_keeps_the_usage(self) -> None:
        run_id = self._new_run()
        set_usage(self.app_engine, self.user_a, run_id, self._USAGE)
        cancel_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(
            read_run(self.app_engine, self.user_a, run_id).usage, self._USAGE
        )

    def _document(self):
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[
                        TailorBullet(text="Invented one", evidence_refs=[]),
                        TailorBullet(text="Invented two", evidence_refs=[]),
                        TailorBullet(text="Invented three", evidence_refs=[]),
                    ],
                )
            ]
        )
        return assemble(self.truth_base, output, target_title="Lead Data Engineer")

    def _draft(self, position: int, text_: str) -> OrphanDraft:
        return OrphanDraft(
            kind="orphan",
            section="experience",
            experience_index=0,
            bullet_index=position,
            text=text_,
            claimed_refs=[],
            issue="no evidence_ref in the CV",
        )

    def _finished_run(self):
        run_id = create_run(
            self.app_engine,
            self.user_a,
            job_group_id="zzfixture-job",
            truth_base_version=3,
            target_title="Lead Data Engineer",
        )
        finish_run(
            self.app_engine,
            self.user_a,
            run_id,
            status="needs_review",
            document=self._document(),
            orphans=[
                self._draft(0, "Invented one"),
                self._draft(1, "Invented two"),
                self._draft(2, "Invented three"),
            ],
            attempts=3,
            tailor_model="llama3.1:8b",
            tailor_prompt_version="local.v1",
            critic_model="claude-sonnet-5",
            critic_prompt_version="claude.v1",
        )
        return run_id

    def test_a_new_run_starts_generating_with_no_document(self) -> None:
        run_id = create_run(
            self.app_engine,
            self.user_a,
            job_group_id="zzfixture-job",
            truth_base_version=3,
            target_title="Lead Data Engineer",
        )
        run = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(run.status, "generating")
        self.assertIsNone(run.document)
        self.assertEqual(run.truth_base_version, 3)
        self.assertEqual(run.orphans, [])

    def test_a_finished_run_round_trips_document_orphans_and_versions(self) -> None:
        run_id = self._finished_run()
        run = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(run.status, "needs_review")
        self.assertEqual(run.attempts, 3)
        self.assertEqual(run.document, self._document())
        self.assertEqual(
            [o.text for o in run.orphans],
            ["Invented one", "Invented two", "Invented three"],
        )
        self.assertEqual(run.tailor_prompt_version, "local.v1")
        self.assertEqual(run.critic_model, "claude-sonnet-5")

    def test_a_failed_run_keeps_its_error_message(self) -> None:
        run_id = create_run(
            self.app_engine,
            self.user_a,
            job_group_id="zzfixture-job",
            truth_base_version=1,
            target_title="T",
        )
        finish_run(
            self.app_engine,
            self.user_a,
            run_id,
            status="failed",
            document=None,
            orphans=[],
            attempts=3,
            error_message="the Tailor's reply hit the output cap",
        )
        run = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(run.status, "failed")
        self.assertIn("output cap", run.error_message)
        self.assertIsNone(run.document)

    def test_another_user_cannot_read_the_run_or_its_orphans(self) -> None:
        run_id = self._finished_run()
        self.assertIsNone(read_run(self.app_engine, self.user_b, run_id))
        orphan = read_run(self.app_engine, self.user_a, run_id).orphans[0]
        self.assertIsNone(read_orphan(self.app_engine, self.user_b, orphan.id))

    def test_read_orphan_returns_the_row(self) -> None:
        run_id = self._finished_run()
        orphan = read_run(self.app_engine, self.user_a, run_id).orphans[1]
        again = read_orphan(self.app_engine, self.user_a, orphan.id)
        self.assertEqual(again.text, "Invented two")
        self.assertEqual(again.status, "pending")

    def test_deciding_the_last_pending_orphan_approves_the_run(self) -> None:
        run_id = self._finished_run()
        run = read_run(self.app_engine, self.user_a, run_id)
        document = run.document
        statuses = []
        for orphan in run.orphans:
            result = apply_decision(
                document,
                read_orphan(self.app_engine, self.user_a, orphan.id),
                action="link",
                evidence_ref=self.ref0,
                truth_base=self.truth_base,
            )
            base, document = document, result.document
            statuses.append(
                save_decision(
                    self.app_engine,
                    self.user_a,
                    orphan=orphan,
                    status="linked",
                    evidence_ref=self.ref0,
                    document=document,
                    base_document=base,
                    removed_position=result.removed_position,
                )
            )
        self.assertEqual(statuses, ["needs_review", "needs_review", "approved"])
        final = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(final.status, "approved")
        self.assertTrue(all(o.status == "linked" for o in final.orphans))
        self.assertTrue(
            all(b.origin == "linked" for b in final.document.experience[0].bullets)
        )

    def test_removing_a_bullet_reindexes_the_runs_other_orphans(self) -> None:
        run_id = self._finished_run()
        run = read_run(self.app_engine, self.user_a, run_id)
        first = run.orphans[0]  # position 0
        result = apply_decision(
            run.document,
            first,
            action="reject",
            evidence_ref=None,
            truth_base=self.truth_base,
        )
        save_decision(
            self.app_engine,
            self.user_a,
            orphan=first,
            status="rejected",
            evidence_ref=None,
            document=result.document,
            base_document=run.document,
            removed_position=result.removed_position,
        )
        after = read_run(self.app_engine, self.user_a, run_id)
        positions = {o.text: o.bullet_index for o in after.orphans}
        self.assertEqual(positions["Invented two"], 0)
        self.assertEqual(positions["Invented three"], 1)
        # And the reindexed orphan still matches its bullet in the document.
        second = next(o for o in after.orphans if o.text == "Invented two")
        again = apply_decision(
            after.document,
            second,
            action="reject",
            evidence_ref=None,
            truth_base=self.truth_base,
        )
        self.assertEqual(again.removed_position, (0, 0))

    def _decide(self, run, orphan, action, user=None, base=None, ref=None):
        result = apply_decision(
            run.document,
            orphan,
            action=action,
            evidence_ref=ref,
            truth_base=self.truth_base,
        )
        return save_decision(
            self.app_engine,
            user or self.user_a,
            orphan=orphan,
            status="linked" if action == "link" else "rejected",
            evidence_ref=ref,
            document=result.document,
            base_document=base or run.document,
            removed_position=result.removed_position,
        )

    def _snapshot(self, run_id):
        run = read_run(self.app_engine, self.user_a, run_id)
        return (
            run.status,
            run.document,
            [(o.status, o.bullet_index) for o in run.orphans],
        )

    def test_a_decision_on_a_stale_base_is_refused_and_changes_nothing(self) -> None:
        run_id = self._finished_run()
        stale = read_run(self.app_engine, self.user_a, run_id)
        self._decide(stale, stale.orphans[0], "reject")
        before = self._snapshot(run_id)
        with self.assertRaises(StaleDecisionError):
            self._decide(stale, stale.orphans[1], "link", ref=self.ref0)
        self.assertEqual(self._snapshot(run_id), before)

    def test_a_double_submit_is_refused_and_does_not_double_reindex(self) -> None:
        run_id = self._finished_run()
        stale = read_run(self.app_engine, self.user_a, run_id)
        self._decide(stale, stale.orphans[0], "reject")
        before = self._snapshot(run_id)
        with self.assertRaises(StaleDecisionError):
            self._decide(stale, stale.orphans[0], "reject")
        self.assertEqual(self._snapshot(run_id), before)
        after = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(
            {o.text: o.bullet_index for o in after.orphans if o.status == "pending"},
            {"Invented two": 0, "Invented three": 1},
        )

    def test_three_decisions_from_one_base_only_the_first_succeeds(self) -> None:
        run_id = self._finished_run()
        base = read_run(self.app_engine, self.user_a, run_id)
        self._decide(base, base.orphans[0], "reject")
        for orphan in base.orphans[1:]:
            with self.assertRaises(StaleDecisionError):
                self._decide(base, orphan, "link", ref=self.ref0)
        final = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(final.status, "needs_review")
        self.assertEqual(len(final.document.experience[0].bullets), 2)
        # The rejected row and the first pending one share bullet_index 0
        # after the re-index; the tie is broken pending-first, always.
        self.assertEqual(
            [o.status for o in final.orphans], ["pending", "rejected", "pending"]
        )

    def test_a_decision_by_another_user_is_refused_and_changes_nothing(self) -> None:
        run_id = self._finished_run()
        run = read_run(self.app_engine, self.user_a, run_id)
        before = self._snapshot(run_id)
        with self.assertRaises(StaleDecisionError):
            self._decide(run, run.orphans[0], "reject", user=self.user_b)
        self.assertEqual(self._snapshot(run_id), before)

    def test_a_decision_on_a_shifted_orphan_position_is_refused(self) -> None:
        # M-race: orphan 2 was read before orphan 0's removal shifted it to
        # position 1; deciding it with the stale position must not land.
        run_id = self._finished_run()
        stale = read_run(self.app_engine, self.user_a, run_id)
        self._decide(stale, stale.orphans[0], "reject")
        current = read_run(self.app_engine, self.user_a, run_id)
        before = self._snapshot(run_id)
        with self.assertRaises(StaleDecisionError):
            save_decision(
                self.app_engine,
                self.user_a,
                orphan=stale.orphans[2],  # still says bullet_index=2
                status="rejected",
                evidence_ref=None,
                document=current.document,
                base_document=current.document,
                removed_position=None,
            )
        self.assertEqual(self._snapshot(run_id), before)

    def test_finishing_a_run_twice_is_refused_and_adds_no_orphans(self) -> None:
        run_id = self._finished_run()
        before = self._snapshot(run_id)
        with self.assertRaises(RunAlreadyFinishedError):
            finish_run(
                self.app_engine,
                self.user_a,
                run_id,
                status="failed",
                document=None,
                orphans=[self._draft(0, "Invented one")],
                attempts=1,
                error_message="late failure",
            )
        self.assertEqual(self._snapshot(run_id), before)
        run = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(len(run.orphans), 3)
        self.assertIsNone(run.error_message)

    def test_cancel_run_marks_a_generating_run_and_clears_progress(self) -> None:
        run_id = self._new_run()
        set_progress(self.app_engine, self.user_a, run_id, self._PROGRESS)
        self.assertFalse(is_cancelled(self.app_engine, self.user_a, run_id))
        self.assertTrue(cancel_run(self.app_engine, self.user_a, run_id))
        run = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(run.status, "cancelled")
        self.assertIsNone(run.progress)
        self.assertTrue(is_cancelled(self.app_engine, self.user_a, run_id))

    def test_cancel_run_keeps_the_attempt_number(self) -> None:
        run_id = self._new_run()
        set_progress(self.app_engine, self.user_a, run_id, self._PROGRESS)
        cancel_run(self.app_engine, self.user_a, run_id)
        run = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(run.attempts, 1)

    def test_cancel_run_tolerates_a_non_numeric_progress_attempt(self) -> None:
        run_id = self._new_run()
        set_progress(
            self.app_engine,
            self.user_a,
            run_id,
            {**self._PROGRESS, "attempt": "two"},
        )
        self.assertTrue(cancel_run(self.app_engine, self.user_a, run_id))
        run = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual((run.status, run.attempts), ("cancelled", 0))

    def test_cancel_run_without_progress_keeps_zero_attempts(self) -> None:
        run_id = self._new_run()
        cancel_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(read_run(self.app_engine, self.user_a, run_id).attempts, 0)

    def test_create_run_stores_the_backend_and_legacy_is_null(self) -> None:
        run_id = create_run(
            self.app_engine,
            self.user_a,
            job_group_id="zzfixture-job",
            truth_base_version=1,
            target_title="Zz",
            tailor_backend="docker",
        )
        self.assertEqual(
            read_run(self.app_engine, self.user_a, run_id).tailor_backend, "docker"
        )
        legacy = self._new_run()
        self.assertIsNone(read_run(self.app_engine, self.user_a, legacy).tailor_backend)

    def test_cancel_run_is_idempotent_and_leaves_finished_runs_alone(self) -> None:
        run_id = self._new_run()
        self.assertTrue(cancel_run(self.app_engine, self.user_a, run_id))
        self.assertFalse(cancel_run(self.app_engine, self.user_a, run_id))
        done = self._finished_run()
        self.assertFalse(cancel_run(self.app_engine, self.user_a, done))
        self.assertEqual(
            read_run(self.app_engine, self.user_a, done).status, "needs_review"
        )
        self.assertFalse(is_cancelled(self.app_engine, self.user_a, done))

    def test_cancel_run_ignores_another_users_run(self) -> None:
        run_id = self._new_run()
        self.assertFalse(cancel_run(self.app_engine, self.user_b, run_id))
        self.assertEqual(
            read_run(self.app_engine, self.user_a, run_id).status, "generating"
        )
        self.assertFalse(is_cancelled(self.app_engine, self.user_b, run_id))

    def test_late_finish_on_a_cancelled_run_is_refused_without_orphans(self) -> None:
        run_id = self._new_run()
        cancel_run(self.app_engine, self.user_a, run_id)
        with self.assertRaises(RunAlreadyFinishedError):
            finish_run(
                self.app_engine,
                self.user_a,
                run_id,
                status="needs_review",
                document=self._document(),
                orphans=[self._draft(0, "Invented one")],
                attempts=1,
            )
        run = read_run(self.app_engine, self.user_a, run_id)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(run.orphans, [])
        self.assertIsNone(run.document)

    def test_finishing_another_users_run_is_refused(self) -> None:
        run_id = create_run(
            self.app_engine,
            self.user_a,
            job_group_id="zzfixture-job",
            truth_base_version=1,
            target_title="T",
        )
        with self.assertRaises(RunAlreadyFinishedError):
            finish_run(
                self.app_engine,
                self.user_b,
                run_id,
                status="failed",
                document=None,
                orphans=[],
                attempts=0,
            )
        self.assertEqual(
            read_run(self.app_engine, self.user_a, run_id).status, "generating"
        )

    def test_latest_run_id_is_the_newest_visible_run(self) -> None:
        job = "zzfixture-tlr-store-latest"
        self.assertIsNone(latest_run_id(self.app_engine, self.user_a, job))
        ids = [
            create_run(
                self.app_engine,
                self.user_a,
                job_group_id=job,
                truth_base_version=1,
                target_title="Lead Data Engineer",
            )
            for _ in range(2)
        ]
        self.assertEqual(latest_run_id(self.app_engine, self.user_a, job), ids[1])
        self.assertIsNone(latest_run_id(self.app_engine, self.user_b, job))

    def test_latest_run_id_can_be_limited_to_a_status(self) -> None:
        job = "zzfixture-tlr-store-status"
        approved = create_run(
            self.app_engine,
            self.user_a,
            job_group_id=job,
            truth_base_version=1,
            target_title="Lead Data Engineer",
        )
        finish_run(
            self.app_engine,
            self.user_a,
            approved,
            status="approved",
            document=None,
            orphans=[],
            attempts=1,
        )
        cancelled = create_run(
            self.app_engine,
            self.user_a,
            job_group_id=job,
            truth_base_version=1,
            target_title="Lead Data Engineer",
        )
        cancel_run(self.app_engine, self.user_a, cancelled)
        self.assertEqual(latest_run_id(self.app_engine, self.user_a, job), cancelled)
        self.assertEqual(
            latest_run_id(self.app_engine, self.user_a, job, status="approved"),
            approved,
        )
        self.assertIsNone(
            latest_run_id(self.app_engine, self.user_a, job, status="needs_review")
        )


if __name__ == "__main__":
    unittest.main()
