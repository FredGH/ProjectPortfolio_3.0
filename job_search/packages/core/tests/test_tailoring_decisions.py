"""Unit tests for the orphan link/reject rules."""

from __future__ import annotations

import unittest
import uuid

from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.tailoring.assemble import assemble
from core.tailoring.decisions import DecisionError, apply_decision
from core.tailoring.schema import (
    TailorBullet,
    TailorExperience,
    TailorOutput,
    TailorSummary,
)
from core.tailoring.store import StoredOrphan


def _orphan(**overrides) -> StoredOrphan:
    base = dict(
        id=uuid.uuid4(),
        tailored_cv_id=uuid.uuid4(),
        kind="orphan",
        section="experience",
        experience_index=0,
        bullet_index=1,
        text="Led a team of 12",
        claimed_refs=[],
        issue=None,
        status="pending",
        evidence_ref=None,
    )
    base.update(overrides)
    return StoredOrphan(**base)


class TestApplyDecision(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        self.ref_old = bullet_id(self.truth_base, 1, 0)
        output = TailorOutput(
            summary=TailorSummary(text="World-class leader.", evidence_refs=[]),
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[
                        TailorBullet(
                            text="Built dbt models for risk reporting",
                            evidence_refs=[self.ref0],
                        ),
                        TailorBullet(text="Led a team of 12", evidence_refs=[]),
                        TailorBullet(
                            text="Led 12 engineers using dbt", evidence_refs=[self.ref0]
                        ),
                        TailorBullet(text="Also invented", evidence_refs=[]),
                    ],
                )
            ],
        )
        self.document = assemble(self.truth_base, output, target_title="T")

    def test_link_attaches_the_chosen_same_role_bullet(self) -> None:
        result = apply_decision(
            self.document,
            _orphan(),
            action="link",
            evidence_ref=self.ref0,
            truth_base=self.truth_base,
        )
        bullet = result.document.experience[0].bullets[1]
        self.assertEqual(bullet.evidence_refs, [self.ref0])
        self.assertEqual(bullet.origin, "linked")
        self.assertEqual(bullet.text, "Led a team of 12")
        self.assertIsNone(result.removed_position)

    def test_link_does_not_mutate_the_input_document(self) -> None:
        apply_decision(
            self.document,
            _orphan(),
            action="link",
            evidence_ref=self.ref0,
            truth_base=self.truth_base,
        )
        self.assertEqual(self.document.experience[0].bullets[1].origin, "orphan")

    def test_link_to_an_unknown_bullet_is_refused(self) -> None:
        with self.assertRaises(DecisionError):
            apply_decision(
                self.document,
                _orphan(),
                action="link",
                evidence_ref="nope",
                truth_base=self.truth_base,
            )

    def test_link_without_a_ref_is_refused(self) -> None:
        with self.assertRaises(DecisionError):
            apply_decision(
                self.document,
                _orphan(),
                action="link",
                evidence_ref=None,
                truth_base=self.truth_base,
            )

    def test_link_to_another_roles_bullet_is_refused(self) -> None:
        with self.assertRaises(DecisionError):
            apply_decision(
                self.document,
                _orphan(),
                action="link",
                evidence_ref=self.ref_old,
                truth_base=self.truth_base,
            )

    def test_reject_removes_an_orphan_and_reports_the_position(self) -> None:
        result = apply_decision(
            self.document,
            _orphan(),
            action="reject",
            evidence_ref=None,
            truth_base=self.truth_base,
        )
        texts = [b.text for b in result.document.experience[0].bullets]
        self.assertNotIn("Led a team of 12", texts)
        self.assertEqual(result.removed_position, (0, 1))

    def test_reject_of_an_unsupported_single_source_bullet_reverts_it(self) -> None:
        orphan = _orphan(
            kind="unsupported",
            bullet_index=2,
            text="Led 12 engineers using dbt",
            claimed_refs=[self.ref0],
        )
        result = apply_decision(
            self.document,
            orphan,
            action="reject",
            evidence_ref=None,
            truth_base=self.truth_base,
        )
        bullet = result.document.experience[0].bullets[2]
        self.assertEqual(bullet.text, "Built dbt models for risk reporting")
        self.assertEqual(bullet.origin, "original")
        self.assertEqual(bullet.evidence_refs, [self.ref0])
        self.assertIsNone(result.removed_position)

    def test_reject_of_an_unsupported_bullet_without_one_source_removes_it(
        self,
    ) -> None:
        orphan = _orphan(
            kind="unsupported",
            bullet_index=2,
            text="Led 12 engineers using dbt",
            claimed_refs=[self.ref0, bullet_id(self.truth_base, 0, 1)],
        )
        result = apply_decision(
            self.document,
            orphan,
            action="reject",
            evidence_ref=None,
            truth_base=self.truth_base,
        )
        self.assertEqual(result.removed_position, (0, 2))

    def test_a_stale_orphan_is_refused(self) -> None:
        with self.assertRaises(DecisionError):
            apply_decision(
                self.document,
                _orphan(text="something the document no longer says"),
                action="reject",
                evidence_ref=None,
                truth_base=self.truth_base,
            )

    def test_an_out_of_range_position_is_refused(self) -> None:
        with self.assertRaises(DecisionError):
            apply_decision(
                self.document,
                _orphan(bullet_index=99),
                action="reject",
                evidence_ref=None,
                truth_base=self.truth_base,
            )

    def test_an_unknown_action_is_refused(self) -> None:
        with self.assertRaises(DecisionError):
            apply_decision(
                self.document,
                _orphan(),
                action="shrug",
                evidence_ref=None,
                truth_base=self.truth_base,
            )

    def test_link_on_the_summary_accepts_any_valid_bullet(self) -> None:
        orphan = _orphan(
            section="summary",
            experience_index=None,
            bullet_index=None,
            text="World-class leader.",
        )
        result = apply_decision(
            self.document,
            orphan,
            action="link",
            evidence_ref=self.ref_old,
            truth_base=self.truth_base,
        )
        self.assertEqual(result.document.summary.origin, "linked")
        self.assertEqual(result.document.summary.evidence_refs, [self.ref_old])

    def test_reject_on_the_summary_restores_the_truth_base_summary(self) -> None:
        orphan = _orphan(
            section="summary",
            experience_index=None,
            bullet_index=None,
            text="World-class leader.",
        )
        result = apply_decision(
            self.document,
            orphan,
            action="reject",
            evidence_ref=None,
            truth_base=self.truth_base,
        )
        self.assertEqual(result.document.summary.text, self.truth_base.summary)
        self.assertEqual(result.document.summary.origin, "original")

    def test_reject_on_the_summary_removes_it_when_the_cv_has_none(self) -> None:
        self.truth_base.summary = None
        orphan = _orphan(
            section="summary",
            experience_index=None,
            bullet_index=None,
            text="World-class leader.",
        )
        result = apply_decision(
            self.document,
            orphan,
            action="reject",
            evidence_ref=None,
            truth_base=self.truth_base,
        )
        self.assertIsNone(result.document.summary)


if __name__ == "__main__":
    unittest.main()
