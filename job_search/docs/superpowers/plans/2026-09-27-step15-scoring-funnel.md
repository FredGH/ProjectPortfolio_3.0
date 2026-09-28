# Step 15 — Scoring Funnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank the shared job pool per user by fit, not topic similarity: hard filters, vector similarity + cross-encoder rerank, skill coverage, LLM re-rank of the top 50 — every component persisted separately so Step 16 can calibrate blend weights against real labels.

**Architecture:** A new `scoring` Postgres schema (5 tables: `user_preference`, `job_chunk_embedding` [shared], `cv_chunk_embedding` [per-user], `job_score`, `weight`), one Python module per pipeline stage under `core/scoring/`, one new pipeline CLI subcommand per stage (so any stage can be re-run alone), a settings page for preferences, and a `fct_job_score` dbt gold mart.

**Tech Stack:** Python 3.11, SQLAlchemy Core, Alembic, Postgres/pgvector (HNSW), LlamaIndex (`llama-index-core`'s `SentenceSplitter`, library only), Ollama (`nomic-embed-text`, 768-dim), `sentence-transformers` (`CrossEncoder`, `BAAI/bge-reranker-base`), Anthropic (Claude Sonnet 5), dbt.

**Spec:** `docs/superpowers/specs/2026-09-27-step15-scoring-funnel-design.md`

## Global Constraints

- Python 3.11; black (88 cols); isort (profile black); ruff clean; Google-style docstrings with Args/Returns/Raises on every function and class; type hints on public signatures; no bare `except:`.
- Lint scoped to `packages apps db` from `job_search/` — never `isort .`/`black .` on the whole repo (it corrupts the git-ignored `venv-dbt`).
- Tests: `unittest`, real Postgres, no DB mocking. Only the Ollama embed call, the cross-encoder, and the Anthropic adapter are faked. Fixture strings/ids prefixed `zzfixture`/`fixture-`; fixture users created and torn down per-test (pattern: `packages/core/tests/integration/test_cv_store.py`'s `setUp`/`tearDown`).
- Per-user tables use RLS keyed on `app.current_user_id`, set only via `core.db.session.session_scope(engine, user_id=...)` — never a raw `SET` elsewhere. Job-pool tables (`job_chunk_embedding`) are shared, no RLS, owner-role writes via pipeline CLI, `job_search_app` gets SELECT only (mirrors `dedup.*`).
- `unknown`/no-preference is never coerced to a default (DECISIONS.md §2.13) — a `NULL`/empty preference field means "no filter on this dimension," not "exclude everything" or "exclude nothing" by assumption.
- Embedding model: `nomic-embed-text`, 768-dim, via the existing `core.embedding.ollama.embed_text`. A vector-similarity comparison between a CV chunk and a job chunk embedded under different `embedding_model` values must be skipped, never compared (mirrors `core.skills.mapper.EmbeddingModelMismatch`).
- LlamaIndex is used as a **library** call (`SentenceSplitter`) only — no `VectorStoreIndex`, no LlamaIndex-managed storage or retrieval.
- The cross-encoder dependency (`sentence-transformers` + the `bge-reranker-base` checkpoint) goes in the **pipeline** image only (`requirements.txt` used by `apps/pipeline/Dockerfile`), never api/ui.
- CLI commands that act on one user's data take an explicit `--user-id` argument (the working `map-cv-skills` pattern) — never `get_current_user_id` (that dependency 501s until Step 22a's auth ships; verified live on this stack).
- LLM re-rank never sees more than 50 jobs per run per user (the plan's own "Done when" criterion).

## Review Focus

- A user with no `scoring.user_preference` row at all (never visited the settings page): hard filters must not exclude every job by treating "no row" as "reject everything" — it should behave exactly like an all-NULL preference row.
- A job whose `description` has no detectable section heading at all (a common aggregator-sourced posting): chunking must produce one `other` section, never raise, never silently drop the text.
- Two jobs/CVs embedded under different `embedding_model` values (e.g. after a future model change) must never be compared as if compatible — this is the exact silent-corruption failure DECISIONS.md §2.8 and the recalibration warning both call out.
- A job that never reaches stage 4 (outside the top 50) must have `final_score` computed from whichever components it does have — not NULL, and not treated as if a missing component were a zero score.
- The `--limit`-style commands (`chunk-embed-jobs`, `score-similarity`, etc.) must be idempotent: running a stage twice on the same data must not create duplicate rows or double-count anything (tested per task, not assumed).

---

### Task 1: Migration — the `scoring` schema

**Files:**
- Create: `db/migrations/versions/0028_create_scoring_schema.py`
- Test: `packages/core/tests/integration/test_scoring_schema.py`

**Interfaces:**
- Produces: schema `scoring` with tables `user_preference`, `job_chunk_embedding`, `cv_chunk_embedding`, `job_score`, `weight`, exactly as columned below. Later tasks read/write these tables by name.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_scoring_schema.py`:

```python
"""Schema tests for the scoring schema (migration 0028)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine


class TestScoringSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture scoring user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            for table in (
                "scoring.weight",
                "scoring.job_score",
                "scoring.cv_chunk_embedding",
                "scoring.user_preference",
            ):
                conn.execute(text(f"DELETE FROM {table} WHERE user_id = :id"), {"id": self.user_id})
            conn.execute(
                text(
                    "DELETE FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id LIKE 'fixture-job-%'"
                )
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id})

    def test_user_preference_defaults_to_no_filters(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO scoring.user_preference (user_id) VALUES (:id)"),
                {"id": self.user_id},
            )
            row = conn.execute(
                text(
                    "SELECT preferred_locations, remote_ok, contract_types, "
                    "excluded_ir35_statuses, min_seniority_band, max_seniority_band, "
                    "min_salary_annual, min_rate_daily, max_posting_age_days "
                    "FROM scoring.user_preference WHERE user_id = :id"
                ),
                {"id": self.user_id},
            ).one()
        self.assertEqual(
            (
                list(row.preferred_locations),
                row.remote_ok,
                list(row.contract_types),
                list(row.excluded_ir35_statuses),
                row.min_seniority_band,
                row.max_seniority_band,
                row.min_salary_annual,
                row.min_rate_daily,
                row.max_posting_age_days,
            ),
            ([], "no_preference", [], [], None, None, None, None, None),
        )

    def test_user_preference_rejects_a_bad_remote_ok_value(self) -> None:
        with self.engine.begin() as conn, self.assertRaises(Exception):
            conn.execute(
                text(
                    "INSERT INTO scoring.user_preference (user_id, remote_ok) "
                    "VALUES (:id, 'sometimes')"
                ),
                {"id": self.user_id},
            )

    def test_job_chunk_embedding_has_no_rls_and_a_composite_key(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding "
                    "(job_group_id, section, chunk_index, chunk_text, embedding, "
                    "embedding_model) VALUES "
                    "('fixture-job-1', 'responsibilities', 0, 'zzfixture text', "
                    "CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"v": "[" + ",".join(["0.0"] * 768) + "]"},
            )
            count = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-1'"
                )
            ).scalar_one()
        self.assertEqual(count, 1)

    def test_job_score_and_weight_are_keyed_on_user_and_job(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:id, 'fixture-job-2', true)"
                ),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.weight (user_id, component, weight) "
                    "VALUES (:id, 'vector_similarity', 0.25)"
                ),
                {"id": self.user_id},
            )
            score = conn.execute(
                text(
                    "SELECT hard_filter_passed FROM scoring.job_score "
                    "WHERE user_id = :id AND job_group_id = 'fixture-job-2'"
                ),
                {"id": self.user_id},
            ).scalar_one()
        self.assertTrue(score)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a; source .env; set +a; export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}"; cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_schema -v`
Expected: FAIL — `relation "scoring.user_preference" does not exist`.

- [ ] **Step 3: Write the migration**

Find the current head revision first: `grep -rl "down_revision = None" db/migrations/versions/*.py` won't help; instead run `venv/bin/alembic -c db/alembic.ini heads` (from `job_search/`, with `DATABASE_URL` exported as above) to get the current head — this plan was written against head `0027`; use whatever `alembic heads` actually prints as `down_revision`.

Create `db/migrations/versions/0028_create_scoring_schema.py`:

```python
"""create the scoring schema (PLAN.md Step 15)

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-27

Five tables for the scoring funnel:

- user_preference: per-user hard-filter settings (RLS). A missing field
  (NULL, or an empty array) means "no filter on this dimension" — never
  coerced to excluding or including everything (DECISIONS.md §2.13's
  never-default-unknown principle, applied here to preferences too).
- job_chunk_embedding: SHARED job-description chunks and their vectors,
  computed once. No RLS — same pattern as dedup.job_blocking_keys (0008):
  owner-role writes via a pipeline CLI subcommand, job_search_app gets
  SELECT only, dbt reads it as a plain source.
- cv_chunk_embedding: per-user CV chunks and their vectors (RLS) — CV
  embeddings are per-user and never shared (PLAN.md Step 15).
- job_score: per-user component scores (RLS), grain (user_id,
  job_group_id) — the silver table dbt's fct_job_score mart reads from.
- weight: per-user calibration weights (RLS), written by Step 16, read
  here with a documented default when no row exists yet.

scoring.job_label (Step 16's hand-labels) is NOT created here — it
belongs to Step 16's own migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    """Enable RLS and add the standard app.current_user_id policy.

    Args:
        table: The unqualified table name, in the `scoring` schema.
    """
    op.execute(f"ALTER TABLE scoring.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_isolation ON scoring.{table} "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS scoring")

    op.create_table(
        "user_preference",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id"),
            primary_key=True,
        ),
        sa.Column(
            "preferred_locations",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "remote_ok", sa.Text(), nullable=False, server_default="no_preference"
        ),
        sa.Column(
            "contract_types", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "excluded_ir35_statuses",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("min_seniority_band", sa.Text(), nullable=True),
        sa.Column("max_seniority_band", sa.Text(), nullable=True),
        sa.Column("min_salary_annual", sa.Numeric(), nullable=True),
        sa.Column("min_rate_daily", sa.Numeric(), nullable=True),
        sa.Column("max_posting_age_days", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "remote_ok IN ('required', 'preferred', 'no_preference', 'excluded')",
            name="ck_user_preference_remote_ok",
        ),
        sa.CheckConstraint(
            "min_seniority_band IS NULL OR min_seniority_band IN "
            "('junior', 'mid', 'senior', 'lead', 'principal')",
            name="ck_user_preference_min_seniority",
        ),
        sa.CheckConstraint(
            "max_seniority_band IS NULL OR max_seniority_band IN "
            "('junior', 'mid', 'senior', 'lead', 'principal')",
            name="ck_user_preference_max_seniority",
        ),
        schema="scoring",
    )
    _rls("user_preference")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON scoring.user_preference TO job_search_app"
    )

    op.create_table(
        "job_chunk_embedding",
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("job_group_id", "section", "chunk_index"),
        sa.CheckConstraint(
            "section IN ('company_blurb', 'responsibilities', 'requirements', "
            "'nice_to_have', 'benefits', 'other')",
            name="ck_job_chunk_embedding_section",
        ),
        schema="scoring",
    )
    op.execute(
        "CREATE INDEX job_chunk_embedding_hnsw ON scoring.job_chunk_embedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "GRANT SELECT ON scoring.job_chunk_embedding TO job_search_app"
    )

    op.create_table(
        "cv_chunk_embedding",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("cv_version", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "cv_version", "section", "chunk_index"),
        sa.CheckConstraint(
            "section IN ('summary', 'experience', 'skills', 'education', "
            "'certifications', 'projects')",
            name="ck_cv_chunk_embedding_section",
        ),
        schema="scoring",
    )
    op.execute(
        "CREATE INDEX cv_chunk_embedding_hnsw ON scoring.cv_chunk_embedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    _rls("cv_chunk_embedding")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON scoring.cv_chunk_embedding "
        "TO job_search_app"
    )

    op.create_table(
        "job_score",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("hard_filter_passed", sa.Boolean(), nullable=False),
        sa.Column("vector_similarity_score", sa.Numeric(), nullable=True),
        sa.Column("reranker_score", sa.Numeric(), nullable=True),
        sa.Column("skill_coverage_score", sa.Numeric(), nullable=True),
        sa.Column("llm_fit_score", sa.Numeric(), nullable=True),
        sa.Column("llm_rationale", sa.Text(), nullable=True),
        sa.Column("llm_missing_skills", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("llm_stretch_flag", sa.Boolean(), nullable=True),
        sa.Column("final_score", sa.Numeric(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column(
            "scored_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "job_group_id"),
        schema="scoring",
    )
    _rls("job_score")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON scoring.job_score TO job_search_app"
    )

    op.create_table(
        "weight",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("weight", sa.Numeric(), nullable=False),
        sa.Column(
            "fitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "component"),
        sa.CheckConstraint(
            "component IN ('vector_similarity', 'reranker', 'skill_coverage', "
            "'llm_fit')",
            name="ck_weight_component",
        ),
        schema="scoring",
    )
    _rls("weight")
    op.execute("GRANT SELECT, INSERT, UPDATE ON scoring.weight TO job_search_app")


def downgrade() -> None:
    op.drop_table("weight", schema="scoring")
    op.drop_table("job_score", schema="scoring")
    op.drop_table("cv_chunk_embedding", schema="scoring")
    op.drop_table("job_chunk_embedding", schema="scoring")
    op.drop_table("user_preference", schema="scoring")
    op.execute("DROP SCHEMA IF EXISTS scoring")
```

Check whether `pgvector.sqlalchemy.Vector` is already used elsewhere (`grep -rn "pgvector.sqlalchemy" db/migrations/versions/*.py packages/core`) — if migration 0022 instead created `esco.skill_embedding.embedding` with a raw `op.execute("... vector(768) ...")` (no `pgvector.sqlalchemy.Vector` type), follow that same raw-SQL-column style here instead, for consistency: replace every `sa.Column("embedding", Vector(768), nullable=False)` with a plain column added via `op.execute("ALTER TABLE scoring.job_chunk_embedding ADD COLUMN embedding vector(768) NOT NULL")` issued right after `op.create_table(...)` (with the column omitted from the `create_table` call), matching exactly how 0022 did it. Use whichever style the codebase actually uses — do not introduce a second convention.

Apply it: `venv/bin/alembic -c db/alembic.ini upgrade head` (with `DATABASE_URL` exported to `localhost`).

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_schema -v`
Expected: 4/4 PASS.

- [ ] **Step 5: Lint and commit**

```bash
cd job_search && venv/bin/ruff check packages apps db && venv/bin/isort packages apps db && venv/bin/black packages apps db
git add db/migrations/versions/0028_create_scoring_schema.py packages/core/tests/integration/test_scoring_schema.py
git commit -m "feat(job_search): create the scoring schema (Step 15)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Preferences + Stage 1 (hard filters)

**Files:**
- Create: `packages/core/core/scoring/__init__.py` (empty)
- Create: `packages/core/core/scoring/preferences.py`
- Create: `packages/core/core/scoring/hard_filters.py`
- Create: `apps/api/app/routers/scoring.py`
- Create: `apps/ui/app/pages/8_Scoring_Preferences.py`
- Modify: `apps/api/app/main.py` (register the router)
- Modify: `apps/pipeline/app/cli.py` (add `score-filter-jobs`)
- Test: `packages/core/tests/integration/test_scoring_preferences.py`
- Test: `packages/core/tests/integration/test_scoring_hard_filters.py`

**Interfaces:**
- Consumes: `core.db.session.session_scope`, `dim_job` (via the app-role engine — it's shared job data, no RLS on it, but the app role already has SELECT per existing grants).
- Produces (used by Tasks 5-8): `@dataclass(frozen=True) UserPreference(preferred_locations, remote_ok, contract_types, excluded_ir35_statuses, min_seniority_band, max_seniority_band, min_salary_annual, min_rate_daily, max_posting_age_days)`; `read_preference(engine, user_id) -> UserPreference` (returns the all-default `UserPreference()` if no row exists — never raises for a missing row); `write_preference(engine, user_id, preference: UserPreference) -> None` (upsert); `run_hard_filters(engine, user_id, *, limit=None) -> int` (returns jobs written) in `hard_filters.py`.

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/integration/test_scoring_preferences.py`:

```python
"""Integration tests for core.scoring.preferences against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.scoring.preferences import UserPreference, read_preference, write_preference


class TestPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture prefs user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.user_preference WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id})

    def test_reading_an_unset_preference_returns_all_defaults(self) -> None:
        pref = read_preference(self.engine, self.user_id)
        self.assertEqual(pref, UserPreference())

    def test_writing_then_reading_round_trips(self) -> None:
        written = UserPreference(
            preferred_locations=["London", "Remote"],
            remote_ok="preferred",
            contract_types=["contract", "ftc"],
            excluded_ir35_statuses=["inside"],
            min_seniority_band="senior",
            max_seniority_band="principal",
            min_salary_annual=None,
            min_rate_daily=500,
            max_posting_age_days=30,
        )
        write_preference(self.engine, self.user_id, written)
        self.assertEqual(read_preference(self.engine, self.user_id), written)

    def test_writing_twice_updates_rather_than_duplicating(self) -> None:
        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=400))
        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=600))
        self.assertEqual(read_preference(self.engine, self.user_id).min_rate_daily, 600)
        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.user_preference WHERE user_id = :id"
                ),
                {"id": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 1)

    def test_a_user_cannot_read_another_users_preference_via_the_app_role(
        self,
    ) -> None:
        from core.settings import get_settings
        from core.db.session import build_engine, session_scope

        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=999))
        app_engine = build_engine(get_settings().app_database_url)
        other_user = uuid.uuid4()
        with session_scope(app_engine, user_id=other_user) as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM scoring.user_preference WHERE user_id = :id"
                ),
                {"id": self.user_id},
            ).one_or_none()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
```

Create `packages/core/tests/integration/test_scoring_hard_filters.py`:

```python
"""Integration tests for core.scoring.hard_filters against live Postgres.

Uses real gold.dim_job rows via fixture job_group_ids so these tests never
touch real job data. dim_job is built by dbt from silver sources this suite
does not have access to seed directly, so these tests insert straight into
gold.dim_job (owner role) and clean it up — the same tactic
test_skills_router.py's _insert_esco_skill uses for esco.skill.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.scoring.hard_filters import run_hard_filters
from core.scoring.preferences import UserPreference, write_preference


class TestHardFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture filters user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.user_preference WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE 'fixture-job-%'")
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id})

    def _insert_job(self, job_group_id: str, **overrides) -> None:
        """Insert one minimal gold.dim_job fixture row."""
        defaults = {
            "job_group_id": job_group_id,
            "title": "zzfixture role",
            "company": "zzfixture co",
            "location": "London",
            "engagement_type": "contract",
            "ir35_status": "outside",
            "seniority_band": "senior",
            "rate_currency": "GBP",
            "rate_daily_equivalent": 600,
            "rate_annualised": None,
            "posted_at": "2026-09-01",
        }
        defaults.update(overrides)
        columns = ", ".join(defaults)
        placeholders = ", ".join(f":{k}" for k in defaults)
        with self.engine.begin() as conn:
            conn.execute(
                text(f"INSERT INTO gold.dim_job ({columns}) VALUES ({placeholders})"),
                defaults,
            )

    def _passed(self, job_group_id: str) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT hard_filter_passed FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": job_group_id},
            ).scalar_one()

    def test_no_preference_row_at_all_passes_every_job(self) -> None:
        self._insert_job("fixture-job-nopref")
        run_hard_filters(self.engine, self.user_id)
        self.assertTrue(self._passed("fixture-job-nopref"))

    def test_excluded_ir35_status_fails_a_matching_job_but_not_others(self) -> None:
        self._insert_job("fixture-job-inside", ir35_status="inside")
        self._insert_job("fixture-job-outside", ir35_status="outside")
        write_preference(
            self.engine, self.user_id, UserPreference(excluded_ir35_statuses=["inside"])
        )
        run_hard_filters(self.engine, self.user_id)
        self.assertFalse(self._passed("fixture-job-inside"))
        self.assertTrue(self._passed("fixture-job-outside"))

    def test_unknown_ir35_status_is_never_auto_excluded(self) -> None:
        self._insert_job("fixture-job-unknown-ir35", ir35_status="unknown")
        write_preference(
            self.engine, self.user_id, UserPreference(excluded_ir35_statuses=["inside"])
        )
        run_hard_filters(self.engine, self.user_id)
        self.assertTrue(self._passed("fixture-job-unknown-ir35"))

    def test_min_rate_daily_excludes_a_lower_rate(self) -> None:
        self._insert_job("fixture-job-lowrate", rate_daily_equivalent=300)
        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=500))
        run_hard_filters(self.engine, self.user_id)
        self.assertFalse(self._passed("fixture-job-lowrate"))

    def test_a_rate_in_a_different_currency_is_never_compared_to_the_floor(
        self,
    ) -> None:
        # rate_annualised/rate_daily_equivalent are in the job's OWN
        # rate_currency, never converted (_gold.yml's own documented
        # caveat) — a non-GBP rate must not be treated as clearing or
        # missing a GBP floor by accident.
        self._insert_job(
            "fixture-job-usd", rate_currency="USD", rate_daily_equivalent=900
        )
        write_preference(self.engine, self.user_id, UserPreference(min_rate_daily=500))
        run_hard_filters(self.engine, self.user_id)
        self.assertFalse(self._passed("fixture-job-usd"))

    def test_seniority_band_range_is_inclusive(self) -> None:
        self._insert_job("fixture-job-junior", seniority_band="junior")
        write_preference(
            self.engine,
            self.user_id,
            UserPreference(min_seniority_band="mid", max_seniority_band="lead"),
        )
        run_hard_filters(self.engine, self.user_id)
        self.assertFalse(self._passed("fixture-job-junior"))

    def test_rerunning_updates_rather_than_duplicating(self) -> None:
        self._insert_job("fixture-job-rerun")
        run_hard_filters(self.engine, self.user_id)
        run_hard_filters(self.engine, self.user_id)
        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-rerun'"
                ),
                {"u": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify both fail**

Run: `set -a; source .env; set +a; export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}" APP_DATABASE_URL="${APP_DATABASE_URL/@postgres:/@localhost:}"; cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_preferences tests.integration.test_scoring_hard_filters -v`
Expected: ImportError — `core.scoring.preferences` / `core.scoring.hard_filters` don't exist yet.

- [ ] **Step 3: Implement `preferences.py`**

Create `packages/core/core/scoring/__init__.py` (empty file).

Create `packages/core/core/scoring/preferences.py`:

```python
"""Per-user hard-filter preferences for the scoring funnel (PLAN.md Step 15).

A field left unset means "no filter on this dimension" — never coerced to
excluding or including everything, the same never-default-unknown principle
DECISIONS.md §2.13 states for IR35/engagement type, applied here to
preferences that gate on those same fields.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import Engine, text

from core.db.session import session_scope


@dataclass(frozen=True)
class UserPreference:
    """One user's hard-filter settings.

    Attributes:
        preferred_locations: Locations to filter to; empty means no filter.
        remote_ok: "required" | "preferred" | "no_preference" | "excluded".
        contract_types: Subset of permanent/contract/ftc/interim to filter
            to; empty means no filter.
        excluded_ir35_statuses: IR35 statuses to exclude; empty excludes
            nothing (never assume an exclusion the user didn't state).
        min_seniority_band: Lowest acceptable band (inclusive), or None.
        max_seniority_band: Highest acceptable band (inclusive), or None.
        min_salary_annual: Minimum annualised salary, or None.
        min_rate_daily: Minimum day rate, or None.
        max_posting_age_days: Oldest acceptable posting age, or None.
    """

    preferred_locations: list[str] = field(default_factory=list)
    remote_ok: str = "no_preference"
    contract_types: list[str] = field(default_factory=list)
    excluded_ir35_statuses: list[str] = field(default_factory=list)
    min_seniority_band: str | None = None
    max_seniority_band: str | None = None
    min_salary_annual: float | None = None
    min_rate_daily: float | None = None
    max_posting_age_days: int | None = None


def read_preference(engine: Engine, user_id: uuid.UUID) -> UserPreference:
    """Read a user's preferences, or the all-default value if unset.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose preferences to read.

    Returns:
        The stored `UserPreference`, or `UserPreference()` if this user has
        never saved one — a missing row is not an error, it is "no filters."
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                "SELECT preferred_locations, remote_ok, contract_types, "
                "excluded_ir35_statuses, min_seniority_band, max_seniority_band, "
                "min_salary_annual, min_rate_daily, max_posting_age_days "
                "FROM scoring.user_preference WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        ).one_or_none()
    if row is None:
        return UserPreference()
    return UserPreference(
        preferred_locations=list(row.preferred_locations),
        remote_ok=row.remote_ok,
        contract_types=list(row.contract_types),
        excluded_ir35_statuses=list(row.excluded_ir35_statuses),
        min_seniority_band=row.min_seniority_band,
        max_seniority_band=row.max_seniority_band,
        min_salary_annual=(
            float(row.min_salary_annual) if row.min_salary_annual is not None else None
        ),
        min_rate_daily=(
            float(row.min_rate_daily) if row.min_rate_daily is not None else None
        ),
        max_posting_age_days=row.max_posting_age_days,
    )


_UPSERT = text(
    "INSERT INTO scoring.user_preference (user_id, preferred_locations, "
    "remote_ok, contract_types, excluded_ir35_statuses, min_seniority_band, "
    "max_seniority_band, min_salary_annual, min_rate_daily, "
    "max_posting_age_days, updated_at) "
    "VALUES (:user_id, :preferred_locations, :remote_ok, :contract_types, "
    ":excluded_ir35_statuses, :min_seniority_band, :max_seniority_band, "
    ":min_salary_annual, :min_rate_daily, :max_posting_age_days, now()) "
    "ON CONFLICT (user_id) DO UPDATE SET "
    "preferred_locations = EXCLUDED.preferred_locations, "
    "remote_ok = EXCLUDED.remote_ok, "
    "contract_types = EXCLUDED.contract_types, "
    "excluded_ir35_statuses = EXCLUDED.excluded_ir35_statuses, "
    "min_seniority_band = EXCLUDED.min_seniority_band, "
    "max_seniority_band = EXCLUDED.max_seniority_band, "
    "min_salary_annual = EXCLUDED.min_salary_annual, "
    "min_rate_daily = EXCLUDED.min_rate_daily, "
    "max_posting_age_days = EXCLUDED.max_posting_age_days, "
    "updated_at = now()"
)


def write_preference(
    engine: Engine, user_id: uuid.UUID, preference: UserPreference
) -> None:
    """Create or replace a user's preferences.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose preferences to write.
        preference: The full preference set to store.
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            _UPSERT,
            {
                "user_id": user_id,
                "preferred_locations": preference.preferred_locations,
                "remote_ok": preference.remote_ok,
                "contract_types": preference.contract_types,
                "excluded_ir35_statuses": preference.excluded_ir35_statuses,
                "min_seniority_band": preference.min_seniority_band,
                "max_seniority_band": preference.max_seniority_band,
                "min_salary_annual": preference.min_salary_annual,
                "min_rate_daily": preference.min_rate_daily,
                "max_posting_age_days": preference.max_posting_age_days,
            },
        )
```

- [ ] **Step 4: Implement `hard_filters.py`**

First read `dbt/models/gold/dim_job.sql` and `dbt/models/gold/_gold.yml` to get the real column names for location, engagement type, IR35 status, seniority band, rate/salary and posted date — use those exact names below in place of any placeholder-looking name.

Create `packages/core/core/scoring/hard_filters.py`:

```python
"""Stage 1 of the scoring funnel: hard filters (PLAN.md Step 15).

Cheap, kills most of the pool before anything expensive runs. A NULL/empty
preference field means "no filter on this dimension" (core.scoring.
preferences.UserPreference already defaults every field this way, so a user
with no saved preferences at all passes every job here).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.scoring.preferences import read_preference

_SELECT_JOBS = text(
    "SELECT job_group_id, location, engagement_type, ir35_status, "
    "seniority_band, rate_currency, rate_daily_equivalent, rate_annualised, "
    "posted_at FROM gold.dim_job"
)
_ASSUMED_CURRENCY = "GBP"
"""rate_annualised/rate_daily_equivalent are in the job's own rate_currency,
never converted (_gold.yml's documented caveat: comparing them across
currencies is wrong). Preferences are assumed GBP (Step 5a's UK/IR35
context) — a job in another currency cannot be confirmed to clear a floor,
so it is filtered out on that dimension rather than compared unsafely.
Currency-normalised comparison is Step 21a's job, not this one's."""
_SENIORITY_ORDER = ["junior", "mid", "senior", "lead", "principal"]
_UPSERT_PASSED = text(
    "INSERT INTO scoring.job_score (user_id, job_group_id, hard_filter_passed) "
    "VALUES (:user_id, :job_group_id, :passed) "
    "ON CONFLICT (user_id, job_group_id) DO UPDATE SET "
    "hard_filter_passed = EXCLUDED.hard_filter_passed"
)


def _passes(job: dict, pref) -> bool:
    """Decide whether one job clears one user's hard filters.

    Args:
        job: A row from `_SELECT_JOBS`, as a mapping.
        pref: The user's `UserPreference`.

    Returns:
        True if the job passes every set filter.
    """
    if pref.preferred_locations and job["location"] not in pref.preferred_locations:
        return False
    if pref.contract_types and job["engagement_type"] not in pref.contract_types:
        return False
    if job["ir35_status"] in pref.excluded_ir35_statuses:
        return False
    band = job["seniority_band"]
    if band is not None:
        if pref.min_seniority_band and _SENIORITY_ORDER.index(
            band
        ) < _SENIORITY_ORDER.index(pref.min_seniority_band):
            return False
        if pref.max_seniority_band and _SENIORITY_ORDER.index(
            band
        ) > _SENIORITY_ORDER.index(pref.max_seniority_band):
            return False
    if pref.min_rate_daily or pref.min_salary_annual:
        if job["rate_currency"] not in (None, _ASSUMED_CURRENCY):
            # Cannot safely compare a non-GBP figure to a GBP floor.
            return False
        if pref.min_rate_daily and (
            job["rate_daily_equivalent"] is None
            or job["rate_daily_equivalent"] < pref.min_rate_daily
        ):
            # No day-rate figure at all cannot be confirmed to clear the
            # floor either, so it is filtered out rather than assumed to pass.
            return False
        if pref.min_salary_annual and (
            job["rate_annualised"] is None
            or job["rate_annualised"] < pref.min_salary_annual
        ):
            return False
    return True


def run_hard_filters(
    engine: Engine, user_id: uuid.UUID, *, limit: int | None = None
) -> int:
    """Run stage 1 for one user over the whole (or `limit`-capped) job pool.

    Args:
        engine: The app-role engine (RLS-enforced for the write; `dim_job`
            itself is shared, no RLS).
        user_id: Whose preferences to filter by.
        limit: Cap the number of jobs considered (tests only); `None` covers
            every job in `dim_job`.

    Returns:
        The number of jobs written (pass or fail; every considered job gets
        a row).
    """
    pref = read_preference(engine, user_id)
    query = _SELECT_JOBS
    if limit is not None:
        query = text(query.text + " LIMIT :limit")
    with engine.connect() as conn:
        jobs = conn.execute(
            query, {"limit": limit} if limit is not None else {}
        ).mappings().all()
    with session_scope(engine, user_id=user_id) as conn:
        for job in jobs:
            conn.execute(
                _UPSERT_PASSED,
                {
                    "user_id": user_id,
                    "job_group_id": job["job_group_id"],
                    "passed": _passes(job, pref),
                },
            )
    return len(jobs)
```

- [ ] **Step 5: Run to verify both test files pass**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_preferences tests.integration.test_scoring_hard_filters -v`
Expected: all PASS. If `_insert_job`'s column names were wrong, fix the test to match the real `dim_job` schema — do not change `hard_filters.py` to match a wrong test.

- [ ] **Step 6: API endpoints and CLI**

Create `apps/api/app/routers/scoring.py`:

```python
"""Scoring-preferences endpoints (PLAN.md Step 15).

Follows the same `get_current_user_id` pattern as apps/api/app/routers/cv.py
— every endpoint here 501s until Step 22a's identity middleware exists,
exactly like the CV router does today (verified live: GET /cv/truth-base
returns 501 on this stack). Not a regression introduced here.
"""

from __future__ import annotations

import uuid

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from core.db.session import get_current_user_id
from core.scoring.preferences import UserPreference, read_preference, write_preference

router = APIRouter()


class UserPreferenceModel(BaseModel):
    """Request/response body for scoring preferences."""

    preferred_locations: list[str] = Field(default_factory=list)
    remote_ok: str = "no_preference"
    contract_types: list[str] = Field(default_factory=list)
    excluded_ir35_statuses: list[str] = Field(default_factory=list)
    min_seniority_band: str | None = None
    max_seniority_band: str | None = None
    min_salary_annual: float | None = None
    min_rate_daily: float | None = None
    max_posting_age_days: int | None = None


@router.get("/scoring/preferences", response_model=UserPreferenceModel)
def get_preferences(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> UserPreferenceModel:
    """Read the caller's scoring preferences.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The stored preferences, or all defaults if never saved.
    """
    pref = read_preference(engine, user_id)
    return UserPreferenceModel(**pref.__dict__)


@router.put("/scoring/preferences", response_model=UserPreferenceModel)
def put_preferences(
    body: UserPreferenceModel,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> UserPreferenceModel:
    """Replace the caller's scoring preferences.

    Args:
        body: The full preference set to store.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The preferences as stored.
    """
    pref = UserPreference(**body.model_dump())
    write_preference(engine, user_id, pref)
    return body
```

In `apps/api/app/main.py`, add `from app.routers import scoring` to the import line and `app.include_router(scoring.router)` after the other `include_router` calls.

In `apps/pipeline/app/cli.py`, add a `score-filter-jobs` subcommand: import `run_hard_filters` from `core.scoring.hard_filters`, add a parser with `--user-id` (required, `type=uuid.UUID`) and `--limit` (optional int), and a `_cmd_score_filter_jobs(args)` following the exact shape of `_cmd_map_cv_skills` (build the app engine via `build_engine(settings.app_database_url)`, call `run_hard_filters(app_engine, args.user_id, limit=args.limit)`, print `f"score-filter-jobs complete: considered={n}"`). Wire it into the dispatch `if args.command == "score-filter-jobs": return _cmd_score_filter_jobs(args)`.

- [ ] **Step 7: Settings page**

Create `apps/ui/app/pages/8_Scoring_Preferences.py` — follow `apps/ui/app/pages/5_CV_Editor.py`'s structure (imports, `_settings = get_settings()`, `httpx` GET/PUT helpers with the same error-handling shape as `6_Skill_Review.py`'s `_get`/`_post`). Fields: multiselect or comma-separated text input for `preferred_locations`; selectbox for `remote_ok` (`required`/`preferred`/`no_preference`/`excluded`); multiselect for `contract_types`; multiselect for `excluded_ir35_statuses`; selectboxes for `min_seniority_band`/`max_seniority_band` (options: the 5 bands plus a blank/"No minimum" option mapping to `None`); number inputs for `min_salary_annual`/`min_rate_daily`/`max_posting_age_days` (0 or empty mapping to `None`). A "Save" button PUTs to `/scoring/preferences`. Add a `st.info` banner at the top: "This page needs sign-in (PLAN.md Step 22a), not yet built — it will 501 until then. Preferences can be set directly via SQL or a future admin path in the meantime." (Copy this banner's wording pattern from any existing "known limitation" note in `6_Skill_Review.py` if one fits better — keep it factual, not apologetic.)

- [ ] **Step 8: Verify, lint, commit**

```bash
cd job_search
venv/bin/python -m py_compile apps/ui/app/pages/8_Scoring_Preferences.py apps/api/app/routers/scoring.py
venv/bin/ruff check packages apps db && venv/bin/isort packages apps db && venv/bin/black packages apps db
set -a; source .env; set +a; export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}" APP_DATABASE_URL="${APP_DATABASE_URL/@postgres:/@localhost:}"
cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_preferences tests.integration.test_scoring_hard_filters -v
```
Expected: compiles clean, lint clean, tests pass.

```bash
git add packages/core/core/scoring apps/api/app/routers/scoring.py apps/api/app/main.py apps/ui/app/pages/8_Scoring_Preferences.py apps/pipeline/app/cli.py packages/core/tests/integration/test_scoring_preferences.py packages/core/tests/integration/test_scoring_hard_filters.py
git commit -m "feat(job_search): scoring preferences and hard filters (Step 15 stage 1)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Job chunking + embedding (stage 2, job side)

**Files:**
- Create: `packages/core/core/scoring/job_chunking.py`
- Modify: `apps/pipeline/app/cli.py` (add `chunk-embed-jobs`)
- Modify: `requirements.txt` (add `llama-index-core`)
- Test: `packages/core/tests/integration/test_scoring_job_chunking.py`

**Interfaces:**
- Consumes: `core.embedding.ollama.embed_text`, `gold.dim_job.description`.
- Produces (used by Task 5): `detect_sections(description: str) -> list[tuple[str, str]]` (list of `(section, text)`, in document order); `chunk_and_embed_jobs(engine, *, embed, embedding_model, limit=None) -> int` (jobs newly chunked) in `job_chunking.py`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_scoring_job_chunking.py`:

```python
"""Integration tests for core.scoring.job_chunking against live Postgres."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.scoring.job_chunking import chunk_and_embed_jobs, detect_sections

_STRUCTURED = """We are a fast-growing fintech company.

Responsibilities
Build and maintain our core ledger service.
Own the on-call rotation for payments.

Requirements
5+ years of backend experience.
Strong SQL skills.

Nice to have
Experience with Kafka.

Benefits
25 days holiday.
"""

_UNSTRUCTURED = "Great company looking for a great engineer to do great things."


class TestDetectSections(unittest.TestCase):
    def test_a_structured_description_splits_into_its_named_sections(self) -> None:
        sections = detect_sections(_STRUCTURED)
        names = [s for s, _ in sections]
        self.assertEqual(
            names, ["company_blurb", "responsibilities", "requirements", "nice_to_have", "benefits"]
        )
        self.assertIn("ledger service", dict(sections)["responsibilities"])

    def test_an_unstructured_description_becomes_one_other_section(self) -> None:
        sections = detect_sections(_UNSTRUCTURED)
        self.assertEqual([s for s, _ in sections], ["other"])

    def test_a_long_line_is_never_mistaken_for_a_heading(self) -> None:
        text_ = (
            "Requirements and expectations for this particular role include the "
            "following extensive list of desirable attributes\n"
            "5+ years experience.\n"
        )
        sections = detect_sections(text_)
        # The long line does not match a canonical heading exactly enough, or
        # is over the length cutoff, so it stays inside company_blurb/other —
        # not treated as a "requirements" heading of its own.
        self.assertNotIn("requirements", [s for s, _ in sections])


class TestChunkAndEmbedJobs(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id LIKE 'fixture-job-%'"
                )
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE 'fixture-job-%'")
            )

    def _insert_job(self, job_group_id: str, description: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title, company, "
                    "description) VALUES (:j, 'zzfixture role', 'zzfixture co', :d)"
                ),
                {"j": job_group_id, "d": description},
            )

    def _fake_embed(self, text_: str) -> list[float]:
        return [0.0] * 768

    def test_chunking_a_job_writes_one_row_per_chunk(self) -> None:
        self._insert_job("fixture-job-chunk-1", _STRUCTURED)
        written = chunk_and_embed_jobs(
            self.engine, embed=self._fake_embed, embedding_model="zzfixture-model"
        )
        self.assertGreaterEqual(written, 1)
        with self.engine.connect() as conn:
            sections = conn.execute(
                text(
                    "SELECT DISTINCT section FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-chunk-1'"
                )
            ).scalars().all()
        self.assertIn("responsibilities", sections)

    def test_rerunning_does_not_duplicate_or_recompute_an_already_chunked_job(
        self,
    ) -> None:
        self._insert_job("fixture-job-chunk-2", _STRUCTURED)
        chunk_and_embed_jobs(
            self.engine, embed=self._fake_embed, embedding_model="zzfixture-model"
        )
        with self.engine.connect() as conn:
            before = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-chunk-2'"
                )
            ).scalar_one()
        written_again = chunk_and_embed_jobs(
            self.engine, embed=self._fake_embed, embedding_model="zzfixture-model"
        )
        with self.engine.connect() as conn:
            after = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-chunk-2'"
                )
            ).scalar_one()
        self.assertEqual(before, after)
        self.assertEqual(written_again, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_job_chunking -v`
Expected: ImportError.

- [ ] **Step 3: Add the dependency**

In `requirements.txt`, add a line `llama-index-core==0.11.*` near the other content-processing deps (check what major version is current on PyPI at implementation time and pin an exact compatible version rather than a floating one, per this repo's pinning convention elsewhere in the file — look at how `docling==2.126.0` is pinned exactly, and match that style, not a range).

- [ ] **Step 4: Implement `job_chunking.py`**

```python
"""Stage 2 (job side) of the scoring funnel: structural chunking and
embedding of job descriptions (PLAN.md Step 15).

Section detection is a heading-matching heuristic, not a parser: a short
line matching one of a fixed set of canonical phrasings starts a new
section. Real postings vary hugely in structure (aggregator HTML-to-text,
manual entries, ATS exports), so this cannot be perfect — a posting whose
headings don't match becomes one 'other' section rather than losing content
or raising. This is a documented, accepted quality boundary (see the design
spec), not a bug to chase to 100%.

Job embeddings are SHARED and computed once — no RLS on
scoring.job_chunk_embedding, matching the dedup.* pattern.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from llama_index.core.node_parser import SentenceSplitter
from sqlalchemy import Engine, text

from core.skills.vector import to_pgvector

_HEADING_MAX_CHARS = 60
_SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "responsibilities",
        re.compile(
            r"^(responsibilities|what you.ll do|the role|key duties)\s*:?\s*$", re.I
        ),
    ),
    (
        "requirements",
        re.compile(
            r"^(requirements|about you|what we.re looking for|essential|"
            r"skills? (and|&) experience)\s*:?\s*$",
            re.I,
        ),
    ),
    (
        "nice_to_have",
        re.compile(r"^(nice to have|desirable|bonus|preferred)\s*:?\s*$", re.I),
    ),
    (
        "benefits",
        re.compile(r"^(benefits|what we offer|perks)\s*:?\s*$", re.I),
    ),
]

_CHUNK_SIZE_TOKENS = 200
_CHUNK_OVERLAP_TOKENS = 20


def detect_sections(description: str) -> list[tuple[str, str]]:
    """Split a job description into named sections by a heading heuristic.

    Args:
        description: The full job description text.

    Returns:
        `(section, text)` pairs in document order. Text before the first
        detected heading is `company_blurb`. If no heading is ever detected,
        the whole description is one `other` section — never dropped.
    """
    lines = description.splitlines()
    sections: list[tuple[str, list[str]]] = [("company_blurb", [])]
    found_any_heading = False
    for line in lines:
        stripped = line.strip()
        matched_section: str | None = None
        if stripped and len(stripped) <= _HEADING_MAX_CHARS:
            for section, pattern in _SECTION_PATTERNS:
                if pattern.match(stripped):
                    matched_section = section
                    break
        if matched_section:
            found_any_heading = True
            sections.append((matched_section, []))
        else:
            sections[-1][1].append(line)
    if not found_any_heading:
        return [("other", description.strip())]
    return [
        (section, "\n".join(body).strip())
        for section, body in sections
        if "\n".join(body).strip()
    ]


_SELECT_UNCHUNKED = text(
    "SELECT j.job_group_id, j.description FROM gold.dim_job AS j "
    "LEFT JOIN (SELECT DISTINCT job_group_id FROM scoring.job_chunk_embedding) "
    "AS c ON c.job_group_id = j.job_group_id "
    "WHERE c.job_group_id IS NULL AND j.description IS NOT NULL"
)
_INSERT_CHUNK = text(
    "INSERT INTO scoring.job_chunk_embedding "
    "(job_group_id, section, chunk_index, chunk_text, embedding, embedding_model) "
    "VALUES (:job_group_id, :section, :chunk_index, :chunk_text, "
    "CAST(:embedding AS vector), :embedding_model) "
    "ON CONFLICT (job_group_id, section, chunk_index) DO NOTHING"
)


def chunk_and_embed_jobs(
    engine: Engine,
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    limit: int | None = None,
) -> int:
    """Chunk and embed every job that has no chunks yet.

    Args:
        engine: The owner-role engine (job_chunk_embedding has no RLS).
        embed: Maps a string to its embedding. `core.embedding.ollama.
            embed_text` has no built-in query/passage prefix support, so the
            raw chunk text is embedded as-is (no `search_document:` prefix
            despite nomic-embed-text supporting one — adding it is future
            work, not required for a working similarity signal).
        embedding_model: Recorded on every row written.
        limit: Cap how many jobs to chunk this run; `None` covers all.

    Returns:
        The number of jobs newly chunked (0 if all were already done).
    """
    query = _SELECT_UNCHUNKED
    if limit is not None:
        query = text(query.text + " LIMIT :limit")
    with engine.connect() as conn:
        jobs = conn.execute(
            query, {"limit": limit} if limit is not None else {}
        ).mappings().all()
    splitter = SentenceSplitter(
        chunk_size=_CHUNK_SIZE_TOKENS, chunk_overlap=_CHUNK_OVERLAP_TOKENS
    )
    for job in jobs:
        with engine.begin() as conn:
            for section, section_text in detect_sections(job["description"]):
                for chunk_index, chunk_text in enumerate(
                    splitter.split_text(section_text)
                ):
                    conn.execute(
                        _INSERT_CHUNK,
                        {
                            "job_group_id": job["job_group_id"],
                            "section": section,
                            "chunk_index": chunk_index,
                            "chunk_text": chunk_text,
                            "embedding": to_pgvector(embed(chunk_text)),
                            "embedding_model": embedding_model,
                        },
                    )
    return len(jobs)
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_job_chunking -v`
Expected: all PASS. If `llama_index.core.node_parser.SentenceSplitter`'s real import path or constructor args differ from what's written here (check by running `venv/bin/python -c "from llama_index.core.node_parser import SentenceSplitter; help(SentenceSplitter)"` after installing), fix the import/call — this is exactly the kind of detail that drifts between library versions.

- [ ] **Step 6: CLI wiring**

In `apps/pipeline/app/cli.py`, add `chunk-embed-jobs [--limit N]`: no `--user-id` (job embeddings are shared, not per-user). `_cmd_chunk_embed_jobs(args)` builds the owner engine, calls `chunk_and_embed_jobs(engine, embed=_build_embedder(http_client, settings), embedding_model=settings.embedding_model, limit=args.limit)`, prints `f"chunk-embed-jobs complete: jobs_chunked={n}"`.

- [ ] **Step 7: Lint and commit**

```bash
cd job_search && venv/bin/ruff check packages apps db && venv/bin/isort packages apps db && venv/bin/black packages apps db
git add packages/core/core/scoring/job_chunking.py apps/pipeline/app/cli.py requirements.txt packages/core/tests/integration/test_scoring_job_chunking.py
git commit -m "feat(job_search): structural JD chunking and embedding (Step 15 stage 2, jobs)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: CV chunking + embedding (stage 2, CV side)

**Files:**
- Create: `packages/core/core/scoring/cv_chunking.py`
- Modify: `apps/pipeline/app/cli.py` (add `chunk-embed-cv`)
- Test: `packages/core/tests/integration/test_scoring_cv_chunking.py`

**Interfaces:**
- Consumes: `core.cv.store.read_truth_base`, `core.cv.schema.CVTruthBase`.
- Produces (used by Task 5): `build_cv_sections(truth_base: CVTruthBase) -> list[tuple[str, str, str]]` (list of `(section, source_ref, text)`); `chunk_and_embed_cv(app_engine, user_id, *, embed, embedding_model, refresh=False) -> int` (chunks written) in `cv_chunking.py`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_scoring_cv_chunking.py`:

```python
"""Integration tests for core.scoring.cv_chunking against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.cv.schema import Bullet, CVTruthBase, Experience, Skill
from core.cv.store import write_truth_base
from core.db.session import build_engine
from core.scoring.cv_chunking import build_cv_sections, chunk_and_embed_cv
from core.settings import get_settings

_TRUTH_BASE = CVTruthBase(
    identity="zzfixture Person",
    headline="Senior Data Engineer",
    summary="A summary paragraph about zzfixture skills.",
    skills=[Skill(name="Python"), Skill(name="SQL")],
    experience=[
        Experience(
            company="zzfixture Co",
            title="Data Engineer",
            bullets=[Bullet(bullet_id="b1", text="Built a zzfixture pipeline.")],
        )
    ],
)


class TestBuildCvSections(unittest.TestCase):
    def test_sections_cover_summary_skills_and_experience(self) -> None:
        sections = build_cv_sections(_TRUTH_BASE)
        names = {s for s, _, _ in sections}
        self.assertEqual(names, {"summary", "skills", "experience"})

    def test_experience_source_ref_points_at_the_bullet(self) -> None:
        sections = build_cv_sections(_TRUTH_BASE)
        experience = [s for s in sections if s[0] == "experience"][0]
        self.assertIn("b1", experience[1])
        self.assertIn("zzfixture pipeline", experience[2])


class TestChunkAndEmbedCv(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture cv chunk user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
        write_truth_base(
            self.app_engine, self.user_id, "zzfixture markdown", _TRUTH_BASE,
            label="fixture",
        )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.cv_chunk_embedding WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :id"), {"id": self.user_id}
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id})

    def _fake_embed(self, text_: str) -> list[float]:
        return [0.0] * 768

    def test_chunking_the_cv_writes_rows_for_its_sections(self) -> None:
        written = chunk_and_embed_cv(
            self.app_engine,
            self.user_id,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
        )
        self.assertGreater(written, 0)
        with self.owner.connect() as conn:
            sections = conn.execute(
                text(
                    "SELECT DISTINCT section FROM scoring.cv_chunk_embedding "
                    "WHERE user_id = :id"
                ),
                {"id": self.user_id},
            ).scalars().all()
        self.assertIn("experience", sections)

    def test_without_refresh_a_second_call_on_the_same_version_writes_nothing(
        self,
    ) -> None:
        chunk_and_embed_cv(
            self.app_engine,
            self.user_id,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
        )
        written_again = chunk_and_embed_cv(
            self.app_engine,
            self.user_id,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
        )
        self.assertEqual(written_again, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_cv_chunking -v`
Expected: ImportError.

- [ ] **Step 3: Implement `cv_chunking.py`**

```python
"""Stage 2 (CV side) of the scoring funnel: chunking and embedding a user's
CV truth base (PLAN.md Step 15).

Unlike the job side, the CV's structure is already known (core.cv.schema) —
no heading detection needed. CV embeddings are per-user and never shared
(RLS on scoring.cv_chunk_embedding).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from llama_index.core.node_parser import SentenceSplitter
from sqlalchemy import Engine, text

from core.cv.schema import CVTruthBase
from core.cv.store import read_truth_base
from core.db.session import session_scope
from core.skills.vector import to_pgvector

_CHUNK_SIZE_TOKENS = 200
_CHUNK_OVERLAP_TOKENS = 20


def build_cv_sections(truth_base: CVTruthBase) -> list[tuple[str, str, str]]:
    """Turn a CV truth base into named, embeddable sections.

    Args:
        truth_base: The user's CV truth base.

    Returns:
        `(section, source_ref, text)` triples. `source_ref` traces back to
        the originating CV entry (e.g. a bullet_id) for later display;
        empty string where there is no finer-grained ref (e.g. `summary`,
        `skills`). A section with no content is omitted, not emitted empty.
    """
    sections: list[tuple[str, str, str]] = []
    if truth_base.summary:
        sections.append(("summary", "", truth_base.summary))
    if truth_base.skills:
        sections.append(
            ("skills", "", ", ".join(s.name for s in truth_base.skills))
        )
    for exp in truth_base.experience:
        for bullet in exp.bullets:
            sections.append(
                ("experience", bullet.bullet_id, f"{exp.title} at {exp.company}: {bullet.text}")
            )
    if truth_base.education:
        sections.append(
            (
                "education",
                "",
                "; ".join(
                    f"{e.qualification or ''} {e.institution}".strip()
                    for e in truth_base.education
                ),
            )
        )
    if truth_base.qualifications:
        sections.append(
            ("certifications", "", "; ".join(q.name for q in truth_base.qualifications))
        )
    if truth_base.projects:
        sections.append(
            (
                "projects",
                "",
                "; ".join(f"{p.name}: {p.description}" for p in truth_base.projects),
            )
        )
    return sections


_SELECT_EXISTING_VERSION = text(
    "SELECT DISTINCT cv_version FROM scoring.cv_chunk_embedding "
    "WHERE user_id = :user_id LIMIT 1"
)
_DELETE_FOR_USER = text(
    "DELETE FROM scoring.cv_chunk_embedding WHERE user_id = :user_id"
)
_INSERT_CHUNK = text(
    "INSERT INTO scoring.cv_chunk_embedding "
    "(user_id, cv_version, section, chunk_index, source_ref, chunk_text, "
    "embedding, embedding_model) "
    "VALUES (:user_id, :cv_version, :section, :chunk_index, :source_ref, "
    ":chunk_text, CAST(:embedding AS vector), :embedding_model)"
)


def chunk_and_embed_cv(
    app_engine: Engine,
    user_id: uuid.UUID,
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    refresh: bool = False,
) -> int:
    """Chunk and embed one user's current CV truth base.

    Args:
        app_engine: The app-role engine (RLS-enforced).
        user_id: Whose CV to chunk.
        embed: Maps a string to its embedding — the same raw-text
            `embed_text` call the job side uses (no query/passage prefix;
            see `job_chunking.chunk_and_embed_jobs`'s docstring).
        embedding_model: Recorded on every row written.
        refresh: Recompute even if this CV version already has chunks
            (e.g. after an embedding-model change). Without it, a call for
            a version that already has rows writes nothing.

    Returns:
        The number of chunks written (0 if skipped because already current).

    Raises:
        LookupError: If the user has no CV truth base.
    """
    stored = read_truth_base(app_engine, user_id)
    if stored is None:
        raise LookupError(f"user {user_id} has no CV truth base")
    with session_scope(app_engine, user_id=user_id) as conn:
        existing_version = conn.execute(
            _SELECT_EXISTING_VERSION, {"user_id": user_id}
        ).scalar_one_or_none()
    if existing_version == stored.version and not refresh:
        return 0
    splitter = SentenceSplitter(
        chunk_size=_CHUNK_SIZE_TOKENS, chunk_overlap=_CHUNK_OVERLAP_TOKENS
    )
    written = 0
    with session_scope(app_engine, user_id=user_id) as conn:
        conn.execute(_DELETE_FOR_USER, {"user_id": user_id})
        for section, source_ref, section_text in build_cv_sections(stored.truth_base):
            for chunk_index, chunk_text in enumerate(splitter.split_text(section_text)):
                conn.execute(
                    _INSERT_CHUNK,
                    {
                        "user_id": user_id,
                        "cv_version": stored.version,
                        "section": section,
                        "chunk_index": chunk_index,
                        "source_ref": source_ref,
                        "chunk_text": chunk_text,
                        "embedding": to_pgvector(embed(chunk_text)),
                        "embedding_model": embedding_model,
                    },
                )
                written += 1
    return written
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_cv_chunking -v`
Expected: all PASS.

- [ ] **Step 5: CLI wiring**

In `apps/pipeline/app/cli.py`, add `chunk-embed-cv --user-id <id> [--refresh]`, mirroring `map-cv-skills`'s argument shape exactly. `_cmd_chunk_embed_cv(args)` builds the app engine, calls `chunk_and_embed_cv(app_engine, args.user_id, embed=..., embedding_model=settings.embedding_model, refresh=args.refresh)`, prints `f"chunk-embed-cv complete: chunks_written={n}"`; catch `LookupError` and print/return 1 like `_cmd_map_cv_skills` does for the same error.

- [ ] **Step 6: Lint and commit**

```bash
cd job_search && venv/bin/ruff check packages apps db && venv/bin/isort packages apps db && venv/bin/black packages apps db
git add packages/core/core/scoring/cv_chunking.py apps/pipeline/app/cli.py packages/core/tests/integration/test_scoring_cv_chunking.py
git commit -m "feat(job_search): CV chunking and embedding (Step 15 stage 2, CV side)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Vector similarity + cross-encoder rerank (stage 2b)

**Files:**
- Create: `packages/core/core/scoring/similarity.py`
- Modify: `apps/pipeline/app/cli.py` (add `score-similarity`)
- Modify: `requirements.txt` (add `sentence-transformers`)
- Test: `packages/core/tests/integration/test_scoring_similarity.py`

**Interfaces:**
- Consumes: `scoring.job_chunk_embedding`, `scoring.cv_chunk_embedding`, `scoring.job_score.hard_filter_passed`.
- Produces (used by Task 8): `run_similarity(engine, user_id, *, rerank, top_n=200) -> int` (jobs scored) in `similarity.py`, where `rerank` is an injected `Callable[[str, str], float]` (CV text, JD text) -> reranker score — tests inject a fake, the CLI injects a real `sentence_transformers.CrossEncoder`-backed function.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_scoring_similarity.py`:

```python
"""Integration tests for core.scoring.similarity against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.scoring.similarity import run_similarity
from core.settings import get_settings


def _vec(dim: int, value: float) -> str:
    return "[" + ",".join([str(value)] * 767 + [str(1.0)]) + "]" if dim == 0 else "[" + ",".join(["0.0"] * 768) + "]"


class TestSimilarity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.job_a = "fixture-job-sim-a"  # aligned with the CV
        self.job_b = "fixture-job-sim-b"  # embedded under a different model
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture sim user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            for job in (self.job_a, self.job_b):
                conn.execute(
                    text(
                        "INSERT INTO gold.dim_job (job_group_id, title, company) "
                        "VALUES (:j, 'zzfixture role', 'zzfixture co')"
                    ),
                    {"j": job},
                )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding (job_group_id, section, "
                    "chunk_index, chunk_text, embedding, embedding_model) VALUES "
                    "(:j, 'responsibilities', 0, 'zzfixture', "
                    "CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"j": self.job_a, "v": "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding (job_group_id, section, "
                    "chunk_index, chunk_text, embedding, embedding_model) VALUES "
                    "(:j, 'responsibilities', 0, 'zzfixture', "
                    "CAST(:v AS vector), 'a-different-model')"
                ),
                {"j": self.job_b, "v": "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, :j, true)"
                ),
                {"u": self.user_id, "j": self.job_a},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, :j, true)"
                ),
                {"u": self.user_id, "j": self.job_b},
            )
        with self.app_engine.begin() as conn:
            conn.execute(text("SET app.current_user_id = :u"), {"u": str(self.user_id)})
            conn.execute(
                text(
                    "INSERT INTO scoring.cv_chunk_embedding (user_id, cv_version, "
                    "section, chunk_index, chunk_text, embedding, embedding_model) "
                    "VALUES (:u, 1, 'experience', 0, 'zzfixture', "
                    "CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"u": self.user_id, "v": "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"},
            )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.cv_chunk_embedding WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id LIKE 'fixture-job-sim-%'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job WHERE job_group_id LIKE 'fixture-job-sim-%'"
                )
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id})

    def _fake_rerank(self, cv_text: str, jd_text: str) -> float:
        return 0.5

    def test_matching_embedding_model_gets_a_similarity_score(self) -> None:
        run_similarity(self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=200)
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT vector_similarity_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": self.job_a},
            ).scalar_one()
        self.assertIsNotNone(score)
        self.assertGreater(float(score), 0.9)

    def test_a_model_mismatch_is_skipped_not_compared(self) -> None:
        run_similarity(self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=200)
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT vector_similarity_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": self.job_b},
            ).scalar_one()
        self.assertIsNone(score)

    def test_an_unpaired_section_is_never_compared(self) -> None:
        # "benefits" has no CV-side counterpart in _SECTION_PAIRS, so a job
        # with ONLY a benefits chunk must get no vector_similarity_score at
        # all, even though a CV chunk exists.
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_chunk_embedding (job_group_id, "
                    "section, chunk_index, chunk_text, embedding, embedding_model) "
                    "VALUES ('fixture-job-sim-benefits-only', 'benefits', 0, "
                    "'zzfixture', CAST(:v AS vector), 'nomic-embed-text')"
                ),
                {"v": "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"},
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title, company) "
                    "VALUES ('fixture-job-sim-benefits-only', 'zzfixture role', "
                    "'zzfixture co')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, 'fixture-job-sim-benefits-only', "
                    "true)"
                ),
                {"u": self.user_id},
            )
        run_similarity(self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=200)
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT vector_similarity_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-sim-benefits-only'"
                ),
                {"u": self.user_id},
            ).scalar_one()
            conn.execute(
                text(
                    "DELETE FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-sim-benefits-only'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job "
                    "WHERE job_group_id = 'fixture-job-sim-benefits-only'"
                )
            )
        self.assertIsNone(score)

    def test_reranker_score_only_set_for_the_top_n(self) -> None:
        run_similarity(self.app_engine, self.user_id, rerank=self._fake_rerank, top_n=1)
        with self.owner.connect() as conn:
            reranked = conn.execute(
                text(
                    "SELECT job_group_id FROM scoring.job_score "
                    "WHERE user_id = :u AND reranker_score IS NOT NULL"
                ),
                {"u": self.user_id},
            ).scalars().all()
        self.assertEqual(reranked, [self.job_a])


if __name__ == "__main__":
    unittest.main()
```

Delete the unused `_vec` helper above before committing — it was scaffolding for an earlier draft of this test and is not called; leaving dead code in a test file is exactly the kind of thing the task review will flag.

- [ ] **Step 2: Run to verify it fails**

Run: `set -a; source .env; set +a; export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}" APP_DATABASE_URL="${APP_DATABASE_URL/@postgres:/@localhost:}"; cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_similarity -v`
Expected: ImportError.

- [ ] **Step 3: Add the dependency**

In `requirements.txt`, add `sentence-transformers==3.*` (pin an exact version current at implementation time, matching this repo's exact-pin convention). This is a **pipeline-only** dependency — confirm `requirements.txt` is the file `apps/pipeline/Dockerfile` installs (it is, per every other pipeline dependency in this repo) and that `apps/api/Dockerfile`/`apps/ui/Dockerfile` do NOT also install it from the same file without a good reason; if they do install the same `requirements.txt` verbatim (check both Dockerfiles), flag this in the task report as DONE_WITH_CONCERNS rather than silently bloating the api/ui images — the spec requires pipeline-only.

- [ ] **Step 4: Implement `similarity.py`**

```python
"""Stage 2 continued: vector similarity + cross-encoder rerank (PLAN.md
Step 15).

Guards against comparing chunks embedded under different embedding_model
values (mirrors core.skills.mapper.EmbeddingModelMismatch) — a stale job
vector compared against a CV re-embedded under a new model would produce a
confident-looking, meaningless number.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import Engine, text

from core.db.session import session_scope

# Only these section pairs are compared — "compare like sections only"
# (DECISIONS.md §4). A CV section not listed here (education, certifications,
# projects) is never compared against any JD section.
_SECTION_PAIRS = {
    "experience": "responsibilities",
    "skills": "requirements",
    "summary": "company_blurb",
}

_SELECT_CV_CHUNKS = text(
    "SELECT section, embedding, embedding_model FROM scoring.cv_chunk_embedding "
    "WHERE user_id = :user_id"
)
_SELECT_JOB_CHUNKS = text(
    "SELECT job_group_id, section, embedding, embedding_model, chunk_text "
    "FROM scoring.job_chunk_embedding"
)
_SELECT_CANDIDATE_JOBS = text(
    "SELECT job_group_id FROM scoring.job_score "
    "WHERE user_id = :user_id AND hard_filter_passed = true"
)
_UPDATE_VECTOR_SCORE = text(
    "UPDATE scoring.job_score SET vector_similarity_score = :score, "
    "embedding_model = :embedding_model "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)
_UPDATE_RERANK_SCORE = text(
    "UPDATE scoring.job_score SET reranker_score = :score "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        The cosine similarity, in [-1, 1].
    """
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def run_similarity(
    app_engine: Engine,
    user_id: uuid.UUID,
    *,
    rerank: Callable[[str, str], float],
    top_n: int = 200,
) -> int:
    """Score vector similarity for every hard-filter-passing job, then
    rerank the top `top_n` with a cross-encoder.

    Args:
        app_engine: The app-role engine (RLS-enforced for the per-user
            reads/writes; job_chunk_embedding itself has no RLS).
        user_id: Whose CV and scores to use.
        rerank: Given (CV text, JD text), returns a cross-encoder score.
            Injected so tests never load a real model.
        top_n: How many top-scoring jobs get a reranker score.

    Returns:
        The number of jobs given a `vector_similarity_score` (including
        those skipped for a model mismatch, which get no score but are
        still "considered").
    """
    with session_scope(app_engine, user_id=user_id) as conn:
        cv_chunks = conn.execute(_SELECT_CV_CHUNKS, {"user_id": user_id}).mappings().all()
        candidate_jobs = {
            r.job_group_id
            for r in conn.execute(_SELECT_CANDIDATE_JOBS, {"user_id": user_id})
        }
    with app_engine.connect() as conn:
        job_chunks = conn.execute(_SELECT_JOB_CHUNKS).mappings().all()

    cv_by_section: dict[str, list[dict]] = {}
    for chunk in cv_chunks:
        cv_by_section.setdefault(chunk["section"], []).append(chunk)

    jobs: dict[str, list[dict]] = {}
    for chunk in job_chunks:
        if chunk["job_group_id"] in candidate_jobs:
            jobs.setdefault(chunk["job_group_id"], []).append(chunk)

    results: dict[str, float | None] = {}
    embedding_model_used: dict[str, str] = {}
    for job_group_id, chunks in jobs.items():
        pair_scores: list[float] = []
        mismatch = False
        for cv_section, job_section in _SECTION_PAIRS.items():
            cv_side = cv_by_section.get(cv_section, [])
            job_side = [c for c in chunks if c["section"] == job_section]
            if not cv_side or not job_side:
                continue
            best = 0.0
            for cv_chunk in cv_side:
                for job_chunk in job_side:
                    if cv_chunk["embedding_model"] != job_chunk["embedding_model"]:
                        mismatch = True
                        continue
                    embedding_model_used[job_group_id] = cv_chunk["embedding_model"]
                    score = _cosine(
                        list(cv_chunk["embedding"]), list(job_chunk["embedding"])
                    )
                    best = max(best, score)
            if best:
                pair_scores.append(best)
        results[job_group_id] = None if (mismatch and not pair_scores) else (
            sum(pair_scores) / len(pair_scores) if pair_scores else None
        )

    with session_scope(app_engine, user_id=user_id) as conn:
        for job_group_id, score in results.items():
            conn.execute(
                _UPDATE_VECTOR_SCORE,
                {
                    "user_id": user_id,
                    "job_group_id": job_group_id,
                    "score": score,
                    "embedding_model": embedding_model_used.get(job_group_id),
                },
            )

    ranked = sorted(
        (j for j, s in results.items() if s is not None),
        key=lambda j: results[j],
        reverse=True,
    )[:top_n]
    job_text_by_id = {
        job_group_id: " ".join(c["chunk_text"] for c in chunks)
        for job_group_id, chunks in jobs.items()
    }
    cv_text = " ".join(c["chunk_text"] for c in cv_chunks)
    with session_scope(app_engine, user_id=user_id) as conn:
        for job_group_id in ranked:
            conn.execute(
                _UPDATE_RERANK_SCORE,
                {
                    "user_id": user_id,
                    "job_group_id": job_group_id,
                    "score": rerank(cv_text, job_text_by_id[job_group_id]),
                },
            )
    return len(jobs)
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_similarity -v`
Expected: all PASS.

- [ ] **Step 6: CLI wiring**

In `apps/pipeline/app/cli.py`, add `score-similarity --user-id <id> [--top-n N]`. Build the real reranker function:

```python
def _build_reranker() -> Callable[[str, str], float]:
    """Build the cross-encoder reranker function for score-similarity.

    Returns:
        A function taking (cv_text, jd_text) and returning a relevance
        score from the local BAAI/bge-reranker-base model.
    """
    from sentence_transformers import CrossEncoder

    model = CrossEncoder("BAAI/bge-reranker-base")

    def rerank(cv_text: str, jd_text: str) -> float:
        return float(model.predict([(cv_text, jd_text)])[0])

    return rerank
```

`_cmd_score_similarity(args)` builds the app engine, calls
`run_similarity(app_engine, args.user_id, rerank=_build_reranker(), top_n=args.top_n)`,
prints `f"score-similarity complete: jobs_scored={n}"`. Loading the
cross-encoder model downloads it on first use (network + disk); do not call
`_build_reranker()` until the command actually runs (lazy import already
handles this — `sentence_transformers` is imported inside the function, not
at module load, so `apps/pipeline/app/cli.py` stays importable even before
the dependency is installed in a dev venv that hasn't rebuilt yet).

- [ ] **Step 7: Lint and commit**

```bash
cd job_search && venv/bin/ruff check packages apps db && venv/bin/isort packages apps db && venv/bin/black packages apps db
git add packages/core/core/scoring/similarity.py apps/pipeline/app/cli.py requirements.txt packages/core/tests/integration/test_scoring_similarity.py
git commit -m "feat(job_search): vector similarity and cross-encoder rerank (Step 15 stage 2b)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Skill coverage (stage 3)

**Files:**
- Create: `packages/core/core/scoring/skill_coverage.py`
- Modify: `apps/pipeline/app/cli.py` (add `score-skill-coverage`)
- Test: `packages/core/tests/integration/test_scoring_skill_coverage.py`

**Interfaces:**
- Consumes: `silver.bridge_job_skill` (via dbt-built view/table — read directly with SQL, same as other silver reads elsewhere in `core`), the CV truth base's `skills` (canonical_id, last_used).
- Produces (used by Task 8): `run_skill_coverage(app_engine, user_id, *, as_of=None) -> int` (jobs scored) in `skill_coverage.py`. `as_of` is an injectable "today" for deterministic recency-decay tests (default `date.today()`).

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_scoring_skill_coverage.py`:

```python
"""Integration tests for core.scoring.skill_coverage against live Postgres."""

from __future__ import annotations

import datetime
import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.cv.schema import CVTruthBase, Skill
from core.cv.store import write_truth_base
from core.db.session import build_engine
from core.scoring.skill_coverage import run_skill_coverage
from core.settings import get_settings


class TestSkillCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.job_must = "fixture-job-cov-must"
        self.job_nice = "fixture-job-cov-nice"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture coverage user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            for job in (self.job_must, self.job_nice):
                conn.execute(
                    text(
                        "INSERT INTO gold.dim_job (job_group_id, title, company) "
                        "VALUES (:j, 'zzfixture role', 'zzfixture co')"
                    ),
                    {"j": job},
                )
                conn.execute(
                    text(
                        "INSERT INTO scoring.job_score (user_id, job_group_id, "
                        "hard_filter_passed) VALUES (:u, :j, true)"
                    ),
                    {"u": self.user_id, "j": job},
                )
            conn.execute(
                text(
                    "INSERT INTO silver.bridge_job_skill (job_group_id, skill_id, "
                    "requirement_level, mention_count) VALUES "
                    "(:j, 'fixture-python', 'must_have', 1)"
                ),
                {"j": self.job_must},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.bridge_job_skill (job_group_id, skill_id, "
                    "requirement_level, mention_count) VALUES "
                    "(:j, 'fixture-python', 'nice_to_have', 1)"
                ),
                {"j": self.job_nice},
            )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.bridge_job_skill "
                    "WHERE job_group_id LIKE 'fixture-job-cov-%'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job WHERE job_group_id LIKE 'fixture-job-cov-%'"
                )
            )
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :id"), {"id": self.user_id}
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id})

    def _write_cv(self, last_used: str | None) -> None:
        truth_base = CVTruthBase(
            identity="zzfixture Person",
            headline="Engineer",
            skills=[
                Skill(name="Python", canonical_id="fixture-python", last_used=last_used)
            ],
        )
        write_truth_base(
            self.app_engine, self.user_id, "zzfixture markdown", truth_base, label="f"
        )

    def _coverage(self, job_group_id: str) -> float:
        with self.owner.connect() as conn:
            return float(
                conn.execute(
                    text(
                        "SELECT skill_coverage_score FROM scoring.job_score "
                        "WHERE user_id = :u AND job_group_id = :j"
                    ),
                    {"u": self.user_id, "j": job_group_id},
                ).scalar_one()
            )

    def test_a_must_have_hit_scores_higher_than_an_equivalent_nice_to_have(
        self,
    ) -> None:
        self._write_cv(last_used="2026-06")
        run_skill_coverage(
            self.app_engine, self.user_id, as_of=datetime.date(2026, 9, 27)
        )
        self.assertGreater(self._coverage(self.job_must), self._coverage(self.job_nice))

    def test_a_skill_unused_for_six_years_scores_lower_than_one_used_last_year(
        self,
    ) -> None:
        self._write_cv(last_used="2020-01")
        run_skill_coverage(
            self.app_engine, self.user_id, as_of=datetime.date(2026, 9, 27)
        )
        stale_score = self._coverage(self.job_must)
        self._write_cv(last_used="2025-06")
        run_skill_coverage(
            self.app_engine, self.user_id, as_of=datetime.date(2026, 9, 27)
        )
        fresh_score = self._coverage(self.job_must)
        self.assertLess(stale_score, fresh_score)

    def test_a_job_with_no_bridge_rows_stays_null_not_zero(self) -> None:
        # A job scoring.job_score row with hard_filter_passed=true but no
        # matching silver.bridge_job_skill rows (e.g. extraction hasn't run
        # for it yet) has no data to score coverage from — that is "unknown,"
        # not "zero coverage," so it must be excluded from the blend later
        # (core.scoring.blend), not scored as a fit failure.
        no_skills_job = "fixture-job-cov-no-skills"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title, company) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co')"
                ),
                {"j": no_skills_job},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed) VALUES (:u, :j, true)"
                ),
                {"u": self.user_id, "j": no_skills_job},
            )
        self._write_cv(last_used="2026-06")
        run_skill_coverage(
            self.app_engine, self.user_id, as_of=datetime.date(2026, 9, 27)
        )
        with self.owner.connect() as conn:
            score = conn.execute(
                text(
                    "SELECT skill_coverage_score FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = :j"
                ),
                {"u": self.user_id, "j": no_skills_job},
            ).scalar_one()
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = :j"),
                {"j": no_skills_job},
            )
        self.assertIsNone(score)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_skill_coverage -v`
Expected: ImportError.

- [ ] **Step 3: Implement `skill_coverage.py`**

```python
"""Stage 3 of the scoring funnel: skill coverage (PLAN.md Step 15).

Independent of stages 2/2b — only needs Step 14's silver.bridge_job_skill
and the CV truth base's skills. A must-have hit counts more than a
nice-to-have hit; a skill's contribution decays with how long since it was
last used, since a skill last touched a decade ago is not the same asset as
one used last quarter.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Engine, text

from core.cv.store import read_truth_base
from core.db.session import session_scope

_MUST_HAVE_WEIGHT = 2.0
_NICE_TO_HAVE_WEIGHT = 1.0
_DECAY_START_YEARS = 5.0
"""Years of disuse after which a skill's contribution starts decaying."""
_DECAY_FLOOR_YEARS = 10.0
"""Years of disuse at which a skill's contribution reaches zero."""

_SELECT_CANDIDATE_JOBS = text(
    "SELECT job_group_id FROM scoring.job_score "
    "WHERE user_id = :user_id AND hard_filter_passed = true"
)
_SELECT_JOB_SKILLS = text(
    "SELECT skill_id, requirement_level FROM silver.bridge_job_skill "
    "WHERE job_group_id = :job_group_id"
)
_UPDATE_COVERAGE = text(
    "UPDATE scoring.job_score SET skill_coverage_score = :score "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)


def _recency_weight(last_used: str | None, as_of: datetime.date) -> float:
    """How much a skill counts, based on how recently it was used.

    Args:
        last_used: "YYYY-MM", or None if unstated (treated as fully current
            — a CV that never states dates should not be penalised for it).
        as_of: The date to measure recency against.

    Returns:
        1.0 for a skill used within `_DECAY_START_YEARS`, linearly falling
        to 0.0 at `_DECAY_FLOOR_YEARS`, 1.0 if `last_used` is unstated.
    """
    if last_used is None:
        return 1.0
    year, month = (int(p) for p in last_used.split("-"))
    used_date = datetime.date(year, month, 1)
    years_since = (as_of - used_date).days / 365.25
    if years_since <= _DECAY_START_YEARS:
        return 1.0
    if years_since >= _DECAY_FLOOR_YEARS:
        return 0.0
    span = _DECAY_FLOOR_YEARS - _DECAY_START_YEARS
    return 1.0 - (years_since - _DECAY_START_YEARS) / span


def run_skill_coverage(
    app_engine: Engine,
    user_id: uuid.UUID,
    *,
    as_of: datetime.date | None = None,
) -> int:
    """Score skill coverage for every hard-filter-passing job.

    Args:
        app_engine: The app-role engine (RLS-enforced for the CV read and
            the per-user score write; `bridge_job_skill` itself is shared).
        user_id: Whose CV skills to match against.
        as_of: The date to measure recency decay against; defaults to today.
            Injectable so tests are deterministic.

    Returns:
        The number of jobs scored.

    Raises:
        LookupError: If the user has no CV truth base.
    """
    as_of = as_of or datetime.date.today()
    stored = read_truth_base(app_engine, user_id)
    if stored is None:
        raise LookupError(f"user {user_id} has no CV truth base")
    cv_skills = {
        s.canonical_id: _recency_weight(s.last_used, as_of)
        for s in stored.truth_base.skills
        if s.canonical_id is not None
    }
    with session_scope(app_engine, user_id=user_id) as conn:
        job_ids = [
            r.job_group_id
            for r in conn.execute(_SELECT_CANDIDATE_JOBS, {"user_id": user_id})
        ]
    scored = 0
    with app_engine.connect() as conn:
        job_skills_by_job = {
            job_group_id: conn.execute(
                _SELECT_JOB_SKILLS, {"job_group_id": job_group_id}
            ).mappings().all()
            for job_group_id in job_ids
        }
    with session_scope(app_engine, user_id=user_id) as conn:
        for job_group_id, job_skills in job_skills_by_job.items():
            if not job_skills:
                continue
            total = hit = 0.0
            for row in job_skills:
                weight = (
                    _MUST_HAVE_WEIGHT
                    if row["requirement_level"] == "must_have"
                    else _NICE_TO_HAVE_WEIGHT
                )
                total += weight
                if row["skill_id"] in cv_skills:
                    hit += weight * cv_skills[row["skill_id"]]
            conn.execute(
                _UPDATE_COVERAGE,
                {
                    "user_id": user_id,
                    "job_group_id": job_group_id,
                    "score": hit / total,
                },
            )
            scored += 1
    return scored
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_skill_coverage -v`
Expected: all PASS.

- [ ] **Step 5: CLI wiring**

In `apps/pipeline/app/cli.py`, add `score-skill-coverage --user-id <id>`. `_cmd_score_skill_coverage(args)` builds the app engine, calls `run_skill_coverage(app_engine, args.user_id)`, prints `f"score-skill-coverage complete: jobs_scored={n}"`, catches `LookupError` like the CV-touching commands do.

- [ ] **Step 6: Lint and commit**

```bash
cd job_search && venv/bin/ruff check packages apps db && venv/bin/isort packages apps db && venv/bin/black packages apps db
git add packages/core/core/scoring/skill_coverage.py apps/pipeline/app/cli.py packages/core/tests/integration/test_scoring_skill_coverage.py
git commit -m "feat(job_search): skill coverage scoring with recency decay (Step 15 stage 3)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: LLM re-rank (stage 4) + final blend + dbt mart

**Files:**
- Create: `prompts/job_scoring/claude.v1.md`
- Modify: `config/llm_tasks.yml` (add `job_scoring`)
- Create: `packages/core/core/scoring/llm_rerank.py`
- Create: `packages/core/core/scoring/blend.py`
- Create: `dbt/models/gold/fct_job_score.sql`
- Modify: `dbt/models/gold/_gold.yml` (document the new mart)
- Modify: `apps/pipeline/app/cli.py` (add `score-llm-rerank`, `score-blend`)
- Test: `packages/core/tests/integration/test_scoring_llm_rerank.py`
- Test: `packages/core/tests/integration/test_scoring_blend.py`

**Interfaces:**
- Consumes: `core.llm.gateway.complete`, `scoring.weight`, `scoring.job_score`'s existing components.
- Produces: `run_llm_rerank(engine, user_id, *, adapters, config_path=None) -> int` (jobs re-ranked, capped at 50) in `llm_rerank.py`; `compute_final_scores(engine, user_id) -> int` (jobs blended) in `blend.py`.

- [ ] **Step 1: Write the failing tests**

Create `prompts/job_scoring/claude.v1.md`:

```
You assess how well a candidate's CV fits a job description.

CV:
{cv_text}

Job description:
{jd_text}

Rate the fit from 0 to 100, explain briefly, list any specific skills the job
asks for that the CV does not show, and flag whether this role is a stretch
(more senior or different in kind from the CV's demonstrated experience).

Respond with ONLY a JSON object, no other text:
{{"fit_score": <0-100>, "rationale": "<one or two sentences>", "missing_skills": [<string>, ...], "stretch_flag": <true or false>}}
```

Add to `config/llm_tasks.yml`:

```yaml
  job_scoring:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
```

Create `packages/core/tests/integration/test_scoring_llm_rerank.py`:

```python
"""Integration tests for core.scoring.llm_rerank against live Postgres.

Only the Anthropic adapter is faked.
"""

from __future__ import annotations

import json
import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.llm.types import LLMResponse
from core.scoring.llm_rerank import run_llm_rerank
from core.settings import get_settings


class _FakeAdapter:
    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        return LLMResponse(
            text=json.dumps(
                {
                    "fit_score": 80,
                    "rationale": "zzfixture rationale",
                    "missing_skills": ["Kubernetes"],
                    "stretch_flag": False,
                }
            ),
            provider="anthropic",
            model=model,
            input_tokens=10,
            output_tokens=10,
        )


class TestLlmRerank(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture rerank user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            for i in range(3):
                job = f"fixture-job-rerank-{i}"
                conn.execute(
                    text(
                        "INSERT INTO gold.dim_job (job_group_id, title, company) "
                        "VALUES (:j, 'zzfixture role', 'zzfixture co')"
                    ),
                    {"j": job},
                )
                conn.execute(
                    text(
                        "INSERT INTO scoring.job_score (user_id, job_group_id, "
                        "hard_filter_passed, vector_similarity_score) "
                        "VALUES (:u, :j, true, :s)"
                    ),
                    {"u": self.user_id, "j": job, "s": 0.9 - i * 0.1},
                )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job WHERE job_group_id LIKE 'fixture-job-rerank-%'"
                )
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id})

    def test_top_jobs_get_an_llm_fit_score(self) -> None:
        run_llm_rerank(self.app_engine, self.user_id, adapters={"anthropic": _FakeAdapter()})
        with self.owner.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT llm_fit_score, llm_rationale, llm_missing_skills, "
                    "llm_stretch_flag FROM scoring.job_score "
                    "WHERE user_id = :u AND job_group_id = 'fixture-job-rerank-0'"
                ),
                {"u": self.user_id},
            ).one()
        self.assertEqual(int(row.llm_fit_score), 80)
        self.assertEqual(list(row.llm_missing_skills), ["Kubernetes"])
        self.assertFalse(row.llm_stretch_flag)

    def test_never_sends_more_than_top_n_jobs(self) -> None:
        # 3 fixture jobs exist; capping top_n at 2 must leave exactly one
        # (the lowest-scoring) untouched, regardless of the real 50 cap.
        run_llm_rerank(
            self.app_engine,
            self.user_id,
            adapters={"anthropic": _FakeAdapter()},
            top_n=2,
        )
        with self.owner.connect() as conn:
            scored = conn.execute(
                text(
                    "SELECT job_group_id FROM scoring.job_score "
                    "WHERE user_id = :u AND llm_fit_score IS NOT NULL "
                    "ORDER BY job_group_id"
                ),
                {"u": self.user_id},
            ).scalars().all()
        self.assertEqual(scored, ["fixture-job-rerank-0", "fixture-job-rerank-1"])


if __name__ == "__main__":
    unittest.main()
```

Create `packages/core/tests/integration/test_scoring_blend.py`:

```python
"""Integration tests for core.scoring.blend against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.scoring.blend import compute_final_scores
from core.settings import get_settings


class TestBlend(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.job_id = "fixture-job-blend-1"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture blend user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title, company) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co')"
                ),
                {"j": self.job_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, "
                    "skill_coverage_score) VALUES (:u, :j, true, 0.8, 0.6)"
                ),
                {"u": self.user_id, "j": self.job_id},
            )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.weight WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job WHERE job_group_id LIKE 'fixture-job-blend-%'"
                )
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id})

    def _final(self) -> float:
        with self.owner.connect() as conn:
            return float(
                conn.execute(
                    text(
                        "SELECT final_score FROM scoring.job_score "
                        "WHERE user_id = :u AND job_group_id = :j"
                    ),
                    {"u": self.user_id, "j": self.job_id},
                ).scalar_one()
            )

    def test_missing_components_are_excluded_not_treated_as_zero(self) -> None:
        compute_final_scores(self.app_engine, self.user_id)
        # Equal weight over the two present components: (0.8 + 0.6) / 2 = 0.7,
        # not (0.8 + 0.6 + 0 + 0) / 4 = 0.35.
        self.assertAlmostEqual(self._final(), 0.7, places=4)

    def test_a_fitted_weight_overrides_the_default(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.weight (user_id, component, weight) "
                    "VALUES (:u, 'vector_similarity', 0.9), "
                    "(:u, 'skill_coverage', 0.1)"
                ),
                {"u": self.user_id},
            )
        compute_final_scores(self.app_engine, self.user_id)
        self.assertAlmostEqual(self._final(), 0.8 * 0.9 + 0.6 * 0.1, places=4)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify both fail**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_llm_rerank tests.integration.test_scoring_blend -v`
Expected: ImportError.

- [ ] **Step 3: Implement `llm_rerank.py`**

```python
"""Stage 4 of the scoring funnel: LLM re-rank of the top 50 (PLAN.md Step
15). Never sends more than 50 jobs per user per run — the plan's own
"Done when" criterion.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.llm import gateway
from core.llm.json_response import parse_json_response
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter

TASK = "job_scoring"
PROMPT_VERSION = "claude.v1"
TOP_N = 50

_SELECT_PRE_LLM_TOP = text(
    "SELECT job_group_id, "
    "COALESCE(vector_similarity_score, 0) + COALESCE(reranker_score, 0) "
    "+ COALESCE(skill_coverage_score, 0) AS pre_llm_score "
    "FROM scoring.job_score WHERE user_id = :user_id AND hard_filter_passed = true "
    "ORDER BY pre_llm_score DESC LIMIT :top_n"
)
_SELECT_JOB_TEXT = text(
    "SELECT string_agg(chunk_text, ' ') AS jd_text FROM scoring.job_chunk_embedding "
    "WHERE job_group_id = :job_group_id"
)
_SELECT_CV_TEXT = text(
    "SELECT string_agg(chunk_text, ' ') AS cv_text FROM scoring.cv_chunk_embedding "
    "WHERE user_id = :user_id"
)
_UPDATE_LLM_FIELDS = text(
    "UPDATE scoring.job_score SET llm_fit_score = :fit_score, "
    "llm_rationale = :rationale, llm_missing_skills = :missing_skills, "
    "llm_stretch_flag = :stretch_flag "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)


def run_llm_rerank(
    app_engine: Engine,
    user_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    top_n: int = TOP_N,
    config_path: Path | None = None,
) -> int:
    """Run the LLM re-rank stage for one user's top-scoring jobs.

    Args:
        app_engine: The app-role engine (RLS-enforced).
        user_id: Whose top jobs to re-rank.
        adapters: LLM adapters keyed by provider; must include the task's
            provider ("anthropic").
        top_n: Send at most this many jobs. Defaults to `TOP_N` (50, the
            plan's own cap); tests pass a smaller value so a fixture with a
            handful of jobs can prove the cap is respected without inserting
            50+ rows.
        config_path: Task-config override (tests).

    Returns:
        The number of jobs successfully re-ranked (a parse/API failure on
        one job is skipped, not fatal to the rest — same retry-safe pattern
        as core.skills.llm_map).
    """
    template = load_prompt(TASK, load_task_config(TASK, config_path).prompt_family, 1)
    with session_scope(app_engine, user_id=user_id) as conn:
        top_jobs = conn.execute(
            _SELECT_PRE_LLM_TOP, {"user_id": user_id, "top_n": top_n}
        ).all()
        cv_text = conn.execute(_SELECT_CV_TEXT, {"user_id": user_id}).scalar_one_or_none() or ""
    reranked = 0
    for row in top_jobs:
        with app_engine.connect() as conn:
            jd_text = conn.execute(
                _SELECT_JOB_TEXT, {"job_group_id": row.job_group_id}
            ).scalar_one_or_none() or ""
        prompt = template.format(cv_text=cv_text, jd_text=jd_text)
        try:
            response = gateway.complete(
                TASK, prompt, prompt_version=PROMPT_VERSION, adapters=adapters,
                config_path=config_path,
            )
            data = parse_json_response(response.text.strip())
            fit_score = int(data["fit_score"])
            missing_skills = list(data["missing_skills"])
            stretch_flag = bool(data["stretch_flag"])
            rationale = str(data["rationale"])[:500]
        except Exception:  # noqa: BLE001 — a bad reply skips this job, not the run
            continue
        with session_scope(app_engine, user_id=user_id) as conn:
            conn.execute(
                _UPDATE_LLM_FIELDS,
                {
                    "user_id": user_id,
                    "job_group_id": row.job_group_id,
                    "fit_score": fit_score,
                    "rationale": rationale,
                    "missing_skills": missing_skills,
                    "stretch_flag": stretch_flag,
                },
            )
        reranked += 1
    return reranked
```

- [ ] **Step 4: Implement `blend.py`**

```python
"""Final stage of the scoring funnel: config-driven weighted blend
(PLAN.md Step 15). Before scoring.weight has a row for a user (before Step
16 calibrates), every present component is weighted equally — an
uncalibrated score is still computed, never left NULL, because Step 16
exists to fix the weights, not to unhide the score.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, text

from core.db.session import session_scope

_COMPONENTS = ("vector_similarity", "reranker", "skill_coverage", "llm_fit")
_COLUMN_BY_COMPONENT = {
    "vector_similarity": "vector_similarity_score",
    "reranker": "reranker_score",
    "skill_coverage": "skill_coverage_score",
    "llm_fit": "llm_fit_score",
}

_SELECT_WEIGHTS = text(
    "SELECT component, weight FROM scoring.weight WHERE user_id = :user_id"
)
_SELECT_SCORES = text(
    "SELECT job_group_id, vector_similarity_score, reranker_score, "
    "skill_coverage_score, llm_fit_score FROM scoring.job_score "
    "WHERE user_id = :user_id AND hard_filter_passed = true"
)
_UPDATE_FINAL = text(
    "UPDATE scoring.job_score SET final_score = :final_score "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)


def compute_final_scores(app_engine: Engine, user_id: uuid.UUID) -> int:
    """Blend each hard-filter-passing job's present components into
    `final_score`.

    Args:
        app_engine: The app-role engine (RLS-enforced).
        user_id: Whose scores to blend.

    Returns:
        The number of jobs blended.
    """
    with session_scope(app_engine, user_id=user_id) as conn:
        fitted_weights = {
            r.component: float(r.weight)
            for r in conn.execute(_SELECT_WEIGHTS, {"user_id": user_id})
        }
        rows = conn.execute(_SELECT_SCORES, {"user_id": user_id}).mappings().all()
    blended = 0
    with session_scope(app_engine, user_id=user_id) as conn:
        for row in rows:
            present = {
                component: float(row[_COLUMN_BY_COMPONENT[component]])
                for component in _COMPONENTS
                if row[_COLUMN_BY_COMPONENT[component]] is not None
            }
            if not present:
                continue
            weights = {
                component: fitted_weights.get(component, 1.0 / len(present))
                for component in present
            }
            weight_sum = sum(weights.values())
            final = sum(present[c] * weights[c] for c in present) / weight_sum
            conn.execute(
                _UPDATE_FINAL,
                {"user_id": user_id, "job_group_id": row["job_group_id"], "final_score": final},
            )
            blended += 1
    return blended
```

- [ ] **Step 5: Run to verify both pass**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_llm_rerank tests.integration.test_scoring_blend -v`
Expected: all PASS.

- [ ] **Step 6: `fct_job_score` dbt mart**

Read `dbt/models/gold/dim_job.sql`'s header comment style and `_gold.yml`'s existing model-documentation format first, then create `dbt/models/gold/fct_job_score.sql`:

```sql
-- fct_job_score: one row per (user_id, job_group_id) — the shared job pool,
-- scored independently per user (PLAN.md Step 15). Thin pass-through over
-- scoring.job_score, enriched with dim_job's display fields so a caller
-- doesn't need a second join for every consumer.
-- Grain: (user_id, job_group_id) (unique).

SELECT
    s.user_id,
    s.job_group_id,
    j.title,
    j.company,
    s.hard_filter_passed,
    s.vector_similarity_score,
    s.reranker_score,
    s.skill_coverage_score,
    s.llm_fit_score,
    s.llm_rationale,
    s.llm_missing_skills,
    s.llm_stretch_flag,
    s.final_score,
    s.scored_at
FROM {{ source('scoring', 'job_score') }} AS s
INNER JOIN {{ ref('dim_job') }} AS j USING (job_group_id)
```

Add a `scoring` source block to whichever `sources.yml` file defines the `silver_ingest`/`dedup` sources already (find it with `grep -rln "source_name: dedup\|name: dedup" dbt/models`), following the exact same shape, naming the source `scoring` and the table `job_score`. Add `fct_job_score` to `_gold.yml` with column descriptions and a `unique`/`not_null` test on a `dbt_utils.generate_surrogate_key(['user_id', 'job_group_id'])` column or a combined test, matching how other multi-column-grain gold models in this file declare their uniqueness test — copy that exact pattern rather than inventing a new one.

- [ ] **Step 7: CLI wiring for stages 4 and blend**

In `apps/pipeline/app/cli.py`, add `score-llm-rerank --user-id <id>` (checks `settings.anthropic_api_key`, prints a clear message and returns 1 if unset, same as `llm-map-skills`) and `score-blend --user-id <id>`. Each follows the established `_cmd_*` shape and prints a one-line summary.

- [ ] **Step 8: Lint, verify, commit**

```bash
cd job_search
venv/bin/ruff check packages apps db && venv/bin/isort packages apps db && venv/bin/black packages apps db
set -a; source .env; set +a; export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}" APP_DATABASE_URL="${APP_DATABASE_URL/@postgres:/@localhost:}"
cd packages/core && ../../venv/bin/python -m unittest tests.integration.test_scoring_llm_rerank tests.integration.test_scoring_blend -v
cd .. && docker compose run --rm dbt run --select fct_job_score
```

```bash
git add prompts/job_scoring config/llm_tasks.yml packages/core/core/scoring/llm_rerank.py packages/core/core/scoring/blend.py dbt/models/gold/fct_job_score.sql dbt/models/gold/_gold.yml dbt/models/staging/sources.yml apps/pipeline/app/cli.py packages/core/tests/integration/test_scoring_llm_rerank.py packages/core/tests/integration/test_scoring_blend.py
git commit -m "feat(job_search): LLM re-rank, final blend, fct_job_score mart (Step 15 stage 4)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

(Adjust the `sources.yml` path in the `git add` above to whichever file Step 7 actually edited.)

---

### Task 8: README + docs

**Files:**
- Modify: `README.md`
- Modify: `PLAN.md` (mark Step 15 built, if this repo's convention marks steps done inline — check for a precedent, e.g. how Step 14 or Step 13's section reads now vs. before it was built; follow whatever marker convention exists, or add none if there isn't one)

**Interfaces:** none (docs only).

- [ ] **Step 1: Add a README section**

Add a "Scoring the job pool (Step 15)" section to `README.md`, modeled on the "Claude pre-review of unmapped skills" section: what each CLI command does, the order to run them in (`score-filter-jobs` → `chunk-embed-jobs`/`chunk-embed-cv` → `score-similarity` → `score-skill-coverage` → `score-llm-rerank` → `score-blend`), that the settings page needs Step 22a's auth to work in a browser today, and where to read the scored results (`fct_job_score`, or `scoring.job_score` directly before a dbt run).

- [ ] **Step 2: Commit**

```bash
cd job_search
git add README.md
git commit -m "docs(job_search): document the Step 15 scoring funnel commands

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
