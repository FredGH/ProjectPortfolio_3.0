"""DB-backed tests for loading the evidence pack (real Postgres)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.company_intel_fixtures import make_job
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.tailoring_fixtures import make_truth_base

from core.artefacts.context import load_pack, saved_intel_for
from core.company_intel import store as intel_store
from core.company_intel.keys import company_key
from core.company_intel.schema import IntelContent, IntelFact
from core.cv.store import write_truth_base

_COMPANY = "Zzfixture Artefacts Ltd"


class TestLoadPack(unittest.TestCase):
    """load_pack reads the user's CV and the saved research, never researches."""

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
                    "VALUES (:id, :e, 'zzfixture')"
                ),
                {"id": self.user, "e": f"zzfixture-{self.user}@example.com"},
            )
        write_truth_base(self.engine, self.user, "md", make_truth_base())
        self.key = company_key(_COMPANY)

    def tearDown(self) -> None:
        """Remove the fixture rows."""
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :u"),
                {"u": self.user},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :u"), {"u": self.user}
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :u"), {"u": self.user})
            conn.execute(
                text("DELETE FROM intel.company_intel WHERE company_key = :k"),
                {"k": self.key},
            )

    def _save_intel(self) -> None:
        """Save one intel version for the fixture company."""
        intel_store.append_intel(
            self.engine,
            company_key=self.key,
            display_name=_COMPANY,
            status="ok",
            content=IntelContent(
                business_model=[
                    IntelFact(text="Sells widgets.", source_url="https://x.example")
                ],
            ),
            pages=[],
            resolved_from_job="zzfixture-intel-job",
            model="m",
            prompt_version="claude.v1",
            usage={"calls": []},
        )

    def test_saved_site_facts_become_site_evidence_with_the_intel_version(self) -> None:
        self._save_intel()
        loaded = load_pack(self.engine, self.user, make_job(company=_COMPANY))
        assert loaded is not None
        self.assertEqual(loaded.intel_version, 1)
        self.assertEqual(loaded.truth_base_version, 1)
        self.assertEqual(
            loaded.pack.items["site:business_model:0"].text, "Sells widgets."
        )

    def test_no_saved_research_gives_no_site_evidence_and_no_intel_version(
        self,
    ) -> None:
        loaded = load_pack(self.engine, self.user, make_job(company=_COMPANY))
        assert loaded is not None
        self.assertIsNone(loaded.intel_version)
        self.assertFalse(loaded.pack.has_site)
        self.assertIsNone(saved_intel_for(self.engine, make_job(company=_COMPANY)))

    def test_a_user_with_no_cv_gets_none(self) -> None:
        self.assertIsNone(
            load_pack(self.engine, uuid.uuid4(), make_job(company=_COMPANY))
        )


if __name__ == "__main__":
    unittest.main()
