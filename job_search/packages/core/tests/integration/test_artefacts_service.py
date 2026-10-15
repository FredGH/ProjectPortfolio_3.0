"""DB-backed tests for generating, storing and deciding artefacts."""

from __future__ import annotations

import json
import unittest
import uuid

from sqlalchemy import text
from tests.artefacts_fixtures import (
    TODAY,
    ScriptedAdapter,
    cv_ref,
    padded_letter_json,
    sentence_json,
)
from tests.company_intel_fixtures import FakeAdapter, make_job
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.tailoring_fixtures import make_truth_base

from core.artefacts import service
from core.artefacts.decisions import DecisionError
from core.cv.store import write_truth_base

_COMPANY = "Zzfixture Artefacts Ltd"
_POSTING_ONLY = sentence_json(
    "You want to replace the batch ETL.", sources=["job:posting"]
)


def _letter_json(**overrides: object) -> str:
    """Build a padded letter that needs no saved company research.

    Args:
        **overrides: Per-role sentence overrides.

    Returns:
        A writer-reply JSON string whose why_company cites the posting only.
    """
    return padded_letter_json(**{"why_company": [_POSTING_ONLY], **overrides})


class TestGenerateArtefact(unittest.TestCase):
    """The service generates from the stored CV and saved research."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def setUp(self) -> None:
        """Create a fixture user with a stored CV."""
        self.user = uuid.uuid4()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:i, :e, 'zzfixture')"
                ),
                {"i": self.user, "e": f"zzfixture-{self.user}@example.com"},
            )
        write_truth_base(self.engine, self.user, "md", make_truth_base())
        self.job = make_job(company=_COMPANY, title_for_display="Data Engineer")

    def tearDown(self) -> None:
        """Remove the fixture rows."""
        with self.owner.begin() as conn:
            for table in (
                "artefact_open_sentence",
                "artefact_cover_letter",
                "artefact_pitch",
            ):
                conn.execute(
                    text(f"DELETE FROM artefacts.{table} WHERE user_id = :u"),
                    {"u": self.user},
                )
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :u"),
                {"u": self.user},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :u"), {"u": self.user}
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :u"), {"u": self.user})

    def _generate(self, adapter, job=None, user=None):  # type: ignore[no-untyped-def]
        """Generate a letter with the scripted adapter.

        Args:
            adapter: The scripted adapter.
            job: The job (default: the fixture job).
            user: The user (default: the fixture user).

        Returns:
            The stored artefact.
        """
        return service.generate_artefact(
            self.engine,
            user or self.user,
            "cover_letter",
            job or self.job,
            adapters={"anthropic": adapter},
            today=TODAY,
        )

    def test_a_clean_letter_is_stored_approved_with_the_versions_it_used(self) -> None:
        stored = self._generate(ScriptedAdapter([_letter_json()]))
        self.assertEqual(stored.status, "approved")
        self.assertEqual(stored.truth_base_version, 1)
        self.assertIsNone(stored.intel_version)
        self.assertEqual(stored.target_title, "Data Engineer")
        self.assertEqual(stored.writer_prompt_version, "claude.v2")
        self.assertEqual(stored.open_sentences, [])
        self.assertGreater(stored.usage["input_tokens"], 0)  # type: ignore[index]

    def _exaggerated(self):  # type: ignore[no-untyped-def]
        """Build an adapter whose letter exaggerates and whose critic objects.

        Returns:
            A scripted adapter.
        """
        bad = _letter_json(
            evidence=[
                sentence_json(
                    "I built the whole platform on my own.", sources=[cv_ref(0, 0)]
                )
            ]
        )
        return ScriptedAdapter([bad], reject={"on my own": "not supported"})

    def test_an_exaggeration_is_stored_for_review_not_approved(self) -> None:
        stored = self._generate(self._exaggerated())
        self.assertEqual(stored.status, "needs_review")
        self.assertEqual(
            [o.text for o in stored.open_sentences],
            ["I built the whole platform on my own."],
        )

    def test_deciding_the_last_open_sentence_approves_it(self) -> None:
        stored = self._generate(self._exaggerated())
        decided = service.decide(
            self.engine,
            self.user,
            "cover_letter",
            stored.open_sentences[0].id,
            self.job,
            action="remove",
            evidence_ref=None,
        )
        self.assertEqual(decided.status, "approved")
        self.assertNotIn("on my own", json.dumps(decided.content))

    def test_a_decision_with_another_jobs_id_is_refused(self) -> None:
        stored = self._generate(self._exaggerated())
        with self.assertRaises(DecisionError):
            service.decide(
                self.engine,
                self.user,
                "cover_letter",
                stored.open_sentences[0].id,
                make_job(job_group_id="zzfixture-other-job", company=_COMPANY),
                action="remove",
                evidence_ref=None,
            )

    def test_a_user_without_a_cv_cannot_generate(self) -> None:
        with self.assertRaises(service.NoCvError):
            self._generate(ScriptedAdapter([_letter_json()]), user=uuid.uuid4())

    def test_a_job_with_no_title_cannot_generate(self) -> None:
        with self.assertRaises(service.NoTargetTitleError):
            self._generate(
                ScriptedAdapter([_letter_json()]),
                job=make_job(title_for_display=" "),
            )

    def test_a_failed_run_is_stored_with_its_error_and_no_document(self) -> None:
        stored = self._generate(FakeAdapter(["I cannot do that"]))
        self.assertEqual(stored.status, "failed")
        self.assertIsNone(stored.content)
        self.assertIn("unusable", stored.error_message or "")

    def test_generation_never_researches_the_company(self) -> None:
        adapter = ScriptedAdapter([_letter_json()])
        self._generate(adapter)
        self.assertFalse(any("Give the official website" in p for p in adapter.prompts))
        with self.owner.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT count(*) FROM intel.company_intel WHERE display_name = :n"
                ),
                {"n": _COMPANY},
            ).scalar_one()
        self.assertEqual(rows, 0)


if __name__ == "__main__":
    unittest.main()
