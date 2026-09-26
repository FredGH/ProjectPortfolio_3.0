"""Integration tests for core.skills.llm_map against live Postgres.

Only the LLM adapter and the embedder are faked. Fixture ESCO skills:
`fixture-cloud` sits on embedding axis 0, `fixture-python` on axis 1, so a
string embedded on axis 0 has `fixture-cloud` as candidate 1 (score 1.0).
"""

from __future__ import annotations

import json
import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    axis_vector,
    insert_mapping,
    live_owner_engine,
    purge_fixtures,
)

from core.llm.types import LLMResponse
from core.settings import get_settings
from core.skills.esco_load import load_esco
from core.skills.llm_map import (
    count_eligible,
    evaluate_against_resolved,
    propose_matches,
)
from core.skills.vector import to_pgvector

_MODEL = get_settings().embedding_model


def _embed_on_axis_0(_text: str) -> list[float]:
    return axis_vector(0)


class _FakeAdapter:
    """Replies with canned text; records every prompt it was sent."""

    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(
            text=reply,
            provider="anthropic",
            model=model,
            input_tokens=10,
            output_tokens=5,
        )


def _reply(*entries: dict) -> str:
    return json.dumps({"results": list(entries)})


def _match(n: int, candidate: int = 1, confidence: str = "high") -> dict:
    return {
        "n": n,
        "verdict": "match",
        "candidate": candidate,
        "confidence": confidence,
        "custom_label": None,
        "note": "same",
    }


class TestProposeMatches(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.begin() as conn:
            for skill_id, axis in (("fixture-cloud", 0), ("fixture-python", 1)):
                conn.execute(
                    text(
                        "INSERT INTO esco.skill_embedding "
                        "(skill_id, embedding_model, embedding) "
                        "VALUES (:id, :m, CAST(:v AS vector))"
                    ),
                    {"id": skill_id, "m": _MODEL, "v": to_pgvector(axis_vector(axis))},
                )
            for name in ("a", "b"):
                insert_mapping(conn, f"zzfixture llm {name}", review_status="open")
        self.norms = ["zzfixture llm a", "zzfixture llm b"]

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _run(self, adapter: _FakeAdapter, **kwargs):
        return propose_matches(
            self.engine,
            adapters={"anthropic": adapter},
            embed=_embed_on_axis_0,
            embedding_model=_MODEL,
            raw_norms=self.norms,
            **kwargs,
        )

    def _row(self, raw_norm: str):
        with self.engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT skill_id, method, review_status, llm_verdict, "
                    "llm_custom_label, llm_note, llm_checked_at "
                    "FROM silver.skill_mapping WHERE raw_norm = :n"
                ),
                {"n": raw_norm},
            ).one()

    def test_high_confidence_match_is_applied_as_an_llm_mapping(self) -> None:
        adapter = _FakeAdapter([_reply(_match(1), _match(2, confidence="low"))])
        summary = self._run(adapter)
        first = self._row("zzfixture llm a")
        self.assertEqual(
            (first.skill_id, first.method, first.review_status, first.llm_verdict),
            ("fixture-cloud", "llm", None, "match_high"),
        )
        self.assertEqual(
            (summary.checked, summary.applied, summary.left_open), (2, 1, 1)
        )

    def test_low_confidence_match_stays_open_with_the_verdict_recorded(self) -> None:
        self._run(_FakeAdapter([_reply(_match(1, confidence="low"), _match(2))]))
        second = self._row("zzfixture llm a")
        self.assertEqual(
            (second.skill_id, second.method, second.review_status, second.llm_verdict),
            (None, "none", "open", "match_low"),
        )
        self.assertIsNotNone(second.llm_checked_at)

    def test_no_equivalent_records_the_custom_label_and_note(self) -> None:
        entry = {
            "n": 1,
            "verdict": "no_equivalent",
            "candidate": None,
            "confidence": None,
            "custom_label": "GRPO",
            "note": "RL method",
        }
        self._run(_FakeAdapter([_reply(entry, {"n": 2, "verdict": "unsure"})]))
        row = self._row("zzfixture llm a")
        self.assertEqual(
            (
                row.method,
                row.review_status,
                row.llm_verdict,
                row.llm_custom_label,
                row.llm_note,
            ),
            ("none", "open", "no_equivalent", "GRPO", "RL method"),
        )
        self.assertEqual(self._row("zzfixture llm b").llm_verdict, "unsure")

    def test_a_candidate_number_outside_the_offered_range_is_unsure(self) -> None:
        self._run(_FakeAdapter([_reply(_match(1, candidate=9), _match(2))]))
        row = self._row("zzfixture llm a")
        self.assertEqual((row.method, row.llm_verdict), ("none", "unsure"))

    def test_a_string_missing_from_the_reply_stays_unchecked_and_retried(
        self,
    ) -> None:
        summary = self._run(_FakeAdapter([_reply(_match(1))]))  # no entry for 2
        self.assertEqual(summary.failed, 1)
        self.assertIsNone(self._row("zzfixture llm b").llm_checked_at)
        retry = self._run(_FakeAdapter([_reply(_match(1))]))
        self.assertEqual(retry.checked, 1)
        self.assertIsNotNone(self._row("zzfixture llm b").llm_checked_at)

    def test_malformed_json_and_adapter_errors_leave_strings_unchecked(self) -> None:
        for reply in ("not json at all", RuntimeError("api down")):
            summary = self._run(_FakeAdapter([reply]))
            self.assertEqual((summary.checked, summary.failed), (0, 2))
        self.assertIsNone(self._row("zzfixture llm a").llm_checked_at)

    def test_a_checked_string_is_not_asked_again(self) -> None:
        self._run(_FakeAdapter([_reply(_match(1), _match(2))]))
        adapter = _FakeAdapter([])  # any call would raise IndexError
        summary = self._run(adapter)
        self.assertEqual(summary.checked, 0)
        self.assertEqual(adapter.prompts, [])

    def test_human_decisions_are_never_touched(self) -> None:
        with self.engine.begin() as conn:
            for name, status in (
                ("res", "resolved"),
                ("rej", "rejected"),
                ("dis", "dismissed"),
            ):
                insert_mapping(
                    conn,
                    f"zzfixture llm {name}",
                    review_status=status,
                    skill_id="fixture-python" if status == "resolved" else None,
                    method="alias" if status == "resolved" else "none",
                )
        propose_matches(
            self.engine,
            adapters={"anthropic": _FakeAdapter([_reply(_match(1), _match(2))])},
            embed=_embed_on_axis_0,
            embedding_model=_MODEL,
            raw_norms=self.norms
            + ["zzfixture llm res", "zzfixture llm rej", "zzfixture llm dis"],
        )
        self.assertEqual(self._row("zzfixture llm res").skill_id, "fixture-python")
        for name in ("rej", "dis"):
            self.assertIsNone(self._row(f"zzfixture llm {name}").llm_checked_at)

    def test_a_decision_made_mid_batch_wins_over_the_models_answer(self) -> None:
        class _DecidingAdapter(_FakeAdapter):
            def complete(inner, **kwargs):  # noqa: N805
                with self.engine.begin() as conn:
                    conn.execute(
                        text(
                            "UPDATE silver.skill_mapping SET review_status = "
                            "'dismissed' WHERE raw_norm = 'zzfixture llm a'"
                        )
                    )
                return super().complete(**kwargs)

        self._run(_DecidingAdapter([_reply(_match(1), _match(2))]))
        row = self._row("zzfixture llm a")
        self.assertEqual((row.method, row.review_status), ("none", "dismissed"))

    def test_the_limit_caps_how_many_strings_are_sent(self) -> None:
        adapter = _FakeAdapter([_reply(_match(1))])
        summary = self._run(adapter, limit=1)
        self.assertEqual(summary.checked, 1)
        self.assertEqual(count_eligible(self.engine, raw_norms=self.norms), 1)

    def test_the_prompt_lists_original_spelling_and_candidate_labels(self) -> None:
        adapter = _FakeAdapter([_reply(_match(1), _match(2))])
        self._run(adapter)
        self.assertIn("zzfixture llm a", adapter.prompts[0])
        self.assertIn("1)", adapter.prompts[0])


class TestEvaluate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.begin() as conn:
            for skill_id, axis in (("fixture-cloud", 0), ("fixture-python", 1)):
                conn.execute(
                    text(
                        "INSERT INTO esco.skill_embedding "
                        "(skill_id, embedding_model, embedding) "
                        "VALUES (:id, :m, CAST(:v AS vector))"
                    ),
                    {"id": skill_id, "m": _MODEL, "v": to_pgvector(axis_vector(axis))},
                )
            # A person resolved "a" to fixture-cloud and "b" to fixture-python.
            insert_mapping(
                conn,
                "zzfixture eval a",
                skill_id="fixture-cloud",
                method="alias",
                review_status="resolved",
            )
            insert_mapping(
                conn,
                "zzfixture eval b",
                skill_id="fixture-python",
                method="alias",
                review_status="resolved",
            )

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def test_reports_agreement_on_high_confidence_picks_and_writes_nothing(
        self,
    ) -> None:
        # Candidate 1 for both strings is fixture-cloud: right for "a", wrong for "b".
        adapter = _FakeAdapter([_reply(_match(1), _match(2))])
        count_sql = text(
            "SELECT count(*) FROM silver.skill_mapping "
            "WHERE llm_checked_at IS NOT NULL"
        )
        with self.engine.connect() as conn:
            before = conn.execute(count_sql).scalar_one()
        report = evaluate_against_resolved(
            self.engine,
            adapters={"anthropic": adapter},
            embed=_embed_on_axis_0,
            embedding_model=_MODEL,
            sample=2,
            raw_norms=["zzfixture eval a", "zzfixture eval b"],
        )
        self.assertEqual((report.high_matches, report.high_agree), (2, 1))
        self.assertEqual(report.agreement, 0.5)
        with self.engine.connect() as conn:
            after = conn.execute(count_sql).scalar_one()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
