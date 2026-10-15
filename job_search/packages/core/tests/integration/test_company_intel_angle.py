"""Tests for the per-job competitive angle."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from sqlalchemy import text
from tests.company_intel_fixtures import FakeAdapter, make_job
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.company_intel.angle import get_or_create_angle, guard_angle
from core.company_intel.schema import Competitor, IntelContent, IntelFact, StoredIntel

_KEY = "zzfixture-angle-key"
_JOB = "zzfixture-intel-job"


def _intel(version: int = 1, status: str = "ok", empty: bool = False) -> StoredIntel:
    """Build a stored intel with one competitor.

    Args:
        version: The version number.
        status: The status.
        empty: Whether the profile has no content at all.

    Returns:
        A `StoredIntel`.
    """
    content = (
        IntelContent()
        if empty
        else IntelContent(
            business_model=[
                IntelFact(text="Sells widgets.", source_url="https://x.example")
            ],
            competitors=[Competitor(name="Rival", basis="model_knowledge")],
        )
    )
    return StoredIntel(
        company_key=_KEY,
        version=version,
        display_name="Zz Co",
        status=status,  # type: ignore[arg-type]
        researched_at=datetime(2026, 10, 14, tzinfo=UTC),
        content=content,
        pages=[],
        resolved_from_job=_JOB,
        model="m",
        prompt_version="claude.v1",
        usage={},
    )


def _reply(advantages: list[dict] | None = None) -> str:
    """Build an angle reply.

    Args:
        advantages: The advantage items.

    Returns:
        A JSON string.
    """
    return json.dumps(
        {
            "transformation": "Moves from batch ETL to a modern stack.",
            "advantages": (
                advantages
                if advantages is not None
                else [{"competitor": "Rival", "point": "Fresher data for customers."}]
            ),
            "caveats": ["Based on one job description."],
        }
    )


class TestGuardAngle(unittest.TestCase):
    """guard_angle keeps only grounded, well-formed items."""

    def test_an_unlisted_competitor_is_dropped_and_the_field_is_allowed(self) -> None:
        content = guard_angle(
            json.loads(
                _reply(
                    [
                        {"competitor": "Rival", "point": "A."},
                        {"competitor": "Invented Inc", "point": "B."},
                        {"competitor": "", "point": "C."},
                        {"competitor": "rival", "point": ""},
                    ]
                )
            ),
            {"rival"},
        )
        self.assertEqual(
            [(a.competitor, a.point) for a in content.advantages],
            [("Rival", "A."), ("", "C.")],
        )

    def test_at_most_five_advantages(self) -> None:
        items = [{"competitor": "", "point": f"p{i}"} for i in range(9)]
        self.assertEqual(len(guard_angle({"advantages": items}, set()).advantages), 5)

    def test_junk_gives_an_empty_angle(self) -> None:
        self.assertEqual(guard_angle("x", set()).advantages, [])
        self.assertEqual(
            guard_angle({"transformation": 5, "caveats": "no"}, set()).transformation,
            "",
        )


class TestGetOrCreateAngle(unittest.TestCase):
    """The angle is written once per intel version."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove fixture angles."""
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM intel.job_competitive_angle WHERE company_key = :k"),
                {"k": _KEY},
            )

    def test_it_writes_once_per_version_and_reuses_it(self) -> None:
        adapter = FakeAdapter([_reply()])
        adapters = {"anthropic": adapter}
        first = get_or_create_angle(
            self.engine, make_job(), _intel(1), adapters=adapters
        )
        again = get_or_create_angle(
            self.engine, make_job(), _intel(1), adapters=adapters
        )
        assert first is not None and again is not None
        self.assertEqual(len(adapter.prompts), 1)
        self.assertEqual(first.content.advantages[0].competitor, "Rival")
        self.assertEqual(again.created_at, first.created_at)
        self.assertIn("Sells widgets.", adapter.prompts[0])
        self.assertIn("Build dbt and Snowflake pipelines", adapter.prompts[0])

    def test_a_new_intel_version_gets_a_new_angle(self) -> None:
        adapter = FakeAdapter([_reply()])
        adapters = {"anthropic": adapter}
        get_or_create_angle(self.engine, make_job(), _intel(1), adapters=adapters)
        second = get_or_create_angle(
            self.engine, make_job(), _intel(2), adapters=adapters
        )
        assert second is not None
        self.assertEqual((len(adapter.prompts), second.intel_version), (2, 2))

    def test_force_writes_another_angle_for_the_same_version(self) -> None:
        adapter = FakeAdapter([_reply()])
        adapters = {"anthropic": adapter}
        get_or_create_angle(self.engine, make_job(), _intel(1), adapters=adapters)
        get_or_create_angle(
            self.engine, make_job(), _intel(1), adapters=adapters, force=True
        )
        self.assertEqual(len(adapter.prompts), 2)

    def test_an_empty_intel_gives_no_angle_and_no_call(self) -> None:
        adapter = FakeAdapter([_reply()])
        found = get_or_create_angle(
            self.engine,
            make_job(),
            _intel(1, "no_content", empty=True),
            adapters={"anthropic": adapter},
        )
        self.assertIsNone(found)
        self.assertEqual(adapter.prompts, [])

    def test_an_unreadable_reply_gives_no_angle(self) -> None:
        found = get_or_create_angle(
            self.engine,
            make_job(),
            _intel(1),
            adapters={"anthropic": FakeAdapter(["nope"])},
        )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
