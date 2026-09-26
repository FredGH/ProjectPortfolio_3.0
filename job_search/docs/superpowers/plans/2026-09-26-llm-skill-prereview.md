# LLM Skill Pre-review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Claude pre-review the ~5,000 open unmapped skill strings so a person only handles the unsure / "no ESCO equivalent" cases and spot-checks the applied matches.

**Architecture:** A new core module `core/skills/llm_map.py` takes each `open`, not-yet-checked string, embeds it, fetches its 5 nearest ESCO skills, asks Claude (through the existing LLM gateway + prompt registry) to pick one / say none / say unsure, and either applies a high-confidence pick as `method='llm'` (which lands in the existing "Auto-matches — verify" list) or records the verdict as a note on the still-open row. It is driven by a `pipeline llm-map-skills` CLI (with an `--evaluate` accuracy gate) and, capped and best-effort, by the post-run mapping hook.

**Tech Stack:** Python 3.11, SQLAlchemy Core (`text()`), Alembic, Postgres/pgvector, FastAPI, Streamlit, `unittest` on a real Postgres, Anthropic Haiku 4.5 via `core.llm.gateway`.

**Spec:** `docs/superpowers/specs/2026-09-26-llm-skill-prereview-design.md`

## Global Constraints

- Python 3.11; black (88 cols), isort (profile black), ruff clean; Google-style docstrings with `Args/Returns/Raises` on every function and class; type hints on public signatures; no bare `except:`.
- Prompts are versioned files under `prompts/<task>/<family>.v<N>.md`, never inline in Python. Model routing lives in `config/llm_tasks.yml`, never hardcoded.
- Model: `claude-haiku-4-5-20251001`, task `skill_mapping`, prompt family `claude`.
- Tests: `unittest`, real Postgres, **no DB mocking**; only the LLM adapter (and the embedder) are faked. Fixture strings start with `zzfixture` so `purge_fixtures` removes them; fixture ESCO skills are `fixture-cloud` (embedding axis 0) and `fixture-python` (axis 1).
- Only touch rows with `review_status = 'open'`. `resolved`, `rejected`, `dismissed` rows are never changed by this feature.
- Only skill strings and ESCO labels go to the API — never job or CV text.
- A model reply may only pick one of the 5 offered candidates; anything else is `unsure`.
- Post-run hook: capped at 300 strings per run; failures never fail the run; no `ANTHROPIC_API_KEY` → step skipped with a note.
- Run tests from `job_search/packages/core` as `../../venv/bin/python -m unittest <module> -v` (add `PYTHONPATH=.` if `tests` is not importable).
- Prerequisite: PR #47 (search boxes on the Skill Review page) is merged and this branch rebased on `main` before Task 5 — both edit `apps/ui/app/pages/6_Skill_Review.py`.

## Review Focus

- A model reply that is not JSON, omits some strings, repeats an index, or names a candidate number outside 1–5 → those strings stay unchecked (or `unsure`); nothing is misapplied. (Task 2)
- A human resolves/rejects/dismisses a string while a batch is in flight → the model's write is skipped (`WHERE review_status='open'` guard), the human decision survives. (Task 2)
- `map-skills --remap-all-auto` deletes rows with `review_status IS NULL`; it must not wipe paid-for `llm` matches. (Task 1)
- Rejecting an `llm` match must send it to `rejected` and never be re-proposed to the model. (Task 1, 2)
- The CLI/hook run with no Anthropic key or with the embedder down → clear message, no traceback, exit code / summary reflect it. (Task 3, 4)

---

### Task 1: Migration + review/mapper integration

**Files:**
- Create: `db/migrations/versions/0027_add_llm_columns_to_skill_mapping.py`
- Modify: `packages/core/core/skills/review.py` (`_AUTO_METHODS`, `ReviewItem`, `MatchItem`, `list_unmapped`, `list_auto_matches`, `_match_sort_key`)
- Modify: `packages/core/core/skills/mapper.py` (`remap_all_auto`)
- Modify: `apps/api/app/routers/skills.py` (`ReviewItemModel`, `MatchItemModel`)
- Test: `packages/core/tests/integration/test_llm_review.py`

**Interfaces:**
- Produces: table columns `silver.skill_mapping.llm_verdict text`, `llm_custom_label text`, `llm_note text`, `llm_checked_at timestamptz`; `method='llm'` allowed. `ReviewItem` gains `llm_verdict: str | None = None`, `llm_custom_label: str | None = None`, `llm_note: str | None = None`. `MatchItem` gains `llm_note: str | None = None`. Later tasks rely on these exact names.

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/integration/test_llm_review.py`:

```python
"""Integration tests: how `llm` mappings behave in the review flow."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    insert_mapping,
    live_owner_engine,
    purge_fixtures,
)

from core.skills import review
from core.skills.esco_load import load_esco
from core.skills.mapper import remap_all_auto


def _insert_llm_match(conn, raw_norm: str, note: str = "same skill") -> None:
    insert_mapping(
        conn,
        raw_norm,
        skill_id="fixture-cloud",
        method="llm",
        score=0.8,
        review_status=None,
    )
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET llm_verdict = 'match_high', "
            "llm_note = :note, llm_checked_at = now() WHERE raw_norm = :n"
        ),
        {"note": note, "n": raw_norm},
    )


class TestLlmReviewFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def test_llm_match_is_listed_for_verification_with_its_note(self) -> None:
        with self.engine.begin() as conn:
            _insert_llm_match(conn, "zzfixture llm one", note="cloud platform")
        with self.engine.connect() as conn:
            items = review.list_auto_matches(conn, query="zzfixture llm one")
        self.assertEqual(len(items), 1)
        self.assertEqual(
            (items[0].method, items[0].skill_id, items[0].llm_note),
            ("llm", "fixture-cloud", "cloud platform"),
        )

    def test_confirming_an_llm_match_creates_a_review_alias(self) -> None:
        with self.engine.begin() as conn:
            _insert_llm_match(conn, "zzfixture llm two")
        with self.engine.begin() as conn:
            review.resolve_to_skill(conn, "zzfixture llm two", "fixture-cloud")
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT a.source, m.method, m.review_status "
                    "FROM silver.skill_alias a JOIN silver.skill_mapping m "
                    "ON m.raw_norm = a.alias_norm WHERE a.alias_norm = :n"
                ),
                {"n": "zzfixture llm two"},
            ).one()
        self.assertEqual(tuple(row), ("review", "alias", "resolved"))

    def test_rejecting_an_llm_match_returns_it_to_the_unmapped_list(self) -> None:
        with self.engine.begin() as conn:
            _insert_llm_match(conn, "zzfixture llm three")
        with self.engine.begin() as conn:
            review.reject_auto_match(conn, "zzfixture llm three")
        with self.engine.connect() as conn:
            items = review.list_unmapped(conn, query="zzfixture llm three")
            checked = conn.execute(
                text(
                    "SELECT llm_checked_at IS NOT NULL FROM silver.skill_mapping "
                    "WHERE raw_norm = :n"
                ),
                {"n": "zzfixture llm three"},
            ).scalar_one()
        self.assertEqual([i.review_status for i in items], ["rejected"])
        self.assertTrue(checked)  # so the model is never asked about it again

    def test_unmapped_item_carries_the_models_note_and_custom_label(self) -> None:
        with self.engine.begin() as conn:
            insert_mapping(conn, "zzfixture llm four", review_status="open")
            conn.execute(
                text(
                    "UPDATE silver.skill_mapping SET llm_verdict = 'no_equivalent', "
                    "llm_custom_label = 'GRPO', llm_note = 'RL method', "
                    "llm_checked_at = now() WHERE raw_norm = :n"
                ),
                {"n": "zzfixture llm four"},
            )
        with self.engine.connect() as conn:
            (item,) = review.list_unmapped(conn, query="zzfixture llm four")
        self.assertEqual(
            (item.llm_verdict, item.llm_custom_label, item.llm_note),
            ("no_equivalent", "GRPO", "RL method"),
        )

    def test_remap_all_auto_keeps_llm_matches(self) -> None:
        with self.engine.begin() as conn:
            _insert_llm_match(conn, "zzfixture llm five")
        remap_all_auto(self.engine, raw_norms=["zzfixture llm five"])
        with self.engine.connect() as conn:
            method = conn.execute(
                text("SELECT method FROM silver.skill_mapping WHERE raw_norm = :n"),
                {"n": "zzfixture llm five"},
            ).scalar_one_or_none()
        self.assertEqual(method, "llm")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_llm_review -v`
Expected: FAIL/ERROR — the `llm_verdict` column and `llm` method do not exist yet.

- [ ] **Step 3: Write the migration**

Create `db/migrations/versions/0027_add_llm_columns_to_skill_mapping.py`:

```python
"""Add the LLM pre-review columns and the 'llm' method to skill_mapping.

An LLM pre-review (`core.skills.llm_map`) either applies a high-confidence
ESCO match — stored as `method = 'llm'`, `review_status` NULL, so it shows up
in the "Auto-matches — verify" list — or records its verdict on a still-open
row. `llm_checked_at` marks a string as already asked so it is not sent again.

`job_search_app` already has UPDATE on `silver.skill_mapping` (0023), so no
new grants are needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_skill_mapping_method", "skill_mapping", schema="silver", type_="check"
    )
    op.create_check_constraint(
        "ck_skill_mapping_method",
        "skill_mapping",
        "method IN ('alias', 'label', 'embedding', 'llm', 'none')",
        schema="silver",
    )
    op.add_column(
        "skill_mapping", sa.Column("llm_verdict", sa.Text()), schema="silver"
    )
    op.add_column(
        "skill_mapping", sa.Column("llm_custom_label", sa.Text()), schema="silver"
    )
    op.add_column("skill_mapping", sa.Column("llm_note", sa.Text()), schema="silver")
    op.add_column(
        "skill_mapping",
        sa.Column("llm_checked_at", sa.DateTime(timezone=True)),
        schema="silver",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE silver.skill_mapping SET method = 'embedding' WHERE method = 'llm'"
    )
    for column in ("llm_checked_at", "llm_note", "llm_custom_label", "llm_verdict"):
        op.drop_column("skill_mapping", column, schema="silver")
    op.drop_constraint(
        "ck_skill_mapping_method", "skill_mapping", schema="silver", type_="check"
    )
    op.create_check_constraint(
        "ck_skill_mapping_method",
        "skill_mapping",
        "method IN ('alias', 'label', 'embedding', 'none')",
        schema="silver",
    )
```

Apply it to the dev DB (from `job_search/`):

```bash
set -a; source .env; set +a
DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}" venv/bin/alembic -c db/alembic.ini upgrade head
```

Expected: `Running upgrade 0026 -> 0027`. (If `alembic.ini` is elsewhere, `git grep -l "script_location"`.)

- [ ] **Step 4: Integrate with review and mapper**

In `packages/core/core/skills/review.py`:

1. `_AUTO_METHODS = ("embedding", "label")` → `_AUTO_METHODS = ("embedding", "label", "llm")`. Update the two docstrings that say "embedding or label" (`_require_resolvable`, `reject_auto_match`) to say "embedding, label or llm", and the `reject_auto_match` error text to `was not auto-mapped by embedding, label or llm`.
2. `ReviewItem`: after `candidate_score: float | None` add (with docstring lines under `Attributes`):
   ```python
       llm_verdict: str | None = None
       llm_custom_label: str | None = None
       llm_note: str | None = None
   ```
3. `MatchItem`: after `jd_job_count: int` add `llm_note: str | None = None` (+ docstring line).
4. `list_unmapped`: add `m.llm_verdict, m.llm_custom_label, m.llm_note, ` to the SELECT list (right after `m.candidate_skill_id, m.candidate_score, `), and add to the `ReviewItem(...)` construction `llm_verdict=r.llm_verdict, llm_custom_label=r.llm_custom_label, llm_note=r.llm_note,`.
5. `list_auto_matches`: change `WHERE m.method IN ('embedding', 'label')` to `WHERE m.method IN ('embedding', 'label', 'llm')`; add `m.llm_note, ` after `m.seen_in_cv, ` in the SELECT; add `llm_note=r.llm_note,` to `MatchItem(...)`; update the docstring "Rows whose method is `embedding`, `label` or `llm`".
6. `_match_sort_key`: `if item.method == "embedding":` → `if item.method in ("embedding", "llm"):` (score is a cosine for both; `llm` rows have a score).

In `packages/core/core/skills/mapper.py`, `remap_all_auto`, change the SQL to protect paid-for LLM matches:

```python
                "WHERE ((review_status IS NULL AND method <> 'llm') "
                "OR (method = 'none' AND review_status = 'open')) "
```
and add to its docstring: "An `llm` match is kept: it cost an API call and a person can reject it in the verify list."

In `apps/api/app/routers/skills.py`: `ReviewItemModel` add
```python
    llm_verdict: str | None = None
    llm_custom_label: str | None = None
    llm_note: str | None = None
```
and `MatchItemModel` add `llm_note: str | None = None`.

- [ ] **Step 5: Run to verify it passes, plus regressions**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_llm_review tests.integration.test_skills_router tests.integration.test_skills_mapper tests.integration.test_skills_map_strings -v`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
cd job_search && venv/bin/ruff check . && venv/bin/isort . && venv/bin/black .
git add db/migrations/versions/0027_add_llm_columns_to_skill_mapping.py packages/core/core/skills/review.py packages/core/core/skills/mapper.py apps/api/app/routers/skills.py packages/core/tests/integration/test_llm_review.py
git commit -m "feat(job_search): allow llm mappings in review flow (migration 0027)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: The proposer — prompt, task config, `llm_map.py`

**Files:**
- Create: `prompts/skill_mapping/claude.v1.md`
- Modify: `config/llm_tasks.yml`
- Create: `packages/core/core/skills/llm_map.py`
- Test: `packages/core/tests/integration/test_llm_map.py`

**Interfaces:**
- Consumes: `core.llm.gateway.complete`, `core.llm.prompts.load_prompt`, `core.llm.task_config.load_task_config`, `core.llm.json_response.parse_json_response`, `core.skills.vector.to_pgvector`; migration 0027's columns.
- Produces (used by Tasks 3–4):
  - `TASK = "skill_mapping"`, `PROMPT_VERSION = "claude.v1"`, `BATCH_SIZE = 20`, `CANDIDATES_PER_STRING = 5`
  - `@dataclass(frozen=True) Candidate(skill_id: str, label: str, score: float)`
  - `@dataclass(frozen=True) Verdict(kind: str, skill_id: str | None, score: float | None, confidence: str | None, custom_label: str | None, note: str | None)` with property `stored: str` → `"match_high" | "match_low" | "no_equivalent" | "unsure"`
  - `@dataclass(frozen=True) LlmMapSummary(checked: int, applied: int, left_open: int, failed: int, input_tokens: int, output_tokens: int)`
  - `count_eligible(engine, *, raw_norms: list[str] | None = None) -> int`
  - `propose_matches(engine, *, adapters, embed, embedding_model, limit=None, batch_size=BATCH_SIZE, raw_norms=None, config_path=None) -> LlmMapSummary`

- [ ] **Step 1: Write the prompt and task config**

Create `prompts/skill_mapping/claude.v1.md`:

```
You match skill names taken from job descriptions to entries in the ESCO skills taxonomy.

For each numbered skill below, decide which of its candidate ESCO skills (if any) means the SAME skill. Be strict:
- "match": one candidate is the same skill, or a standard alternative name for it. Give its candidate number and a confidence: "high" only if you are sure they are the same skill; otherwise "low". A related-but-broader or narrower skill is NOT a match (e.g. "GPU" is not "GPU programming").
- "no_equivalent": none of the candidates is the same skill. Very common for modern tools, frameworks and techniques that ESCO predates. If the skill is real and specific, give a short canonical "custom_label" for a custom skill.
- "unsure": you cannot tell what the skill is or cannot decide.

Skills:
{strings}

Respond with ONLY a JSON object, no other text:
{{"results": [{{"n": <skill number>, "verdict": "match" | "no_equivalent" | "unsure", "candidate": <candidate number or null>, "confidence": "high" | "low" | null, "custom_label": <string or null>, "note": "<one short reason>"}}]}}
Include exactly one entry for every numbered skill.
```

Append to `config/llm_tasks.yml` (under `tasks:`, same indentation as the others):

```yaml
  skill_mapping:
    provider: anthropic
    model: claude-haiku-4-5-20251001
    prompt_family: claude
```

- [ ] **Step 2: Write the failing tests**

Create `packages/core/tests/integration/test_llm_map.py`:

```python
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
from core.skills.llm_map import count_eligible, propose_matches
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
            text=reply, provider="anthropic", model=model, input_tokens=10,
            output_tokens=5,
        )


def _reply(*entries: dict) -> str:
    return json.dumps({"results": list(entries)})


def _match(n: int, candidate: int = 1, confidence: str = "high") -> dict:
    return {"n": n, "verdict": "match", "candidate": candidate,
            "confidence": confidence, "custom_label": None, "note": "same"}


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
        self.assertEqual((summary.checked, summary.applied, summary.left_open), (2, 1, 1))

    def test_low_confidence_match_stays_open_with_the_verdict_recorded(self) -> None:
        self._run(_FakeAdapter([_reply(_match(1, confidence="low"), _match(2))]))
        second = self._row("zzfixture llm a")
        self.assertEqual(
            (second.skill_id, second.method, second.review_status, second.llm_verdict),
            (None, "none", "open", "match_low"),
        )
        self.assertIsNotNone(second.llm_checked_at)

    def test_no_equivalent_records_the_custom_label_and_note(self) -> None:
        entry = {"n": 1, "verdict": "no_equivalent", "candidate": None,
                 "confidence": None, "custom_label": "GRPO", "note": "RL method"}
        self._run(_FakeAdapter([_reply(entry, {"n": 2, "verdict": "unsure"})]))
        row = self._row("zzfixture llm a")
        self.assertEqual(
            (row.method, row.review_status, row.llm_verdict, row.llm_custom_label,
             row.llm_note),
            ("none", "open", "no_equivalent", "GRPO", "RL method"),
        )
        self.assertEqual(self._row("zzfixture llm b").llm_verdict, "unsure")

    def test_a_candidate_number_outside_the_offered_range_is_unsure(self) -> None:
        self._run(_FakeAdapter([_reply(_match(1, candidate=9), _match(2))]))
        row = self._row("zzfixture llm a")
        self.assertEqual((row.method, row.llm_verdict), ("none", "unsure"))

    def test_a_string_missing_from_the_reply_stays_unchecked_and_is_retried(self) -> None:
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
            for name, status in (("res", "resolved"), ("rej", "rejected"),
                                 ("dis", "dismissed")):
                insert_mapping(
                    conn, f"zzfixture llm {name}", review_status=status,
                    skill_id="fixture-python" if status == "resolved" else None,
                    method="alias" if status == "resolved" else "none",
                )
        propose_matches(
            self.engine,
            adapters={"anthropic": _FakeAdapter([_reply(_match(1), _match(2))])},
            embed=_embed_on_axis_0,
            embedding_model=_MODEL,
            raw_norms=self.norms + ["zzfixture llm res", "zzfixture llm rej",
                                    "zzfixture llm dis"],
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_llm_map -v`
Expected: ERROR — `No module named 'core.skills.llm_map'`.

- [ ] **Step 4: Implement `llm_map.py`**

Create `packages/core/core/skills/llm_map.py`:

```python
"""LLM pre-review of unmapped skill strings.

The mapper (`core.skills.mapper`) leaves a string `open` when no alias, ESCO
label or embedding at or above 0.85 matches. This module asks Claude, for each
such string, to choose among its 5 nearest ESCO skills, say none fits, or say
it is unsure:

- a high-confidence match is applied as `method = 'llm'` with `review_status`
  NULL, so it appears in the "Auto-matches — verify" list and only becomes a
  permanent alias when a person confirms it;
- anything else leaves the row `open`, with the verdict recorded for display.

Every string the model answered gets `llm_checked_at`, so it is never sent
twice. A string the model did not answer (bad JSON, missing entry, API error)
stays unchecked and is retried by the next run. Only `open` rows are ever
written: the guard is in the UPDATE itself, so a person's decision made while
a batch is in flight wins.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, text

from core.llm import gateway
from core.llm.json_response import parse_json_response
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.skills.vector import to_pgvector

TASK = "skill_mapping"
PROMPT_VERSION = "claude.v1"
BATCH_SIZE = 20
CANDIDATES_PER_STRING = 5
MAX_REPLY_TOKENS = 4096
_NOTE_MAX_CHARS = 200

_ELIGIBLE = (
    "FROM silver.skill_mapping AS m WHERE m.review_status = 'open' "
    "AND m.llm_checked_at IS NULL "
    "AND (CAST(:raw_norms AS text[]) IS NULL OR m.raw_norm = ANY(:raw_norms))"
)
_SELECT_ELIGIBLE = text(
    "SELECT m.raw_norm, m.raw_example, "
    "(SELECT count(DISTINCT r.job_group_id) FROM silver.job_skill_raw AS r "
    "WHERE r.raw_norm = m.raw_norm) AS jd_job_count "
    f"{_ELIGIBLE} ORDER BY jd_job_count DESC, m.raw_norm LIMIT :limit"
)
_COUNT_ELIGIBLE = text(f"SELECT count(*) {_ELIGIBLE}")
_NEAREST = text(
    "SELECT e.skill_id, s.preferred_label AS label, "
    "1 - (e.embedding <=> CAST(:q AS vector)) AS score "
    "FROM esco.skill_embedding AS e JOIN esco.skill AS s USING (skill_id) "
    "WHERE e.embedding_model = :model "
    "ORDER BY e.embedding <=> CAST(:q AS vector) LIMIT :k"
)
_APPLY_MATCH = text(
    "UPDATE silver.skill_mapping SET skill_id = :skill_id, method = 'llm', "
    "score = :score, candidate_skill_id = NULL, candidate_score = NULL, "
    "review_status = NULL, llm_verdict = :verdict, llm_note = :note, "
    "llm_checked_at = now(), mapped_at = now() "
    "WHERE raw_norm = :raw_norm AND review_status = 'open' "
    "AND llm_checked_at IS NULL"
)
_RECORD_VERDICT = text(
    "UPDATE silver.skill_mapping SET llm_verdict = :verdict, "
    "llm_custom_label = :custom_label, llm_note = :note, llm_checked_at = now() "
    "WHERE raw_norm = :raw_norm AND review_status = 'open' "
    "AND llm_checked_at IS NULL"
)


@dataclass(frozen=True)
class Candidate:
    """One ESCO skill offered to the model for a string.

    Attributes:
        skill_id: The ESCO skill id.
        label: Its preferred label.
        score: Cosine similarity to the string.
    """

    skill_id: str
    label: str
    score: float


@dataclass(frozen=True)
class Verdict:
    """The model's answer for one string.

    Attributes:
        kind: "match", "no_equivalent" or "unsure".
        skill_id: The chosen candidate's id (kind "match" only).
        score: The chosen candidate's cosine (kind "match" only).
        confidence: "high" or "low" (kind "match" only).
        custom_label: Suggested custom-skill name (kind "no_equivalent").
        note: The model's one-line reason.
    """

    kind: str
    skill_id: str | None = None
    score: float | None = None
    confidence: str | None = None
    custom_label: str | None = None
    note: str | None = None

    @property
    def stored(self) -> str:
        """The value kept in `silver.skill_mapping.llm_verdict`.

        Returns:
            "match_high", "match_low", "no_equivalent" or "unsure".
        """
        if self.kind == "match":
            return f"match_{self.confidence}"
        return self.kind

    @property
    def applies(self) -> bool:
        """Whether this verdict maps the string (only a high-confidence match).

        Returns:
            True for a high-confidence match.
        """
        return self.kind == "match" and self.confidence == "high"


@dataclass(frozen=True)
class LlmMapSummary:
    """Counts from one `propose_matches` run.

    Attributes:
        checked: Strings the model answered and that were recorded.
        applied: Of those, high-confidence matches applied as `llm` mappings.
        left_open: Of those, strings left open for a person.
        failed: Strings sent but not answered / not recorded (retried later).
        input_tokens: Prompt tokens billed across all calls.
        output_tokens: Completion tokens billed across all calls.
    """

    checked: int
    applied: int
    left_open: int
    failed: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class _Item:
    raw_norm: str
    raw_example: str
    candidates: list[Candidate]


def count_eligible(engine: Engine, *, raw_norms: list[str] | None = None) -> int:
    """Count strings a run would send to the model.

    Args:
        engine: A DB engine.
        raw_norms: Restrict to these strings; `None` covers everything.

    Returns:
        The number of `open`, not-yet-checked strings.
    """
    with engine.connect() as conn:
        return conn.execute(_COUNT_ELIGIBLE, {"raw_norms": raw_norms}).scalar_one()


def build_prompt(items: list[_Item], template: str) -> str:
    """Render the numbered skill list into the prompt template.

    Args:
        items: The batch, in the order that defines the numbering (1-based).
        template: The prompt file's text, with a `{strings}` placeholder.

    Returns:
        The full prompt.
    """
    blocks = []
    for number, item in enumerate(items, start=1):
        lines = [f"{number}. {item.raw_example}"]
        lines += [
            f"   {index}) {cand.label}"
            for index, cand in enumerate(item.candidates, start=1)
        ]
        blocks.append("\n".join(lines))
    return template.format(strings="\n\n".join(blocks))


def _clean(value: object) -> str | None:
    """Normalise a free-text field from the model.

    Args:
        value: The raw JSON value.

    Returns:
        A stripped, length-capped string, or None if empty / not a string.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:_NOTE_MAX_CHARS]


def _to_verdict(entry: dict[str, object], candidates: list[Candidate]) -> Verdict:
    """Turn one reply entry into a `Verdict`, treating anything odd as unsure.

    Args:
        entry: One element of the reply's `results` list.
        candidates: The candidates offered for that string.

    Returns:
        The verdict; a pick outside the offered candidates becomes "unsure".
    """
    note = _clean(entry.get("note"))
    kind = entry.get("verdict")
    if kind == "match":
        pick = entry.get("candidate")
        if isinstance(pick, int) and not isinstance(pick, bool):
            if 1 <= pick <= len(candidates):
                chosen = candidates[pick - 1]
                confidence = "high" if entry.get("confidence") == "high" else "low"
                return Verdict(
                    "match", chosen.skill_id, chosen.score, confidence, None, note
                )
        return Verdict("unsure", note=note or "picked a candidate that was not offered")
    if kind == "no_equivalent":
        return Verdict("no_equivalent", custom_label=_clean(entry.get("custom_label")),
                       note=note)
    return Verdict("unsure", note=note)


def parse_verdicts(text_: str, items: list[_Item]) -> dict[int, Verdict]:
    """Parse the model's reply into verdicts keyed by 0-based batch index.

    Args:
        text_: The reply text.
        items: The batch that was sent.

    Returns:
        Verdicts for the strings the reply answered validly. An out-of-range or
        repeated `n` is ignored, so a missing string is simply absent.

    Raises:
        ValueError: If the reply is not JSON or has no `results` list
            (`json.JSONDecodeError` is a `ValueError`).
    """
    data = parse_json_response(text_.strip())
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("reply has no 'results' list")
    verdicts: dict[int, Verdict] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        number = entry.get("n")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        index = number - 1
        if not 0 <= index < len(items) or index in verdicts:
            continue
        verdicts[index] = _to_verdict(entry, items[index].candidates)
    return verdicts


def _candidates(
    engine: Engine, raw_norm: str, embed: Callable[[str], list[float]], model: str
) -> list[Candidate]:
    """Fetch a string's nearest ESCO skills.

    Args:
        engine: A DB engine.
        raw_norm: The normalised string (what the mapper embeds).
        embed: Maps a string to its embedding.
        model: The embedding model in use.

    Returns:
        Up to `CANDIDATES_PER_STRING` candidates, nearest first.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            _NEAREST,
            {"q": to_pgvector(embed(raw_norm)), "model": model,
             "k": CANDIDATES_PER_STRING},
        ).all()
    return [Candidate(r.skill_id, r.label, float(r.score)) for r in rows]


def propose_matches(
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    embed: Callable[[str], list[float]],
    embedding_model: str,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
    raw_norms: list[str] | None = None,
    config_path: Path | None = None,
) -> LlmMapSummary:
    """Ask the model about open, unchecked strings and record its verdicts.

    Args:
        engine: A DB engine (owner or app role; both may UPDATE skill_mapping).
        adapters: LLM adapters keyed by provider; must include the task's
            provider ("anthropic").
        embed: Maps a string to its embedding (to find its ESCO candidates).
        embedding_model: The embedding model the ESCO vectors were made with.
        limit: Send at most this many strings; `None` sends every eligible one.
        batch_size: Strings per model call.
        raw_norms: Restrict to these strings; `None` covers everything.
        config_path: Task-config override (tests).

    Returns:
        Counts and token usage. A failed batch is counted in `failed` and does
        not stop the run.
    """
    template = load_prompt(TASK, load_task_config(TASK, config_path).prompt_family, 1)
    with engine.connect() as conn:
        rows = conn.execute(
            _SELECT_ELIGIBLE,
            {"raw_norms": raw_norms, "limit": limit if limit is not None else 10**9},
        ).all()
    checked = applied = failed = in_tokens = out_tokens = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        items = [
            _Item(r.raw_norm, r.raw_example,
                  _candidates(engine, r.raw_norm, embed, embedding_model))
            for r in chunk
        ]
        try:
            response = gateway.complete(
                TASK,
                build_prompt(items, template),
                prompt_version=PROMPT_VERSION,
                adapters=adapters,
                config_path=config_path,
                max_tokens=MAX_REPLY_TOKENS,
            )
            verdicts = parse_verdicts(response.text, items)
        except Exception:  # noqa: BLE001 — any API/parse failure = retry later
            failed += len(items)
            continue
        in_tokens += response.input_tokens
        out_tokens += response.output_tokens
        failed += len(items) - len(verdicts)
        with engine.begin() as conn:
            for index, verdict in verdicts.items():
                item = items[index]
                if verdict.applies:
                    result = conn.execute(
                        _APPLY_MATCH,
                        {"skill_id": verdict.skill_id, "score": verdict.score,
                         "verdict": verdict.stored, "note": verdict.note,
                         "raw_norm": item.raw_norm},
                    )
                    applied += result.rowcount
                else:
                    result = conn.execute(
                        _RECORD_VERDICT,
                        {"verdict": verdict.stored,
                         "custom_label": verdict.custom_label,
                         "note": verdict.note, "raw_norm": item.raw_norm},
                    )
                checked += result.rowcount
    return LlmMapSummary(
        checked=checked,
        applied=applied,
        left_open=checked - applied,
        failed=failed,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
    )
```

Note: `load_prompt(TASK, family, 1)` reads `prompts/skill_mapping/claude.v1.md`; `PROMPT_VERSION` is the string stamped on the call log.

- [ ] **Step 5: Run to verify it passes**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_llm_map tests.integration.test_llm_review -v`
Expected: all PASS. (If `test_a_string_missing...` fails on `failed`, check `failed += len(items) - len(verdicts)` runs before the write.)

- [ ] **Step 6: Lint and commit**

```bash
cd job_search && venv/bin/ruff check . && venv/bin/isort . && venv/bin/black .
git add prompts/skill_mapping config/llm_tasks.yml packages/core/core/skills/llm_map.py packages/core/tests/integration/test_llm_map.py
git commit -m "feat(job_search): LLM proposer for unmapped skill strings

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Accuracy gate (`--evaluate`) and the `llm-map-skills` CLI

**Files:**
- Modify: `packages/core/core/skills/llm_map.py` (add `EvalReport`, `evaluate_against_resolved`)
- Modify: `apps/pipeline/app/cli.py` (`_cmd_llm_map_skills`, parser, dispatch)
- Test: `packages/core/tests/integration/test_llm_map.py` (add `TestEvaluate`)

**Interfaces:**
- Consumes: Task 2's `_Item`, `_candidates`, `build_prompt`, `parse_verdicts`, `count_eligible`, `propose_matches`, `LlmMapSummary`.
- Produces: `@dataclass(frozen=True) EvalReport(sampled: int, answered: int, high_matches: int, high_agree: int, low_matches: int, no_equivalent: int, unsure: int, truth_in_candidates: int, input_tokens: int, output_tokens: int)` with property `agreement: float | None` (`high_agree / high_matches`, None if no high matches); `evaluate_against_resolved(engine, *, adapters, embed, embedding_model, sample=200, batch_size=BATCH_SIZE, config_path=None) -> EvalReport` — writes nothing. Constant `MIN_HIGH_AGREEMENT = 0.90`.

- [ ] **Step 1: Write the failing tests**

Add to `test_llm_map.py` (imports: `evaluate_against_resolved` from `core.skills.llm_map`):

```python
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
            insert_mapping(conn, "zzfixture eval a", skill_id="fixture-cloud",
                           method="alias", review_status="resolved")
            insert_mapping(conn, "zzfixture eval b", skill_id="fixture-python",
                           method="alias", review_status="resolved")

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def test_reports_agreement_on_high_confidence_picks_and_writes_nothing(self) -> None:
        # Candidate 1 for both strings is fixture-cloud: right for "a", wrong for "b".
        adapter = _FakeAdapter([_reply(_match(1), _match(2))])
        with self.engine.connect() as conn:
            before = conn.execute(
                text("SELECT count(*) FROM silver.skill_mapping "
                     "WHERE llm_checked_at IS NOT NULL")
            ).scalar_one()
        report = evaluate_against_resolved(
            self.engine, adapters={"anthropic": adapter}, embed=_embed_on_axis_0,
            embedding_model=_MODEL, sample=2, raw_norms=["zzfixture eval a",
                                                         "zzfixture eval b"],
        )
        self.assertEqual((report.high_matches, report.high_agree), (2, 1))
        self.assertEqual(report.agreement, 0.5)
        with self.engine.connect() as conn:
            after = conn.execute(
                text("SELECT count(*) FROM silver.skill_mapping "
                     "WHERE llm_checked_at IS NOT NULL")
            ).scalar_one()
        self.assertEqual(before, after)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_llm_map.TestEvaluate -v`
Expected: ImportError for `evaluate_against_resolved`.

- [ ] **Step 3: Implement the evaluator**

Append to `llm_map.py` (add `MIN_HIGH_AGREEMENT = 0.90` next to the other constants, and `_SELECT_RESOLVED`):

```python
MIN_HIGH_AGREEMENT = 0.90
"""Minimum agreement on high-confidence picks before running on the backlog."""

_SELECT_RESOLVED = text(
    "SELECT raw_norm, raw_example, skill_id FROM silver.skill_mapping "
    "WHERE review_status = 'resolved' AND skill_id IS NOT NULL "
    "AND (CAST(:raw_norms AS text[]) IS NULL OR raw_norm = ANY(:raw_norms)) "
    "ORDER BY md5(raw_norm) LIMIT :sample"
)


@dataclass(frozen=True)
class EvalReport:
    """How the model's verdicts compare with strings a person already resolved.

    Attributes:
        sampled: Resolved strings sent.
        answered: Of those, strings the model answered.
        high_matches: High-confidence matches it proposed.
        high_agree: High-confidence matches equal to the person's choice.
        low_matches: Low-confidence matches proposed.
        no_equivalent: Strings it said have no ESCO equivalent.
        unsure: Strings it was unsure about.
        truth_in_candidates: Strings whose human-chosen skill was among the
            5 offered candidates (an upper bound on what any match can reach).
        input_tokens: Prompt tokens billed.
        output_tokens: Completion tokens billed.
    """

    sampled: int
    answered: int
    high_matches: int
    high_agree: int
    low_matches: int
    no_equivalent: int
    unsure: int
    truth_in_candidates: int
    input_tokens: int
    output_tokens: int

    @property
    def agreement(self) -> float | None:
        """Share of high-confidence matches that equal the person's choice.

        Returns:
            `high_agree / high_matches`, or None when there were no high matches.
        """
        return self.high_agree / self.high_matches if self.high_matches else None


def evaluate_against_resolved(
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    embed: Callable[[str], list[float]],
    embedding_model: str,
    sample: int = 200,
    batch_size: int = BATCH_SIZE,
    raw_norms: list[str] | None = None,
    config_path: Path | None = None,
) -> EvalReport:
    """Measure the model against strings a person already resolved. Writes nothing.

    Args:
        engine: A DB engine (read-only use).
        adapters: LLM adapters keyed by provider.
        embed: Maps a string to its embedding.
        embedding_model: The embedding model in use.
        sample: How many resolved strings to send (a stable pseudo-random pick).
        batch_size: Strings per model call.
        raw_norms: Restrict the pool to these strings (tests).
        config_path: Task-config override (tests).

    Returns:
        The comparison report. A failed batch is skipped (`answered` is lower).
    """
    template = load_prompt(TASK, load_task_config(TASK, config_path).prompt_family, 1)
    with engine.connect() as conn:
        rows = conn.execute(
            _SELECT_RESOLVED, {"raw_norms": raw_norms, "sample": sample}
        ).all()
    counts = dict.fromkeys(
        ("answered", "high", "agree", "low", "none", "unsure", "in_cands", "in", "out"),
        0,
    )
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        items = [
            _Item(r.raw_norm, r.raw_example,
                  _candidates(engine, r.raw_norm, embed, embedding_model))
            for r in chunk
        ]
        try:
            response = gateway.complete(
                TASK, build_prompt(items, template), prompt_version=PROMPT_VERSION,
                adapters=adapters, config_path=config_path,
                max_tokens=MAX_REPLY_TOKENS,
            )
            verdicts = parse_verdicts(response.text, items)
        except Exception:  # noqa: BLE001 — a failed batch is just skipped
            continue
        counts["in"] += response.input_tokens
        counts["out"] += response.output_tokens
        for index, verdict in verdicts.items():
            truth = chunk[index].skill_id
            counts["answered"] += 1
            counts["in_cands"] += any(c.skill_id == truth for c in items[index].candidates)
            if verdict.kind == "match" and verdict.confidence == "high":
                counts["high"] += 1
                counts["agree"] += verdict.skill_id == truth
            elif verdict.kind == "match":
                counts["low"] += 1
            elif verdict.kind == "no_equivalent":
                counts["none"] += 1
            else:
                counts["unsure"] += 1
    return EvalReport(
        sampled=len(rows), answered=counts["answered"], high_matches=counts["high"],
        high_agree=counts["agree"], low_matches=counts["low"],
        no_equivalent=counts["none"], unsure=counts["unsure"],
        truth_in_candidates=counts["in_cands"], input_tokens=counts["in"],
        output_tokens=counts["out"],
    )
```

Update `TestEvaluate`'s call in Step 1 already passes `raw_norms=`, matching this signature.

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_llm_map -v`
Expected: all PASS.

- [ ] **Step 5: Add the CLI command**

In `apps/pipeline/app/cli.py`, add imports next to the other skills imports: `from core.skills.llm_map import (
    MIN_HIGH_AGREEMENT,
    EvalReport,
    count_eligible,
    evaluate_against_resolved,
    propose_matches,
)`. Add after `_cmd_map_skills`:

```python
# Haiku 4.5 list price, USD per million tokens — an estimate for the printout
# only; check the current price list before relying on it.
_HAIKU_INPUT_USD_PER_MTOK = 1.0
_HAIKU_OUTPUT_USD_PER_MTOK = 5.0
_EST_INPUT_TOKENS_PER_STRING = 250
_EST_OUTPUT_TOKENS_PER_STRING = 60


def _usd(input_tokens: int, output_tokens: int) -> float:
    """Estimate the cost of a token count at Haiku 4.5 list prices.

    Args:
        input_tokens: Prompt tokens.
        output_tokens: Completion tokens.

    Returns:
        Estimated USD.
    """
    return (
        input_tokens * _HAIKU_INPUT_USD_PER_MTOK
        + output_tokens * _HAIKU_OUTPUT_USD_PER_MTOK
    ) / 1_000_000


def _cmd_llm_map_skills(args: argparse.Namespace) -> int:
    """Run the `llm-map-skills` subcommand.

    Args:
        args: Parsed CLI arguments — `limit`, `dry_run`, `evaluate`, `sample`.

    Returns:
        0 on success; 1 if there is no Anthropic key, the embedding server is
        unavailable, or `--evaluate` finds agreement below the safe threshold.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("llm-map-skills: ANTHROPIC_API_KEY is not set")
        return 1
    engine = build_engine(settings.database_url)
    if args.dry_run:
        eligible = count_eligible(engine)
        sent = min(eligible, args.limit) if args.limit else eligible
        cost = _usd(
            sent * _EST_INPUT_TOKENS_PER_STRING, sent * _EST_OUTPUT_TOKENS_PER_STRING
        )
        print(f"llm-map-skills: {eligible} eligible; would send {sent} (~${cost:.2f})")
        return 0
    http_client = httpx.Client(timeout=120.0)
    try:
        adapters = _build_llm_adapters(http_client)
        embed = _build_embedder(http_client, settings)
        if args.evaluate:
            return _print_evaluation(
                evaluate_against_resolved(
                    engine,
                    adapters=adapters,
                    embed=embed,
                    embedding_model=settings.embedding_model,
                    sample=args.sample,
                )
            )
        summary = propose_matches(
            engine,
            adapters=adapters,
            embed=embed,
            embedding_model=settings.embedding_model,
            limit=args.limit,
        )
    except httpx.HTTPError as exc:
        print(f"llm-map-skills: embedding server unavailable ({exc})")
        return 1
    finally:
        http_client.close()
    print(
        f"llm-map-skills complete: checked={summary.checked} "
        f"applied={summary.applied} left_open={summary.left_open} "
        f"failed={summary.failed} "
        f"cost~${_usd(summary.input_tokens, summary.output_tokens):.2f}"
    )
    return 0


def _print_evaluation(report: EvalReport) -> int:
    """Print an `--evaluate` report and decide the exit status.

    Args:
        report: What `evaluate_against_resolved` measured.

    Returns:
        0 if high-confidence agreement meets `MIN_HIGH_AGREEMENT`, else 1.
    """
    print(
        f"llm-map-skills --evaluate: sampled={report.sampled} "
        f"answered={report.answered} high={report.high_matches} "
        f"high_agree={report.high_agree} low={report.low_matches} "
        f"no_equivalent={report.no_equivalent} unsure={report.unsure} "
        f"truth_in_candidates={report.truth_in_candidates} "
        f"cost~${_usd(report.input_tokens, report.output_tokens):.2f}"
    )
    if report.agreement is None:
        print("llm-map-skills: no high-confidence matches to judge")
        return 1
    print(
        f"llm-map-skills: high-confidence agreement {report.agreement:.0%} "
        f"(need >= {MIN_HIGH_AGREEMENT:.0%})"
    )
    return 0 if report.agreement >= MIN_HIGH_AGREEMENT else 1
```

Add the parser after the `map-skills` parser block:

```python
    llm_parser = subparsers.add_parser(
        "llm-map-skills",
        help="Have Claude pre-review open unmapped skill strings",
    )
    llm_parser.add_argument(
        "--limit", type=int, default=None, help="Send at most this many strings"
    )
    llm_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print how many strings would be sent and the estimated cost; no API calls",
    )
    llm_parser.add_argument(
        "--evaluate",
        action="store_true",
        help=(
            "Judge the model against strings a person already resolved; writes "
            "nothing. Exits 1 if high-confidence agreement is below 90%%"
        ),
    )
    llm_parser.add_argument(
        "--sample", type=int, default=200, help="Resolved strings to use with --evaluate"
    )
```

Dispatch: add next to the `map-skills` branch:

```python
    if args.command == "llm-map-skills":
        return _cmd_llm_map_skills(args)
```

- [ ] **Step 6: Smoke-test the CLI without spending anything**

Run: `cd job_search && docker compose --profile cli run --rm pipeline llm-map-skills --dry-run`
Expected: `llm-map-skills: N eligible; would send N (~$X.XX)`. (Rebuild the pipeline image only if the code isn't bind-mounted — `apps/pipeline/app` is.) With the key unset: prints `ANTHROPIC_API_KEY is not set`, exit 1.

- [ ] **Step 7: Lint and commit**

```bash
venv/bin/ruff check . && venv/bin/isort . && venv/bin/black .
git add packages/core/core/skills/llm_map.py apps/pipeline/app/cli.py packages/core/tests/integration/test_llm_map.py
git commit -m "feat(job_search): llm-map-skills CLI with --evaluate accuracy gate

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Post-run hook

**Files:**
- Modify: `packages/core/core/skills/post_run_mapping.py`
- Modify: `apps/api/app/routers/extraction_runs.py` (pass `llm_adapters`)
- Modify: `packages/core/tests/integration/test_extraction_runs_router.py` (fake factory signature)
- Test: `packages/core/tests/integration/test_post_run_mapping.py`

**Interfaces:**
- Consumes: `propose_matches`, `LlmMapSummary`.
- Produces: `build_post_run_mapping(engine, *, ollama_base_url, embedding_model, http_client, raw_norms=None, llm_adapters: dict[str, LLMAdapter] | None = None, llm_limit: int = 300)`. Returned `run()` appends one sentence: ` Claude pre-review: N checked, A applied, K left for review.` / ` Claude pre-review skipped: no Anthropic key.` / ` Claude pre-review failed: <exc>.`

- [ ] **Step 1: Write the failing tests**

Add to `test_post_run_mapping.py` (reuse `_fake_embeddings`; add imports `json`, `LLMResponse`):

```python
class _FakeAnthropic:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResponse(text=self.reply, provider="anthropic", model=model,
                           input_tokens=1, output_tokens=1)
```

and tests inside `TestPostRunMapping`:

```python
    def _seed(self) -> None:
        with self.owner.begin() as conn:
            insert_job_skills(conn, self.job, "local.v1",
                              [(self.raw, self.raw, "must_have")])

    def _hook_with(self, llm_adapters):
        return build_post_run_mapping(
            self.app, ollama_base_url="http://fake-ollama:11434",
            embedding_model=get_settings().embedding_model,
            http_client=httpx.Client(transport=httpx.MockTransport(_fake_embeddings)),
            raw_norms=[self.raw], llm_adapters=llm_adapters,
        )

    def test_summary_notes_the_skipped_pre_review_when_there_is_no_anthropic_adapter(self):
        self._seed()
        summary = self._hook_with({})()
        self.assertTrue(summary.endswith("Claude pre-review skipped: no Anthropic key."))

    def test_a_pre_review_failure_is_reported_but_does_not_raise(self):
        self._seed()
        summary = self._hook_with({"anthropic": _FakeAnthropic(RuntimeError("down"))})()
        self.assertIn("Mapped 0 new skill string(s)", summary)
        self.assertIn("Claude pre-review: 0 checked", summary)
        self.assertIn("1 not answered", summary)  # batch failures are counted, not raised
```

(Adapter errors are swallowed inside `propose_matches` as `failed`, so the "failed:" sentence only appears if `propose_matches` itself raises, e.g. the task config/prompt is missing; the hook wraps that in `try/except Exception  # noqa: BLE001`.)

Also change the existing assertions in the two current tests from equality to `assertTrue(summary.startswith(...))` only if they now see a pre-review sentence — they pass `llm_adapters=None` via `_hook()`, which must keep returning the **unchanged** one-line summary (no sentence appended when `llm_adapters is None`). Keep `None` = "feature off"; `{}` = "on but no key".

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_post_run_mapping -v`
Expected: TypeError (`unexpected keyword argument 'llm_adapters'`).

- [ ] **Step 3: Implement**

In `post_run_mapping.py` add imports (`from core.llm.types import LLMAdapter`, `from core.skills.llm_map import propose_matches`), add the two parameters (documenting them in `Args`), and change `run()`:

```python
    def run() -> str:
        sync_seed_aliases(engine)
        summary = map_pending(
            engine,
            embed=embed,
            embedding_model=embedding_model,
            raw_norms=raw_norms,
        )
        message = (
            f"Mapped {summary.mapped} new skill string(s) to ESCO; "
            f"{summary.unmapped} need review."
        )
        if llm_adapters is None:
            return message
        if "anthropic" not in llm_adapters:
            return f"{message} Claude pre-review skipped: no Anthropic key."
        try:
            reviewed = propose_matches(
                engine,
                adapters=llm_adapters,
                embed=embed,
                embedding_model=embedding_model,
                limit=llm_limit,
                raw_norms=raw_norms,
            )
        except Exception as exc:  # noqa: BLE001 — never fail the run over this
            return f"{message} Claude pre-review failed: {exc}."
        return (
            f"{message} Claude pre-review: {reviewed.checked} checked, "
            f"{reviewed.applied} applied, {reviewed.left_open} left for review"
            + (f", {reviewed.failed} not answered (retried next run)." if reviewed.failed else ".")
        )
```

In `extraction_runs.py`, in the `mapping_hook_factory(...)` call add `llm_adapters=adapters,` (the dict already built in that function, after the Ollama swap). In `test_extraction_runs_router.py` change the fake to `def fake_factory(engine, *, ollama_base_url, embedding_model, http_client, llm_adapters=None):`.

- [ ] **Step 4: Run to verify it passes, plus the router suite**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_post_run_mapping tests.integration.test_extraction_runs_router -v`
Expected: PASS (router suite ≈ 50 s).

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check . && venv/bin/isort . && venv/bin/black .
git add packages/core/core/skills/post_run_mapping.py apps/api/app/routers/extraction_runs.py packages/core/tests/integration/test_post_run_mapping.py packages/core/tests/integration/test_extraction_runs_router.py
git commit -m "feat(job_search): run the Claude pre-review after a completed extraction run

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Review UI, docs, and the real run

**Files:**
- Modify: `apps/ui/app/pages/6_Skill_Review.py`
- Modify: `README.md`, `docs/esco.md`

**Interfaces:**
- Consumes: API fields `llm_verdict`, `llm_custom_label`, `llm_note` (Unmapped) and `llm_note`, `method == "llm"` (verify).

- [ ] **Step 1: Unmapped cards show the model's note and a one-click custom skill**

In the Unmapped loop, right after the `st.caption(f"In {item['jd_job_count']} job(s)" ...)` block, add:

```python
            if item.get("llm_verdict") in ("no_equivalent", "unsure", "match_low"):
                verdict_text = {
                    "no_equivalent": "no ESCO equivalent",
                    "unsure": "unsure",
                    "match_low": "weak match only",
                }[item["llm_verdict"]]
                st.caption(
                    f"Claude: {verdict_text}"
                    + (f" — {_plain(item['llm_note'])}" if item.get("llm_note") else "")
                )
                if item.get("llm_custom_label") and st.button(
                    f"Create custom skill “{_plain(item['llm_custom_label'])}”",
                    key=f"llmcustom-{key}",
                ):
                    if _post(
                        "/skills/review/resolve",
                        {"raw_norm": key, "custom_label": item["llm_custom_label"]},
                    ):
                        st.rerun()
```

- [ ] **Step 2: Verify cards say "matched by Claude"**

In the verify loop change the `how = (...)` expression to:

```python
            if match["method"] == "label":
                how = "exact ESCO label match"
            elif match["method"] == "llm":
                how = "matched by Claude" + (
                    f" ({_plain(match['llm_note'])})" if match.get("llm_note") else ""
                )
            else:
                how = f"similarity {match['score']:.2f}"
```

Update the verify tab caption to add "…, Claude matches with similarity matches" only if it stays accurate (the sort treats `llm` like `embedding`); otherwise leave it.

- [ ] **Step 3: Docs**

`README.md`: add a short subsection under the skill-extraction runner section, "Claude pre-review of unmapped skills": what it does, the three commands (`docker compose --profile cli run --rm pipeline llm-map-skills --dry-run`, `... --evaluate`, `... llm-map-skills [--limit N]`), that a high-confidence match appears in *Auto-matches — verify* and only becomes an alias when confirmed, that runs also do it (≤300 strings) after each completed extraction run, and that it needs `ANTHROPIC_API_KEY` and sends only skill strings. `docs/esco.md`: one paragraph on the `llm` method and `remap-all-auto` keeping `llm` rows.

- [ ] **Step 4: Lint, restart the UI, look at the pages**

```bash
venv/bin/ruff check . && venv/bin/black --check . && docker compose restart ui
```
Open http://localhost:8501 → Skill Review. Expected: page loads; nothing breaks with no LLM data yet (all `llm_*` fields null).

- [ ] **Step 5: Commit**

```bash
git add apps/ui/app/pages/6_Skill_Review.py README.md docs/esco.md
git commit -m "feat(job_search): show Claude's verdicts on the Skill Review page

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Real run — evaluate first, then the backlog (needs the user's go-ahead for spend)**

1. Confirm `ANTHROPIC_API_KEY` is set in `.env` (do not print it).
2. `docker compose --profile cli run --rm pipeline llm-map-skills --dry-run` → report eligible count and estimated cost.
3. `docker compose --profile cli run --rm pipeline llm-map-skills --evaluate --sample 200` (Ollama must be up for embeddings). Report the printed counts and agreement. **If agreement < 90% the command exits 1: stop, show the disagreements, and revisit `prompts/skill_mapping/claude.v1.md` — do not run on the backlog.**
4. Only if it passes: `... llm-map-skills --limit 100` first, show the user the verify tab, then the rest with no limit.
5. Report: checked / applied / left_open / failed, cost, and how far the Unmapped list shrank.

- [ ] **Step 7: Open the PR** (`gh pr create`, body includes the evaluate numbers) and stop; the user merges.
