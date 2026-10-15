"""DB-backed tests for the artefact store (real Postgres)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.artefacts_fixtures import TODAY, letter_reply_json, make_pack
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.artefacts import store
from core.artefacts.assemble import build_letter, parse_writer_reply
from core.artefacts.decisions import apply_decision
from core.artefacts.schema import CoverLetter

_JOB = "zzfixture-artefacts-job"


class TestArtefactStore(unittest.TestCase):
    """Save, read and decide, per user."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def setUp(self) -> None:
        """Create two fixture users and a letter."""
        self.user, self.other = uuid.uuid4(), uuid.uuid4()
        with self.owner.begin() as conn:
            for u in (self.user, self.other):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:i, :e, 'zzfixture')"
                    ),
                    {"i": u, "e": f"zzfixture-{u}@example.com"},
                )
        self.letter = build_letter(
            make_pack(),
            parse_writer_reply(letter_reply_json(), "cover_letter"),
            today=TODAY,
        )

    def tearDown(self) -> None:
        """Remove the fixture rows."""
        with self.owner.begin() as conn:
            for table in (
                "artefact_open_sentence",
                "artefact_cover_letter",
                "artefact_pitch",
            ):
                conn.execute(
                    text(f"DELETE FROM artefacts.{table} WHERE user_id IN (:a, :b)"),
                    {"a": self.user, "b": self.other},
                )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user, "b": self.other},
            )

    def _save(self, status: str = "needs_review", open_count: int = 1, user=None):  # type: ignore[no-untyped-def]
        """Save a letter with `open_count` open sentences.

        Args:
            status: The artefact status.
            open_count: How many open sentences to attach.
            user: The owner (default: the fixture user).

        Returns:
            The new artefact id.
        """
        sentences = [
            s for p in self.letter.paragraphs for s in p.sentences if s.kind == "claim"
        ]
        drafts = [
            store.OpenDraft(
                s.id, f"evidence:{i}", "unsupported", s.text, s.sources, "adds a metric"
            )
            for i, s in enumerate(sentences[:open_count])
        ]
        return store.save_artefact(
            self.engine,
            user or self.user,
            "cover_letter",
            job_group_id=_JOB,
            truth_base_version=3,
            intel_version=2,
            target_title="Data Engineer",
            status=status,
            document=self.letter,
            attempts=2,
            writer_model="m",
            writer_prompt_version="claude.v1",
            critic_model="m",
            critic_prompt_version="claude.v1",
            usage={"calls": [], "cost_usd": 0.01},
            error_message=None,
            open_sentences=drafts,
        )

    def test_a_saved_artefact_reads_back_with_its_open_sentences(self) -> None:
        artefact_id = self._save()
        read = store.read_latest(self.engine, self.user, "cover_letter", _JOB)
        assert read is not None
        self.assertEqual(
            (read.id, read.status, read.truth_base_version, read.intel_version),
            (artefact_id, "needs_review", 3, 2),
        )
        self.assertEqual(
            CoverLetter.model_validate(read.content).reference, "Re: Data Engineer"
        )
        self.assertEqual(len(read.open_sentences), 1)
        self.assertEqual(read.open_sentences[0].status, "pending")
        self.assertEqual(read.open_sentences[0].issue, "adds a metric")

    def test_the_latest_row_wins(self) -> None:
        self._save()
        second = self._save(status="approved", open_count=0)
        read = store.read_latest(self.engine, self.user, "cover_letter", _JOB)
        assert read is not None
        self.assertEqual(read.id, second)

    def test_another_user_sees_nothing(self) -> None:
        artefact_id = self._save()
        self.assertIsNone(
            store.read_latest(self.engine, self.other, "cover_letter", _JOB)
        )
        self.assertIsNone(
            store.read_artefact(self.engine, self.other, "cover_letter", artefact_id)
        )

    def test_linking_the_last_pending_sentence_approves_the_artefact(self) -> None:
        self._save()
        read = store.read_latest(self.engine, self.user, "cover_letter", _JOB)
        assert read is not None
        pending = read.open_sentences[0]
        base = CoverLetter.model_validate(read.content)
        new_doc = apply_decision(
            base,
            pending.sentence_id,
            action="link",
            evidence_ref="job:posting",
            pack=make_pack(),
        )
        status = store.save_decision(
            self.engine,
            self.user,
            open_sentence=pending,
            status="linked",
            evidence_ref="job:posting",
            document=new_doc,
            base_document=base,
        )
        self.assertEqual(status, "approved")
        after = store.read_artefact(self.engine, self.user, "cover_letter", read.id)
        assert after is not None
        self.assertEqual(after.status, "approved")
        self.assertEqual(after.open_sentences[0].status, "linked")

    def test_one_decision_leaves_the_artefact_in_review_while_others_are_pending(
        self,
    ) -> None:
        self._save(open_count=2)
        read = store.read_latest(self.engine, self.user, "cover_letter", _JOB)
        assert read is not None
        first = read.open_sentences[0]
        base = CoverLetter.model_validate(read.content)
        new_doc = apply_decision(
            base,
            first.sentence_id,
            action="remove",
            evidence_ref=None,
            pack=make_pack(),
        )
        status = store.save_decision(
            self.engine,
            self.user,
            open_sentence=first,
            status="removed",
            evidence_ref=None,
            document=new_doc,
            base_document=base,
        )
        self.assertEqual(status, "needs_review")

    def test_a_decision_on_a_changed_document_or_twice_is_refused(self) -> None:
        self._save()
        read = store.read_latest(self.engine, self.user, "cover_letter", _JOB)
        assert read is not None
        pending = read.open_sentences[0]
        base = CoverLetter.model_validate(read.content)
        new_doc = apply_decision(
            base,
            pending.sentence_id,
            action="remove",
            evidence_ref=None,
            pack=make_pack(),
        )
        stale_base = base.model_copy(deep=True)
        stale_base.sign_off = "Different"
        with self.assertRaises(store.StaleDecisionError):
            store.save_decision(
                self.engine,
                self.user,
                open_sentence=pending,
                status="removed",
                evidence_ref=None,
                document=new_doc,
                base_document=stale_base,
            )
        store.save_decision(
            self.engine,
            self.user,
            open_sentence=pending,
            status="removed",
            evidence_ref=None,
            document=new_doc,
            base_document=base,
        )
        with self.assertRaises(store.StaleDecisionError):
            store.save_decision(
                self.engine,
                self.user,
                open_sentence=pending,
                status="removed",
                evidence_ref=None,
                document=new_doc,
                base_document=new_doc,
            )

    def test_another_user_cannot_decide(self) -> None:
        self._save()
        read = store.read_latest(self.engine, self.user, "cover_letter", _JOB)
        assert read is not None
        pending = read.open_sentences[0]
        base = CoverLetter.model_validate(read.content)
        with self.assertRaises(store.StaleDecisionError):
            store.save_decision(
                self.engine,
                self.other,
                open_sentence=pending,
                status="removed",
                evidence_ref=None,
                document=base,
                base_document=base,
            )


if __name__ == "__main__":
    unittest.main()
