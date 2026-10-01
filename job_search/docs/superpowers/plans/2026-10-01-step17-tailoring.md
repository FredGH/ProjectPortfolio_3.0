# Step 17 — Tailored CV Generation with the Fabrication Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given one user and one `job_group_id`, produce a tailored CV whose every generated line traces to the user's CV truth base, with anything untraceable surfaced for an explicit decision in a review UI.

**Architecture:** Code assembles the document from the truth base; the Tailor LLM only returns per-bullet instructions (`text` + `evidence_refs`), so roles, titles and dates never pass through the model. Code-only checks and a Claude-only critic verify the result inside a tailor→assemble→check→critic loop (at most two retries). Unresolved bullets are persisted as `orphan_bullet` rows; the user links each to an existing truth-base bullet or rejects it. Persistence is two RLS tables in a new `tailoring` schema; access is via a `/tailoring` FastAPI router, a `tailor-cv` CLI command and a Streamlit review page.

**Tech Stack:** Python 3.11, pydantic v2, SQLAlchemy 2 (psycopg3), Alembic, FastAPI, Streamlit 1.39, unittest. LLM via `core.llm.gateway.complete` (task `cv_tailoring` → Ollama in dev; task `fabrication_critic` → Anthropic always).

**Spec:** `job_search/docs/superpowers/specs/2026-10-01-step17-tailoring-design.md`

## Global Constraints

- Python 3.11, `from __future__ import annotations`, 88-column lines, Google-style docstrings (with `Args`/`Returns`/`Raises`) on every function and class, type hints on every signature (`.claude/rules/python-style.md`).
- Tests: `unittest`, one test file per module, no mocking the database (real Postgres; only LLM adapters are faked), each test independent, fixture rows prefixed `zzfixture` and always cleaned up in `tearDown` (`.claude/rules/python-testing.md`).
- **Never** run unscoped destructive SQL against the shared dev DB: every `DELETE` in a test is scoped to `zzfixture` ids or to the fixture user's id. Never run `isort .` or `black .` on the whole repo — format only the files you touch.
- The `fabrication_critic` task must resolve to provider `anthropic`; `core.tailoring.critic` raises if it does not, and a test asserts it against the real `config/llm_tasks.yml`.
- `title_for_display` is injected as `target_title`/`headline`; it is never generated, paraphrased or "adapted". No experience-section company, title or date ever differs from the truth base.
- The truth base is never written by this feature.
- Everything is keyed on `job_group_id`, never a source posting.
- Both new tables have RLS keyed on `app.current_user_id` (same pattern as `scoring.job_label`, migration 0029).
- The new UI page calls `apply_theme()` right after `st.set_page_config` (README "UI theme").
- Commit message trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Out of scope: docx/PDF rendering (Steps 18a/18b), cover letters, batch auto-tailoring, writing accepted bullets into the truth base.

## Test environment (verified 2026-09-30)

The shell is x86_64 under Rosetta and `.env` points at Docker hostnames, so every command below uses this prelude. Run it once per shell:

```bash
cd job_search            # repo subdirectory containing .env, config/, prompts/
set -a; . ./.env; set +a
export DATABASE_URL="${DATABASE_URL//@postgres:/@localhost:}" \
       APP_DATABASE_URL="${APP_DATABASE_URL//@postgres:/@localhost:}" \
       API_BASE_URL=http://localhost:8000 \
       PYTHONPATH="$PWD/packages/core:$PWD/apps/api"
cd packages/core
```

- **Host tests** (everything except API-router tests): `arch -arm64 ../../venv/bin/python -m unittest <dotted.module>`.
- **API-router tests** import `app.main`, which needs `docling` (absent from the host venv). Run them in the API container, which mounts this checkout live: `docker exec -w /app/packages/core job_search-api-1 python -m unittest <dotted.module>`.
- **Migrations** run on the host (the container does not mount `db/`): from `job_search/`, `arch -arm64 ./venv/bin/python -m alembic -c db/alembic.ini upgrade head` with the same env exported.
- Lint a touched file with `arch -arm64 ../../venv/bin/python -m ruff check <file>` and `... -m black --check <file>` (paths relative to `packages/core`; adjust the prefix for files elsewhere).
- Known, unrelated failures: `tests.test_ui_skill_review` fails on `main` already; the host lacks `docling`/`scipy`. Do not "fix" them here.

## Review Focus

Failure modes the spec implies that no happy-path task would otherwise exercise, most likely first. Each has a pinning test in the task named.

1. **A bullet cites a bullet from a *different* role** (moving an achievement to another employer). Must be flagged, never silently accepted. → Task 4 (`cross_role_evidence`), Task 9 (survives retries as an orphan).
2. **The critic's reply omits a verdict for an item** (or is unparseable). Must fail closed — an unanswered item is *unsupported*, an unparseable reply fails the run — never approved. → Task 8, Task 9.
3. **The job has no `title_for_display`** (NULL/blank), so there is nothing to mirror. Must refuse to start with a clear error, not generate with a blank headline. → Task 9 (`start_tailoring`), Task 10 (HTTP 422).
4. **The user has no CV truth base**, or the model's JSON is malformed/truncated on every attempt. Must end `failed`/refuse with a message, never produce an approved document. → Task 9, Task 10.
5. **The model references a role index that does not exist, repeats an index, or omits a role entirely.** An omitted role must keep its original bullets (employment history is never dropped); an unknown index must be ignored; the first of a duplicated index wins. → Task 3.

---

## File Structure

New (all under `job_search/`):

| File | Responsibility |
|---|---|
| `db/migrations/versions/0032_create_tailoring_schema.py` | `tailoring.tailored_cv`, `tailoring.orphan_bullet`, RLS, grants |
| `packages/core/core/tailoring/__init__.py` | package marker |
| `packages/core/core/tailoring/schema.py` | pydantic models: `TailoredDocument`, `Tailor*` output models, `JobSkill`, `JobContext`, `parse_tailor_output` |
| `packages/core/core/tailoring/assemble.py` | deterministic document assembly, `clean_text`, `bullet_index` |
| `packages/core/core/tailoring/checks.py` | `Problem`, code-only checks, keyword coverage |
| `packages/core/core/tailoring/tailor.py` | prompt rendering + the Tailor LLM call |
| `packages/core/core/tailoring/critic.py` | the Claude-only fabrication critic |
| `packages/core/core/tailoring/context.py` | DB reads: `load_job_context`, `list_candidates` |
| `packages/core/core/tailoring/store.py` | persistence of runs and orphan rows |
| `packages/core/core/tailoring/decisions.py` | pure orphan link/reject logic |
| `packages/core/core/tailoring/loop.py` | `start_tailoring`, `execute_tailoring`, `run_tailoring` |
| `prompts/cv_tailoring/local.v1.md`, `claude.v1.md` | Tailor prompts |
| `prompts/fabrication_critic/claude.v1.md` | critic prompt |
| `apps/api/app/routers/tailoring.py` | `/tailoring` endpoints |
| `apps/ui/app/pages/10_Tailored_CV_Review.py` | review page |
| tests | see each task |

Modified: `config/llm_tasks.yml` (add `cv_tailoring`), `apps/api/app/main.py` (include router), `apps/pipeline/app/cli.py` (`tailor-cv`), `packages/core/tests/test_pipeline_registry.py` (exclusion list), `README.md`, the spec (one amendment, Task 11).

---

### Task 1: Migration 0032 — the `tailoring` schema

**Files:**
- Create: `db/migrations/versions/0032_create_tailoring_schema.py`
- Test: `packages/core/tests/integration/test_tailoring_schema.py`

**Interfaces:**
- Consumes: `app_user` table, role `job_search_app`.
- Produces: tables `tailoring.tailored_cv(id, user_id, job_group_id, truth_base_version, target_title, status, content, attempts, stretch, tailor_model, tailor_prompt_version, critic_model, critic_prompt_version, error_message, created_at, updated_at)` and `tailoring.orphan_bullet(id, tailored_cv_id, user_id, kind, section, experience_index, bullet_index, text, claimed_refs, issue, status, evidence_ref, decided_at)`. `status` of `tailored_cv` ∈ `generating|needs_review|approved|failed`; `kind` ∈ `orphan|unsupported`; orphan `status` ∈ `pending|linked|rejected`; `section` ∈ `summary|experience`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_tailoring_schema.py`:

```python
"""Schema tests for the tailoring tables (migration 0032)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.db.session import session_scope


class TestTailoringSchema(unittest.TestCase):
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
                        "VALUES (:id, :email, 'zzfixture tailoring user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )

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

    def _insert_run(self, user_id: uuid.UUID) -> uuid.UUID:
        with session_scope(self.app_engine, user_id=user_id) as conn:
            return conn.execute(
                text(
                    "INSERT INTO tailoring.tailored_cv "
                    "(user_id, job_group_id, truth_base_version, target_title) "
                    "VALUES (:user_id, 'zzfixture-job', 1, 'Zz Title') RETURNING id"
                ),
                {"user_id": user_id},
            ).scalar_one()

    def _insert_orphan(self, user_id: uuid.UUID, run_id: uuid.UUID) -> uuid.UUID:
        with session_scope(self.app_engine, user_id=user_id) as conn:
            return conn.execute(
                text(
                    "INSERT INTO tailoring.orphan_bullet "
                    "(tailored_cv_id, user_id, kind, section, experience_index, "
                    "bullet_index, text) "
                    "VALUES (:run_id, :user_id, 'orphan', 'experience', 0, 0, 'x') "
                    "RETURNING id"
                ),
                {"run_id": run_id, "user_id": user_id},
            ).scalar_one()

    def test_app_role_can_insert_and_read_its_own_run(self) -> None:
        run_id = self._insert_run(self.user_a)
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            row = conn.execute(
                text(
                    "SELECT status, attempts FROM tailoring.tailored_cv WHERE id = :id"
                ),
                {"id": run_id},
            ).one()
        self.assertEqual(row.status, "generating")
        self.assertEqual(row.attempts, 0)

    def test_another_user_cannot_see_the_run(self) -> None:
        run_id = self._insert_run(self.user_a)
        with session_scope(self.app_engine, user_id=self.user_b) as conn:
            rows = conn.execute(
                text("SELECT id FROM tailoring.tailored_cv WHERE id = :id"),
                {"id": run_id},
            ).all()
        self.assertEqual(rows, [])

    def test_another_user_cannot_see_the_orphans(self) -> None:
        run_id = self._insert_run(self.user_a)
        orphan_id = self._insert_orphan(self.user_a, run_id)
        with session_scope(self.app_engine, user_id=self.user_b) as conn:
            rows = conn.execute(
                text("SELECT id FROM tailoring.orphan_bullet WHERE id = :id"),
                {"id": orphan_id},
            ).all()
        self.assertEqual(rows, [])

    def test_invalid_run_status_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tailoring.tailored_cv "
                        "(user_id, job_group_id, truth_base_version, target_title, "
                        "status) VALUES (:u, 'zzfixture-job', 1, 'T', 'bogus')"
                    ),
                    {"u": self.user_a},
                )

    def test_unknown_orphan_kind_is_rejected(self) -> None:
        run_id = self._insert_run(self.user_a)
        with self.assertRaises(IntegrityError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tailoring.orphan_bullet "
                        "(tailored_cv_id, user_id, kind, section, text) "
                        "VALUES (:r, :u, 'bogus', 'summary', 'x')"
                    ),
                    {"r": run_id, "u": self.user_a},
                )

    def test_deleting_a_run_cascades_to_its_orphans(self) -> None:
        run_id = self._insert_run(self.user_a)
        orphan_id = self._insert_orphan(self.user_a, run_id)
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM tailoring.tailored_cv WHERE id = :id"),
                {"id": run_id},
            )
        with self.owner.connect() as conn:
            remaining = conn.execute(
                text("SELECT id FROM tailoring.orphan_bullet WHERE id = :id"),
                {"id": orphan_id},
            ).all()
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run (after the prelude): `arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_schema -v`
Expected: errors such as `relation "tailoring.tailored_cv" does not exist` (or `schema "tailoring" does not exist`).

- [ ] **Step 3: Write the migration**

Create `db/migrations/versions/0032_create_tailoring_schema.py`:

```python
"""create tailoring schema (PLAN.md Step 17)

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-01

Backs docs/superpowers/specs/2026-10-01-step17-tailoring-design.md:

- tailoring.tailored_cv: one row per tailoring run (every run is a new
  row; the UI shows the latest per job). `content` holds the assembled
  TailoredDocument.
- tailoring.orphan_bullet: bullets the loop could not trace to the
  truth base, awaiting an explicit user decision.

Both tables are RLS-isolated per user (same policy pattern as
scoring.job_label, migration 0029): a tailored CV is built from one
user's employment history.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    """Enable RLS and add the standard app.current_user_id policy.

    Args:
        table: The unqualified table name, in the `tailoring` schema.
    """
    op.execute(f"ALTER TABLE tailoring.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_isolation ON tailoring.{table} "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )


def upgrade() -> None:
    """Create the tailoring schema and both tables."""
    op.execute("CREATE SCHEMA IF NOT EXISTS tailoring")
    op.execute("GRANT USAGE ON SCHEMA tailoring TO job_search_app")

    op.create_table(
        "tailored_cv",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("truth_base_version", sa.Integer(), nullable=False),
        sa.Column("target_title", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="generating"
        ),
        sa.Column("content", JSONB(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stretch", JSONB(), nullable=True),
        sa.Column("tailor_model", sa.Text(), nullable=True),
        sa.Column("tailor_prompt_version", sa.Text(), nullable=True),
        sa.Column("critic_model", sa.Text(), nullable=True),
        sa.Column("critic_prompt_version", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('generating', 'needs_review', 'approved', 'failed')",
            name="ck_tailored_cv_status",
        ),
        schema="tailoring",
    )
    op.create_index(
        "ix_tailored_cv_user_job_created",
        "tailored_cv",
        ["user_id", "job_group_id", "created_at"],
        schema="tailoring",
    )
    _rls("tailored_cv")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON tailoring.tailored_cv TO job_search_app"
    )

    op.create_table(
        "orphan_bullet",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tailored_cv_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tailoring.tailored_cv.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("experience_index", sa.Integer(), nullable=True),
        sa.Column("bullet_index", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "claimed_refs",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("issue", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="pending"
        ),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('orphan', 'unsupported')", name="ck_orphan_bullet_kind"
        ),
        sa.CheckConstraint(
            "section IN ('summary', 'experience')", name="ck_orphan_bullet_section"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'linked', 'rejected')",
            name="ck_orphan_bullet_status",
        ),
        schema="tailoring",
    )
    op.create_index(
        "ix_orphan_bullet_run",
        "orphan_bullet",
        ["tailored_cv_id"],
        schema="tailoring",
    )
    _rls("orphan_bullet")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON tailoring.orphan_bullet TO job_search_app"
    )


def downgrade() -> None:
    """Drop both tables and the schema."""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON tailoring.orphan_bullet FROM job_search_app"
    )
    op.drop_index("ix_orphan_bullet_run", table_name="orphan_bullet", schema="tailoring")
    op.drop_table("orphan_bullet", schema="tailoring")
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON tailoring.tailored_cv FROM job_search_app"
    )
    op.drop_index(
        "ix_tailored_cv_user_job_created", table_name="tailored_cv", schema="tailoring"
    )
    op.drop_table("tailored_cv", schema="tailoring")
    op.execute("DROP SCHEMA IF EXISTS tailoring")
```

- [ ] **Step 4: Apply the migration**

Run from `job_search/` with the prelude's env exported (the prelude `cd`s into `packages/core`, so `cd ../..` first):
`cd ../.. && arch -arm64 ./venv/bin/python -m alembic -c db/alembic.ini upgrade head && cd packages/core`
Expected: `Running upgrade 0031 -> 0032, create tailoring schema`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_schema -v`
Expected: 6 tests, all OK.

- [ ] **Step 6: Verify downgrade/upgrade round-trips**

Run (from `job_search/`): `arch -arm64 ./venv/bin/python -m alembic -c db/alembic.ini downgrade -1 && arch -arm64 ./venv/bin/python -m alembic -c db/alembic.ini upgrade head`
Expected: both succeed (0032 down, then up again).

- [ ] **Step 7: Commit**

```bash
git add db/migrations/versions/0032_create_tailoring_schema.py packages/core/tests/integration/test_tailoring_schema.py
git commit -m "feat(job_search): Step 17 — tailoring schema (migration 0032)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Document and Tailor-output models

**Files:**
- Create: `packages/core/core/tailoring/__init__.py` (empty)
- Create: `packages/core/core/tailoring/schema.py`
- Create: `packages/core/tests/tailoring_fixtures.py`
- Test: `packages/core/tests/test_tailoring_schema.py`

**Interfaces:**
- Consumes: `core.cv.schema` (`CVTruthBase`, `Skill`, `Education`, `Certification`, `Project`, `Publication`, `Activity`, `Bullet`, `Experience`), `core.cv.bullet_id.compute_bullet_id`, `core.llm.json_response.parse_json_response`.
- Produces (all in `core.tailoring.schema`):
  - `BulletOrigin = Literal["original", "reworded", "orphan", "linked"]`
  - `TailoredBullet(text: str, evidence_refs: list[str], origin: BulletOrigin)`
  - `TailoredSummary(text: str, evidence_refs: list[str], origin: BulletOrigin)`
  - `TailoredExperience(truth_index: int, company: str, title: str, start: str | None, end: str | None, tech: list[str], bullets: list[TailoredBullet])`
  - `StretchAssessment(is_stretch: bool = False, reason: str = "")`
  - `KeywordCoverage(covered: list[str], missing_evidenced: list[str], missing_unevidenced: list[str])`
  - `TailoredDocument(target_title, headline, identity, email, phone, linkedin_url, nationality, work_auth, locations, summary: TailoredSummary | None, experience: list[TailoredExperience], skills: list[Skill], projects, publications, education, qualifications, activities, stretch: StretchAssessment, keyword_coverage: KeywordCoverage)`
  - `TailorBullet(text, evidence_refs)`, `TailorSummary(text, evidence_refs)`, `TailorExperience(truth_index, bullets)`, `TailorOutput(summary: TailorSummary | None, experience: list[TailorExperience], skills: list[str])`
  - `TailorOutputError(ValueError)`; `parse_tailor_output(text: str) -> TailorOutput`
  - `JobSkill(skill_id: str, label: str, requirement_level: str | None)` and `JobContext(job_group_id: str, title_for_display: str | None, company: str | None, description: str, skills: list[JobSkill])` — frozen dataclasses.
- Test helper produced: `tests.tailoring_fixtures.make_truth_base() -> CVTruthBase` and `bullet_id(truth_base, experience_index, bullet_index) -> str`.

- [ ] **Step 1: Write the shared fixture helper**

Create `packages/core/tests/tailoring_fixtures.py`:

```python
"""Shared fixtures for the Step 17 tailoring tests."""

from __future__ import annotations

from core.cv.bullet_id import compute_bullet_id
from core.cv.schema import Bullet, CVTruthBase, Education, Experience, Skill


def _bullets(experience_index: int, texts: list[str]) -> list[Bullet]:
    """Build bullets with real, stable IDs.

    Args:
        experience_index: The role's index in the truth base.
        texts: The bullet texts.

    Returns:
        The bullets, each with its computed `bullet_id`.
    """
    return [
        Bullet(bullet_id=compute_bullet_id(experience_index, text), text=text)
        for text in texts
    ]


def make_truth_base() -> CVTruthBase:
    """Build a small two-role truth base used across tailoring tests.

    Returns:
        A `CVTruthBase` with a current role (index 0, two bullets), an
        older role (index 1, one bullet) and three canonical-id skills.
    """
    return CVTruthBase(
        identity="Zz Fixture",
        headline="Senior Data Engineer",
        email="zz@example.com",
        summary="Data engineer with eight years building analytics pipelines.",
        experience=[
            Experience(
                company="Acme Bank",
                title="Senior Data Engineer",
                start="2019-01",
                end=None,
                bullets=_bullets(
                    0,
                    [
                        "Built dbt models for risk reporting",
                        "Migrated nightly batch jobs to Airflow",
                    ],
                ),
                tech=["dbt", "Airflow"],
            ),
            Experience(
                company="Beta Retail",
                title="Data Analyst",
                start="2015-06",
                end="2018-12",
                bullets=_bullets(1, ["Wrote SQL reports for the finance team"]),
                tech=["SQL"],
            ),
        ],
        skills=[
            Skill(name="dbt", canonical_id="zzfixture-skill-dbt"),
            Skill(name="Airflow", canonical_id="zzfixture-skill-airflow"),
            Skill(name="SQL", canonical_id="zzfixture-skill-sql"),
        ],
        education=[Education(institution="Zz University", qualification="BSc")],
    )


def bullet_id(truth_base: CVTruthBase, experience_index: int, bullet_index: int) -> str:
    """Look up one bullet's ID.

    Args:
        truth_base: The truth base.
        experience_index: The role index.
        bullet_index: The bullet's position within that role.

    Returns:
        The bullet's `bullet_id`.
    """
    return truth_base.experience[experience_index].bullets[bullet_index].bullet_id
```

- [ ] **Step 2: Write the failing test**

Create `packages/core/tests/test_tailoring_schema.py`:

```python
"""Unit tests for the tailoring document and Tailor-output models."""

from __future__ import annotations

import json
import unittest

from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.tailoring.schema import (
    JobContext,
    JobSkill,
    TailoredBullet,
    TailoredDocument,
    TailorOutputError,
    parse_tailor_output,
)


class TestParseTailorOutput(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()

    def _reply(self) -> dict:
        ref = bullet_id(self.truth_base, 0, 0)
        return {
            "summary": {"text": "Data engineer.", "evidence_refs": [ref]},
            "experience": [
                {
                    "truth_index": 0,
                    "bullets": [{"text": "Built dbt models", "evidence_refs": [ref]}],
                }
            ],
            "skills": ["dbt"],
        }

    def test_parses_a_bare_json_reply(self) -> None:
        output = parse_tailor_output(json.dumps(self._reply()))
        self.assertEqual(output.experience[0].truth_index, 0)
        self.assertEqual(output.experience[0].bullets[0].text, "Built dbt models")
        self.assertEqual(output.skills, ["dbt"])

    def test_parses_a_fenced_reply(self) -> None:
        text = "Here you go:\n```json\n" + json.dumps(self._reply()) + "\n```"
        output = parse_tailor_output(text)
        self.assertEqual(output.skills, ["dbt"])

    def test_missing_sections_default_to_empty(self) -> None:
        output = parse_tailor_output("{}")
        self.assertIsNone(output.summary)
        self.assertEqual(output.experience, [])
        self.assertEqual(output.skills, [])

    def test_unparseable_text_raises(self) -> None:
        with self.assertRaises(TailorOutputError):
            parse_tailor_output("sorry, I cannot do that")

    def test_wrong_shape_raises(self) -> None:
        with self.assertRaises(TailorOutputError):
            parse_tailor_output('{"experience": "not a list"}')

    def test_a_bullet_without_text_raises(self) -> None:
        with self.assertRaises(TailorOutputError):
            parse_tailor_output(
                '{"experience": [{"truth_index": 0, "bullets": [{"evidence_refs": []}]}]}'
            )


class TestTailoredDocument(unittest.TestCase):
    def test_round_trips_through_json(self) -> None:
        document = TailoredDocument(
            target_title="Lead Data Engineer",
            headline="Lead Data Engineer",
            identity="Zz Fixture",
            experience=[],
            skills=[],
        )
        again = TailoredDocument.model_validate_json(document.model_dump_json())
        self.assertEqual(again, document)

    def test_an_unknown_origin_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TailoredBullet(text="x", evidence_refs=[], origin="invented")


class TestJobContext(unittest.TestCase):
    def test_holds_job_skills(self) -> None:
        context = JobContext(
            job_group_id="zzfixture-1",
            title_for_display="Lead Data Engineer",
            company="Gamma",
            description="Build things.",
            skills=[JobSkill("s1", "dbt", "must_have")],
        )
        self.assertEqual(context.skills[0].label, "dbt")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_schema -v`
Expected: `ModuleNotFoundError: No module named 'core.tailoring'`.

- [ ] **Step 4: Write the implementation**

Create `packages/core/core/tailoring/__init__.py` (empty file) and `packages/core/core/tailoring/schema.py`:

```python
"""Models for Step 17's tailored CV (docs/superpowers/specs/
2026-10-01-step17-tailoring-design.md).

Two families live here:

- `Tailor*` — the shape of what the Tailor LLM returns. Deliberately has
  no company, title or date fields: those never pass through the model.
- `Tailored*` / `TailoredDocument` — the assembled document, the contract
  between Step 17 and the Step 18 renderers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from core.cv.schema import (
    Activity,
    Certification,
    Education,
    Project,
    Publication,
    Skill,
)
from core.llm.json_response import parse_json_response

BulletOrigin = Literal["original", "reworded", "orphan", "linked"]
"""How a generated line relates to the truth base.

`original`: text identical to its single source bullet. `reworded`:
changed text with at least one valid source (the critic judges it).
`orphan`: no valid source. `linked`: an orphan the user attached to an
existing truth-base bullet.
"""


class TailoredBullet(BaseModel):
    """One bullet in the tailored document.

    Attributes:
        text: The bullet text as it will appear.
        evidence_refs: `bullet_id`s of the truth-base bullets it draws on.
        origin: How it relates to the truth base.
    """

    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    origin: BulletOrigin


class TailoredSummary(BaseModel):
    """The tailored professional summary.

    Attributes:
        text: The summary text.
        evidence_refs: `bullet_id`s that support it.
        origin: How it relates to the truth base.
    """

    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    origin: BulletOrigin


class TailoredExperience(BaseModel):
    """One role in the tailored document.

    Attributes:
        truth_index: This role's index in the truth base.
        company: Employer — copied from the truth base, never generated.
        title: Job title held — copied from the truth base, never generated.
        start: Start date, copied from the truth base.
        end: End date, copied from the truth base.
        tech: Technologies, copied from the truth base.
        bullets: The tailored bullets for this role.
    """

    truth_index: int
    company: str
    title: str
    start: str | None = None
    end: str | None = None
    tech: list[str] = Field(default_factory=list)
    bullets: list[TailoredBullet] = Field(default_factory=list)


class StretchAssessment(BaseModel):
    """Whether the target title outreaches the truth base's evidence.

    Attributes:
        is_stretch: True when the title implies seniority or scope the
            truth base does not evidence. Advisory only — never a failure.
        reason: One sentence explaining the judgement.
    """

    is_stretch: bool = False
    reason: str = ""


class KeywordCoverage(BaseModel):
    """Job-skill coverage of the tailored document.

    Attributes:
        covered: Job skills present in the document.
        missing_evidenced: Job skills missing from the document that the
            truth base does evidence — safe to ask the Tailor to surface.
        missing_unevidenced: Job skills missing that the truth base does
            not evidence — reported, never requested.
    """

    covered: list[str] = Field(default_factory=list)
    missing_evidenced: list[str] = Field(default_factory=list)
    missing_unevidenced: list[str] = Field(default_factory=list)


class TailoredDocument(BaseModel):
    """The assembled tailored CV, ready for review and later rendering.

    Attributes:
        target_title: `gold.dim_job.title_for_display`, injected.
        headline: Always equal to `target_title`.
        identity: Full name, from the truth base.
        email: Contact email, from the truth base.
        phone: Contact phone, from the truth base.
        linkedin_url: LinkedIn URL, from the truth base.
        nationality: Nationality, from the truth base.
        work_auth: Work authorisation, from the truth base.
        locations: Locations, from the truth base.
        summary: The tailored summary, if any.
        experience: Every role, in truth-base order.
        skills: The skills to show, chosen from the truth base.
        projects: Copied verbatim from the truth base.
        publications: Copied verbatim from the truth base.
        education: Copied verbatim from the truth base.
        qualifications: Copied verbatim from the truth base.
        activities: Copied verbatim from the truth base.
        stretch: The critic's seniority/scope judgement.
        keyword_coverage: Job-skill coverage.
    """

    target_title: str
    headline: str
    identity: str
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    nationality: str | None = None
    work_auth: str | None = None
    locations: list[str] = Field(default_factory=list)
    summary: TailoredSummary | None = None
    experience: list[TailoredExperience] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    qualifications: list[Certification] = Field(default_factory=list)
    activities: list[Activity] = Field(default_factory=list)
    stretch: StretchAssessment = Field(default_factory=StretchAssessment)
    keyword_coverage: KeywordCoverage = Field(default_factory=KeywordCoverage)


class TailorBullet(BaseModel):
    """One bullet instruction from the Tailor.

    Attributes:
        text: The (possibly reworded) bullet.
        evidence_refs: `bullet_id`s it is based on.
    """

    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class TailorSummary(BaseModel):
    """The Tailor's proposed summary.

    Attributes:
        text: The summary text.
        evidence_refs: `bullet_id`s that support it.
    """

    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class TailorExperience(BaseModel):
    """The Tailor's bullets for one role.

    Attributes:
        truth_index: Which truth-base role these bullets belong to.
        bullets: The bullets, in the order they should appear.
    """

    truth_index: int
    bullets: list[TailorBullet] = Field(default_factory=list)


class TailorOutput(BaseModel):
    """Everything the Tailor LLM returns.

    Attributes:
        summary: Proposed summary, if any.
        experience: Per-role bullet instructions.
        skills: Skill names to show, in order.
    """

    summary: TailorSummary | None = None
    experience: list[TailorExperience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class TailorOutputError(ValueError):
    """The Tailor's reply could not be parsed into a `TailorOutput`."""


def parse_tailor_output(text: str) -> TailorOutput:
    """Parse the Tailor LLM's reply.

    Args:
        text: The raw reply text.

    Returns:
        The validated `TailorOutput`.

    Raises:
        TailorOutputError: If the reply is not JSON, or not the expected
            shape.
    """
    try:
        data = parse_json_response(text.strip())
        return TailorOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise TailorOutputError(f"unusable Tailor reply: {exc}") from exc


@dataclass(frozen=True)
class JobSkill:
    """One skill a job asks for.

    Attributes:
        skill_id: The canonical skill id (ESCO or custom).
        label: Human-readable skill name.
        requirement_level: `must_have`, `nice_to_have` or None.
    """

    skill_id: str
    label: str
    requirement_level: str | None


@dataclass(frozen=True)
class JobContext:
    """What the Tailor and critic need to know about the target job.

    Attributes:
        job_group_id: The job's id.
        title_for_display: The title to mirror; None/blank means the job
            cannot be tailored.
        company: The employer, if known.
        description: The job description text.
        skills: The skills the job asks for.
    """

    job_group_id: str
    title_for_display: str | None
    company: str | None
    description: str
    skills: list[JobSkill]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_schema -v`
Expected: 9 tests OK.

- [ ] **Step 6: Lint and commit**

```bash
arch -arm64 ../../venv/bin/python -m ruff check core/tailoring tests/tailoring_fixtures.py tests/test_tailoring_schema.py
arch -arm64 ../../venv/bin/python -m black core/tailoring tests/tailoring_fixtures.py tests/test_tailoring_schema.py
arch -arm64 ../../venv/bin/python -m isort core/tailoring tests/tailoring_fixtures.py tests/test_tailoring_schema.py
git add core/tailoring tests/tailoring_fixtures.py tests/test_tailoring_schema.py
git commit -m "feat(job_search): Step 17 — tailoring document and Tailor-output models

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
Expected: ruff `All checks passed!`; black/isort rewrite nothing or only these files.

---

### Task 3: Deterministic assembly

**Files:**
- Create: `packages/core/core/tailoring/assemble.py`
- Test: `packages/core/tests/test_tailoring_assemble.py`

**Interfaces:**
- Consumes: Task 2 models; `core.cv.schema.CVTruthBase`.
- Produces (`core.tailoring.assemble`):
  - `clean_text(text: str) -> str`
  - `bullet_index(truth_base: CVTruthBase) -> dict[str, tuple[int, str]]` — `bullet_id → (experience_index, text)`
  - `classify_origin(text: str, refs: list[str], known: dict[str, tuple[int, str]]) -> BulletOrigin`
  - `assemble(truth_base: CVTruthBase, output: TailorOutput, *, target_title: str) -> TailoredDocument`

Behaviours pinned (Review Focus 5): an omitted role keeps its original bullets; a role with an explicit empty `bullets` list keeps the role with no bullets; an out-of-range `truth_index` is ignored; a duplicated `truth_index` — the first wins; skills not in the truth base are dropped; empty skills fall back to all truth-base skills.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_tailoring_assemble.py`:

```python
"""Unit tests for deterministic tailored-document assembly."""

from __future__ import annotations

import unittest

from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.tailoring.assemble import (
    assemble,
    bullet_index,
    classify_origin,
    clean_text,
)
from core.tailoring.schema import (
    TailorBullet,
    TailorExperience,
    TailorOutput,
    TailorSummary,
)

TITLE = "Lead Data Engineer"


class TestCleanText(unittest.TestCase):
    def test_strips_a_leading_bullet_glyph(self) -> None:
        self.assertEqual(clean_text("• Built dbt models"), "Built dbt models")

    def test_strips_a_leading_dash_followed_by_space(self) -> None:
        self.assertEqual(clean_text("- Built dbt models"), "Built dbt models")

    def test_keeps_a_leading_minus_sign_that_is_part_of_a_number(self) -> None:
        self.assertEqual(clean_text("-5% churn"), "-5% churn")

    def test_removes_decorative_symbols_and_emoji(self) -> None:
        self.assertEqual(clean_text("Led ★ the team ✅ 🚀"), "Led the team")

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(clean_text("  Built   dbt\n models "), "Built dbt models")


class TestClassifyOrigin(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.known = bullet_index(self.truth_base)
        self.ref = bullet_id(self.truth_base, 0, 0)

    def test_identical_text_with_one_valid_ref_is_original(self) -> None:
        origin = classify_origin("Built dbt models for risk reporting", [self.ref], self.known)
        self.assertEqual(origin, "original")

    def test_identical_text_ignoring_case_and_spacing_is_original(self) -> None:
        origin = classify_origin("built  DBT models for risk reporting", [self.ref], self.known)
        self.assertEqual(origin, "original")

    def test_changed_text_with_a_valid_ref_is_reworded(self) -> None:
        origin = classify_origin("Built dbt models powering risk reporting", [self.ref], self.known)
        self.assertEqual(origin, "reworded")

    def test_no_refs_is_orphan(self) -> None:
        self.assertEqual(classify_origin("Anything", [], self.known), "orphan")

    def test_only_unknown_refs_is_orphan(self) -> None:
        self.assertEqual(classify_origin("Anything", ["nope"], self.known), "orphan")


class TestAssemble(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        self.ref1 = bullet_id(self.truth_base, 0, 1)
        self.ref_old = bullet_id(self.truth_base, 1, 0)

    def test_titles_companies_and_dates_come_from_the_truth_base(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[TailorBullet(text="Built dbt models", evidence_refs=[self.ref0])],
                )
            ]
        )
        document = assemble(self.truth_base, output, target_title=TITLE)
        role = document.experience[0]
        self.assertEqual(
            (role.company, role.title, role.start, role.end),
            ("Acme Bank", "Senior Data Engineer", "2019-01", None),
        )
        self.assertEqual(document.experience[1].company, "Beta Retail")

    def test_headline_and_target_title_are_the_injected_title(self) -> None:
        document = assemble(self.truth_base, TailorOutput(), target_title=TITLE)
        self.assertEqual(document.target_title, TITLE)
        self.assertEqual(document.headline, TITLE)

    def test_bullets_are_ordered_and_classified(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[
                        TailorBullet(
                            text="Migrated nightly batch jobs to Airflow",
                            evidence_refs=[self.ref1],
                        ),
                        TailorBullet(
                            text="Built dbt models powering risk reporting",
                            evidence_refs=[self.ref0],
                        ),
                        TailorBullet(text="Led a team of 12", evidence_refs=[]),
                    ],
                )
            ]
        )
        bullets = assemble(self.truth_base, output, target_title=TITLE).experience[0].bullets
        self.assertEqual(
            [b.origin for b in bullets], ["original", "reworded", "orphan"]
        )
        self.assertEqual(bullets[0].text, "Migrated nightly batch jobs to Airflow")

    def test_generated_text_is_cleaned(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[TailorBullet(text="• Built dbt models ✅", evidence_refs=[self.ref0])],
                )
            ]
        )
        bullet = assemble(self.truth_base, output, target_title=TITLE).experience[0].bullets[0]
        self.assertEqual(bullet.text, "Built dbt models")

    def test_an_omitted_role_keeps_its_original_bullets(self) -> None:
        output = TailorOutput(experience=[])
        document = assemble(self.truth_base, output, target_title=TITLE)
        self.assertEqual(
            [b.text for b in document.experience[1].bullets],
            ["Wrote SQL reports for the finance team"],
        )
        self.assertEqual(document.experience[1].bullets[0].origin, "original")
        self.assertEqual(document.experience[1].bullets[0].evidence_refs, [self.ref_old])

    def test_a_role_with_an_empty_bullet_list_is_kept_without_bullets(self) -> None:
        output = TailorOutput(experience=[TailorExperience(truth_index=1, bullets=[])])
        document = assemble(self.truth_base, output, target_title=TITLE)
        self.assertEqual(len(document.experience), 2)
        self.assertEqual(document.experience[1].bullets, [])

    def test_an_out_of_range_role_index_is_ignored(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=99,
                    bullets=[TailorBullet(text="Ghost", evidence_refs=[self.ref0])],
                )
            ]
        )
        document = assemble(self.truth_base, output, target_title=TITLE)
        self.assertEqual(len(document.experience), 2)
        all_text = [b.text for e in document.experience for b in e.bullets]
        self.assertNotIn("Ghost", all_text)

    def test_the_first_of_a_duplicated_role_index_wins(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[TailorBullet(text="First", evidence_refs=[self.ref0])],
                ),
                TailorExperience(
                    truth_index=0,
                    bullets=[TailorBullet(text="Second", evidence_refs=[self.ref0])],
                ),
            ]
        )
        bullets = assemble(self.truth_base, output, target_title=TITLE).experience[0].bullets
        self.assertEqual([b.text for b in bullets], ["First"])

    def test_blank_bullets_are_dropped(self) -> None:
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[TailorBullet(text="  ✅ ", evidence_refs=[self.ref0])],
                )
            ]
        )
        self.assertEqual(
            assemble(self.truth_base, output, target_title=TITLE).experience[0].bullets,
            [],
        )

    def test_skills_keep_the_models_order_and_drop_unknown_names(self) -> None:
        output = TailorOutput(skills=["airflow", "Kubernetes", "dbt", "AIRFLOW"])
        skills = assemble(self.truth_base, output, target_title=TITLE).skills
        self.assertEqual([s.name for s in skills], ["Airflow", "dbt"])
        self.assertEqual(skills[0].canonical_id, "zzfixture-skill-airflow")

    def test_empty_skills_fall_back_to_all_truth_base_skills(self) -> None:
        document = assemble(self.truth_base, TailorOutput(), target_title=TITLE)
        self.assertEqual([s.name for s in document.skills], ["dbt", "Airflow", "SQL"])

    def test_a_summary_matching_the_truth_base_is_original(self) -> None:
        output = TailorOutput(
            summary=TailorSummary(
                text="Data engineer with eight years building analytics pipelines.",
                evidence_refs=[],
            )
        )
        summary = assemble(self.truth_base, output, target_title=TITLE).summary
        self.assertEqual(summary.origin, "original")

    def test_a_new_summary_with_a_valid_ref_is_reworded(self) -> None:
        output = TailorOutput(
            summary=TailorSummary(text="Pipeline specialist.", evidence_refs=[self.ref0])
        )
        self.assertEqual(
            assemble(self.truth_base, output, target_title=TITLE).summary.origin,
            "reworded",
        )

    def test_a_new_summary_with_no_ref_is_an_orphan(self) -> None:
        output = TailorOutput(
            summary=TailorSummary(text="World-class leader.", evidence_refs=[])
        )
        self.assertEqual(
            assemble(self.truth_base, output, target_title=TITLE).summary.origin,
            "orphan",
        )

    def test_no_model_summary_falls_back_to_the_truth_base_summary(self) -> None:
        summary = assemble(self.truth_base, TailorOutput(), target_title=TITLE).summary
        self.assertEqual(summary.origin, "original")
        self.assertEqual(summary.text, self.truth_base.summary)

    def test_static_sections_are_copied_verbatim(self) -> None:
        document = assemble(self.truth_base, TailorOutput(), target_title=TITLE)
        self.assertEqual(document.education, self.truth_base.education)
        self.assertEqual(document.identity, "Zz Fixture")
        self.assertEqual(document.email, "zz@example.com")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_assemble -v`
Expected: `ModuleNotFoundError: No module named 'core.tailoring.assemble'`.

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/tailoring/assemble.py`:

```python
"""Deterministic assembly of a TailoredDocument (Step 17, approach B:
"assemble, don't generate").

Code builds the document from the truth base. The Tailor's output only
chooses bullet order/wording and skills; companies, titles and dates are
copied by index and never pass through the model, so "no experience title
differs from the truth base" holds by construction.
"""

from __future__ import annotations

import re
import unicodedata

from core.cv.schema import CVTruthBase
from core.tailoring.schema import (
    BulletOrigin,
    TailoredBullet,
    TailoredDocument,
    TailoredExperience,
    TailoredSummary,
    TailorExperience,
    TailorOutput,
)

# A leading decorative bullet glyph, or a "- " dash-then-space marker. A
# bare leading minus ("-5% churn") is a number, not a marker, so a dash
# only counts when whitespace follows it.
_LEADING_MARKER_RE = re.compile(r"^\s*(?:[•▪●◦■–—*·]+\s*|-\s+)")


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace for comparison.

    Args:
        text: Any text.

    Returns:
        The normalised text.
    """
    return re.sub(r"\s+", " ", text.strip().lower())


def clean_text(text: str) -> str:
    """Mechanically enforce content-level ATS hygiene on generated text.

    Removes a leading bullet glyph, decorative symbols and emoji, and
    collapses whitespace. Done in code rather than asked of the model
    (PLAN.md Step 18: enforce mechanically, not by prompting).

    Args:
        text: Generated text.

    Returns:
        The cleaned text; may be empty.
    """
    stripped = _LEADING_MARKER_RE.sub("", text.strip())
    kept = "".join(
        ch
        for ch in stripped
        if unicodedata.category(ch) != "So" and not 0x1F000 <= ord(ch) <= 0x1FAFF
    )
    return re.sub(r"\s+", " ", kept).strip()


def bullet_index(truth_base: CVTruthBase) -> dict[str, tuple[int, str]]:
    """Index every truth-base bullet by its ID.

    Args:
        truth_base: The truth base.

    Returns:
        A map of `bullet_id` to `(experience_index, text)`.
    """
    return {
        bullet.bullet_id: (index, bullet.text)
        for index, experience in enumerate(truth_base.experience)
        for bullet in experience.bullets
    }


def classify_origin(
    text: str, refs: list[str], known: dict[str, tuple[int, str]]
) -> BulletOrigin:
    """Classify a generated line against the truth base.

    Args:
        text: The generated text.
        refs: The `bullet_id`s the Tailor cited.
        known: The result of `bullet_index`.

    Returns:
        `orphan` when no cited ID exists; `original` when exactly one
        valid ID is cited and the text matches that bullet (ignoring case
        and spacing); otherwise `reworded`.
    """
    valid = [ref for ref in refs if ref in known]
    if not valid:
        return "orphan"
    if len(valid) == 1 and _normalise(text) == _normalise(known[valid[0]][1]):
        return "original"
    return "reworded"


def _assemble_summary(
    truth_base: CVTruthBase,
    output: TailorOutput,
    known: dict[str, tuple[int, str]],
) -> TailoredSummary | None:
    """Build the summary section.

    Args:
        truth_base: The truth base.
        output: The Tailor's output.
        known: The result of `bullet_index`.

    Returns:
        The tailored summary, the truth-base summary as `original` when the
        model gave none, or None when neither exists.
    """
    if output.summary is not None:
        text = clean_text(output.summary.text)
        if text:
            refs = list(output.summary.evidence_refs)
            if truth_base.summary and _normalise(text) == _normalise(
                truth_base.summary
            ):
                origin: BulletOrigin = "original"
            elif any(ref in known for ref in refs):
                origin = "reworded"
            else:
                origin = "orphan"
            return TailoredSummary(text=text, evidence_refs=refs, origin=origin)
    if truth_base.summary:
        return TailoredSummary(
            text=truth_base.summary, evidence_refs=[], origin="original"
        )
    return None


def assemble(
    truth_base: CVTruthBase, output: TailorOutput, *, target_title: str
) -> TailoredDocument:
    """Build the tailored document.

    Args:
        truth_base: The user's CV truth base.
        output: The Tailor's parsed output.
        target_title: `title_for_display`, injected as the headline.

    Returns:
        A `TailoredDocument`. Roles are always the truth base's, in order.
        A role the model omitted keeps its original bullets; a role index
        outside the truth base is ignored; for a repeated role index the
        first entry wins.
    """
    known = bullet_index(truth_base)
    by_role: dict[int, TailorExperience] = {}
    for entry in output.experience:
        if 0 <= entry.truth_index < len(truth_base.experience):
            by_role.setdefault(entry.truth_index, entry)

    experience: list[TailoredExperience] = []
    for index, role in enumerate(truth_base.experience):
        chosen = by_role.get(index)
        if chosen is None:
            bullets = [
                TailoredBullet(
                    text=b.text, evidence_refs=[b.bullet_id], origin="original"
                )
                for b in role.bullets
            ]
        else:
            bullets = []
            for item in chosen.bullets:
                text = clean_text(item.text)
                if not text:
                    continue
                refs = list(item.evidence_refs)
                bullets.append(
                    TailoredBullet(
                        text=text,
                        evidence_refs=refs,
                        origin=classify_origin(text, refs, known),
                    )
                )
        experience.append(
            TailoredExperience(
                truth_index=index,
                company=role.company,
                title=role.title,
                start=role.start,
                end=role.end,
                tech=list(role.tech),
                bullets=bullets,
            )
        )

    by_name = {skill.name.casefold(): skill for skill in truth_base.skills}
    chosen_skills = []
    seen: set[str] = set()
    for name in output.skills:
        skill = by_name.get(name.strip().casefold())
        if skill is not None and skill.name.casefold() not in seen:
            chosen_skills.append(skill)
            seen.add(skill.name.casefold())

    return TailoredDocument(
        target_title=target_title,
        headline=target_title,
        identity=truth_base.identity,
        email=truth_base.email,
        phone=truth_base.phone,
        linkedin_url=truth_base.linkedin_url,
        nationality=truth_base.nationality,
        work_auth=truth_base.work_auth,
        locations=list(truth_base.locations),
        summary=_assemble_summary(truth_base, output, known),
        experience=experience,
        skills=chosen_skills or list(truth_base.skills),
        projects=list(truth_base.projects),
        publications=list(truth_base.publications),
        education=list(truth_base.education),
        qualifications=list(truth_base.qualifications),
        activities=list(truth_base.activities),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_assemble -v`
Expected: 24 tests OK.

- [ ] **Step 5: Lint and commit**

```bash
arch -arm64 ../../venv/bin/python -m ruff check core/tailoring/assemble.py tests/test_tailoring_assemble.py
arch -arm64 ../../venv/bin/python -m black core/tailoring/assemble.py tests/test_tailoring_assemble.py
arch -arm64 ../../venv/bin/python -m isort core/tailoring/assemble.py tests/test_tailoring_assemble.py
arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_assemble 2>&1 | tail -3
git add core/tailoring/assemble.py tests/test_tailoring_assemble.py
git commit -m "feat(job_search): Step 17 — deterministic document assembly

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
Expected: ruff clean; tests still OK after black reformatting.

---

### Task 4: Code-only checks and keyword coverage

**Files:**
- Create: `packages/core/core/tailoring/checks.py`
- Test: `packages/core/tests/test_tailoring_checks.py`

**Interfaces:**
- Consumes: Task 2 models, Task 3 `bullet_index`.
- Produces (`core.tailoring.checks`):
  - `Problem(code: str, message: str, location: str)` — frozen dataclass. Codes: `invalid_evidence_ref`, `cross_role_evidence`, `experience_changed`, `headline_mismatch`, `missing_target_title`. `location` is `"summary"`, `"e{role}b{bullet}"`, `"e{role}"` or `"headline"`.
  - `STRUCTURAL_CODES: frozenset[str]` = `{experience_changed, headline_mismatch, missing_target_title}` — problems that cannot be turned into an orphan and must fail the run.
  - `check_evidence_refs(document, truth_base) -> list[Problem]`
  - `check_experience_unchanged(document, truth_base) -> list[Problem]`
  - `check_headline(document) -> list[Problem]`
  - `compute_keyword_coverage(document, truth_base, job_skills: list[JobSkill]) -> KeywordCoverage`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_tailoring_checks.py`:

```python
"""Unit tests for the code-only tailoring checks."""

from __future__ import annotations

import unittest

from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.cv.schema import Skill
from core.tailoring.assemble import assemble
from core.tailoring.checks import (
    STRUCTURAL_CODES,
    check_evidence_refs,
    check_experience_unchanged,
    check_headline,
    compute_keyword_coverage,
)
from core.tailoring.schema import (
    JobSkill,
    TailorBullet,
    TailorExperience,
    TailorOutput,
)

TITLE = "Lead Data Engineer"


def _document(truth_base, bullets_by_role: dict[int, list[TailorBullet]], skills=None):
    output = TailorOutput(
        experience=[
            TailorExperience(truth_index=i, bullets=b) for i, b in bullets_by_role.items()
        ],
        skills=skills or [],
    )
    return assemble(truth_base, output, target_title=TITLE)


class TestEvidenceChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        self.ref_old = bullet_id(self.truth_base, 1, 0)

    def test_a_valid_same_role_reference_has_no_problem(self) -> None:
        document = _document(
            self.truth_base, {0: [TailorBullet(text="Built it", evidence_refs=[self.ref0])]}
        )
        self.assertEqual(check_evidence_refs(document, self.truth_base), [])

    def test_an_unknown_reference_is_flagged_with_its_location(self) -> None:
        document = _document(
            self.truth_base,
            {0: [TailorBullet(text="Built it", evidence_refs=[self.ref0, "nope"])]},
        )
        problems = check_evidence_refs(document, self.truth_base)
        self.assertEqual([p.code for p in problems], ["invalid_evidence_ref"])
        self.assertEqual(problems[0].location, "e0b0")

    def test_a_bullet_citing_another_role_is_flagged(self) -> None:
        # Review Focus 1: moving an achievement to a different employer.
        document = _document(
            self.truth_base,
            {0: [TailorBullet(text="Wrote reports", evidence_refs=[self.ref_old])]},
        )
        problems = check_evidence_refs(document, self.truth_base)
        self.assertEqual([p.code for p in problems], ["cross_role_evidence"])
        self.assertEqual(problems[0].location, "e0b0")

    def test_an_orphan_with_no_refs_is_not_an_evidence_problem(self) -> None:
        # Orphans are surfaced through their `origin`, not as a Problem.
        document = _document(
            self.truth_base, {0: [TailorBullet(text="Invented", evidence_refs=[])]}
        )
        self.assertEqual(check_evidence_refs(document, self.truth_base), [])

    def test_an_unknown_summary_reference_is_flagged(self) -> None:
        from core.tailoring.schema import TailorSummary

        output = TailorOutput(
            summary=TailorSummary(text="New summary", evidence_refs=["nope"])
        )
        document = assemble(self.truth_base, output, target_title=TITLE)
        problems = check_evidence_refs(document, self.truth_base)
        self.assertEqual(problems[0].location, "summary")


class TestExperienceUnchanged(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.document = _document(self.truth_base, {})

    def test_an_assembled_document_has_no_problem(self) -> None:
        self.assertEqual(
            check_experience_unchanged(self.document, self.truth_base), []
        )

    def test_a_changed_title_is_flagged(self) -> None:
        self.document.experience[0].title = "Head of Data"
        problems = check_experience_unchanged(self.document, self.truth_base)
        self.assertEqual([p.code for p in problems], ["experience_changed"])
        self.assertEqual(problems[0].location, "e0")

    def test_a_changed_date_is_flagged(self) -> None:
        self.document.experience[1].end = "2020-01"
        problems = check_experience_unchanged(self.document, self.truth_base)
        self.assertEqual(problems[0].location, "e1")

    def test_a_missing_role_is_flagged(self) -> None:
        self.document.experience.pop()
        problems = check_experience_unchanged(self.document, self.truth_base)
        self.assertEqual([p.code for p in problems], ["experience_changed"])

    def test_structural_codes_cover_what_cannot_become_an_orphan(self) -> None:
        self.assertEqual(
            STRUCTURAL_CODES,
            frozenset(
                {"experience_changed", "headline_mismatch", "missing_target_title"}
            ),
        )


class TestHeadline(unittest.TestCase):
    def setUp(self) -> None:
        self.document = _document(make_truth_base(), {})

    def test_the_exact_target_title_passes(self) -> None:
        self.assertEqual(check_headline(self.document), [])

    def test_a_different_headline_is_flagged(self) -> None:
        self.document.headline = "Senior Data Engineer"
        self.assertEqual(
            [p.code for p in check_headline(self.document)], ["headline_mismatch"]
        )

    def test_a_case_difference_is_flagged(self) -> None:
        self.document.headline = TITLE.upper()
        self.assertEqual(
            [p.code for p in check_headline(self.document)], ["headline_mismatch"]
        )

    def test_a_blank_target_title_is_flagged(self) -> None:
        self.document.target_title = "  "
        self.document.headline = "  "
        self.assertEqual(
            [p.code for p in check_headline(self.document)], ["missing_target_title"]
        )


class TestKeywordCoverage(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.job_skills = [
            JobSkill("zzfixture-skill-dbt", "dbt", "must_have"),
            JobSkill("zzfixture-skill-sql", "SQL", "nice_to_have"),
            JobSkill("zzfixture-skill-k8s", "Kubernetes", "must_have"),
        ]

    def test_a_skill_shown_in_the_document_is_covered(self) -> None:
        document = _document(self.truth_base, {}, skills=["dbt"])
        coverage = compute_keyword_coverage(document, self.truth_base, self.job_skills)
        self.assertIn("dbt", coverage.covered)

    def test_a_skill_the_cv_has_but_the_document_omits_is_missing_evidenced(self) -> None:
        # Both roles are emptied: an omitted role keeps its original bullets,
        # and role 1's original bullet mentions SQL, which would cover it.
        document = _document(self.truth_base, {0: [], 1: []}, skills=["dbt"])
        coverage = compute_keyword_coverage(document, self.truth_base, self.job_skills)
        self.assertEqual(coverage.missing_evidenced, ["SQL"])

    def test_a_skill_the_cv_never_evidences_is_missing_unevidenced(self) -> None:
        document = _document(self.truth_base, {}, skills=["dbt", "SQL"])
        coverage = compute_keyword_coverage(document, self.truth_base, self.job_skills)
        self.assertEqual(coverage.missing_unevidenced, ["Kubernetes"])
        self.assertNotIn("Kubernetes", coverage.missing_evidenced)

    def test_a_label_mentioned_in_bullet_text_counts_as_covered(self) -> None:
        document = _document(self.truth_base, {})
        coverage = compute_keyword_coverage(
            document,
            self.truth_base,
            [JobSkill("x", "Airflow", "must_have")],
        )
        self.assertEqual(coverage.covered, ["Airflow"])

    def test_matching_respects_word_boundaries(self) -> None:
        # "R" must not match inside "Airflow" or "dbt models for risk reporting".
        document = _document(self.truth_base, {})
        coverage = compute_keyword_coverage(
            document, self.truth_base, [JobSkill("zzfixture-r", "R", "nice_to_have")]
        )
        self.assertEqual(coverage.covered, [])
        self.assertEqual(coverage.missing_unevidenced, ["R"])

    def test_canonical_id_match_covers_a_differently_named_skill(self) -> None:
        self.truth_base.skills.append(Skill(name="Data build tool", canonical_id="zz-id"))
        document = _document(self.truth_base, {}, skills=["Data build tool"])
        coverage = compute_keyword_coverage(
            document, self.truth_base, [JobSkill("zz-id", "dbt core", "must_have")]
        )
        self.assertEqual(coverage.covered, ["dbt core"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_checks -v`
Expected: `ModuleNotFoundError: No module named 'core.tailoring.checks'`.

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/tailoring/checks.py`:

```python
"""Code-only checks on an assembled TailoredDocument (Step 17).

Everything that can be verified mechanically is verified here, not left
to an LLM. The critic (core.tailoring.critic) covers only the semantic
gap: does a reworded bullet's claim follow from its cited sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.cv.schema import CVTruthBase
from core.tailoring.assemble import bullet_index
from core.tailoring.schema import JobSkill, KeywordCoverage, TailoredDocument

STRUCTURAL_CODES = frozenset(
    {"experience_changed", "headline_mismatch", "missing_target_title"}
)
"""Problems that cannot be shown to the user as an orphan bullet — they
mean the document itself is wrong, so the run must fail."""


@dataclass(frozen=True)
class Problem:
    """One problem found by a code check.

    Attributes:
        code: Machine-readable kind, e.g. `cross_role_evidence`.
        message: Human-readable explanation, also fed back to the Tailor.
        location: `summary`, `e{role}b{bullet}`, `e{role}` or `headline`.
    """

    code: str
    message: str
    location: str


def check_evidence_refs(
    document: TailoredDocument, truth_base: CVTruthBase
) -> list[Problem]:
    """Check every cited `evidence_ref` exists and stays within its role.

    A bullet with no refs at all is an orphan (see its `origin`), not a
    problem here. A bullet citing a bullet from a *different* role is
    flagged: that moves an achievement to another employer.

    Args:
        document: The assembled document.
        truth_base: The truth base it was assembled from.

    Returns:
        One `Problem` per bad reference.
    """
    known = bullet_index(truth_base)
    problems: list[Problem] = []
    if document.summary is not None:
        for ref in document.summary.evidence_refs:
            if ref not in known:
                problems.append(
                    Problem(
                        "invalid_evidence_ref",
                        f"the summary cites unknown bullet id {ref!r}",
                        "summary",
                    )
                )
    for role in document.experience:
        for position, bullet in enumerate(role.bullets):
            location = f"e{role.truth_index}b{position}"
            for ref in bullet.evidence_refs:
                if ref not in known:
                    problems.append(
                        Problem(
                            "invalid_evidence_ref",
                            f"bullet {location} cites unknown bullet id {ref!r}",
                            location,
                        )
                    )
                elif known[ref][0] != role.truth_index:
                    problems.append(
                        Problem(
                            "cross_role_evidence",
                            f"bullet {location} cites {ref!r} from another role; "
                            "a bullet may only cite bullets from its own role",
                            location,
                        )
                    )
    return problems


def check_experience_unchanged(
    document: TailoredDocument, truth_base: CVTruthBase
) -> list[Problem]:
    """Assert no role's company, title or dates differ from the truth base.

    Assembly makes this true by construction; the check is kept as defence
    in depth (PLAN.md Step 17: the second assertion "is the one that
    matters").

    Args:
        document: The assembled document.
        truth_base: The truth base.

    Returns:
        One `Problem` per differing or missing role.
    """
    problems: list[Problem] = []
    if len(document.experience) != len(truth_base.experience):
        problems.append(
            Problem(
                "experience_changed",
                f"the document has {len(document.experience)} roles, "
                f"the truth base has {len(truth_base.experience)}",
                "e0",
            )
        )
    for index, truth_role in enumerate(truth_base.experience):
        if index >= len(document.experience):
            break
        role = document.experience[index]
        if (role.company, role.title, role.start, role.end) != (
            truth_role.company,
            truth_role.title,
            truth_role.start,
            truth_role.end,
        ):
            problems.append(
                Problem(
                    "experience_changed",
                    f"role {index} differs from the truth base "
                    f"({role.title} at {role.company})",
                    f"e{index}",
                )
            )
    return problems


def check_headline(document: TailoredDocument) -> list[Problem]:
    """Assert the headline is exactly the injected target title.

    Args:
        document: The assembled document.

    Returns:
        A `missing_target_title` problem when there is no title to mirror,
        else a `headline_mismatch` problem when the headline differs from
        it in any way (including case).
    """
    if not document.target_title.strip():
        return [
            Problem(
                "missing_target_title", "there is no target title to mirror", "headline"
            )
        ]
    if document.headline != document.target_title:
        return [
            Problem(
                "headline_mismatch",
                f"the headline {document.headline!r} is not exactly the target "
                f"title {document.target_title!r}",
                "headline",
            )
        ]
    return []


def _mentions(text: str, label: str) -> bool:
    """Whether `label` appears in `text` as a whole word or phrase.

    Args:
        text: Text to search, already case-folded.
        label: The skill label.

    Returns:
        True on a whole-word, case-insensitive match.
    """
    pattern = rf"(?<!\w){re.escape(label.casefold())}(?!\w)"
    return re.search(pattern, text) is not None


def compute_keyword_coverage(
    document: TailoredDocument,
    truth_base: CVTruthBase,
    job_skills: list[JobSkill],
) -> KeywordCoverage:
    """Split a job's skills into covered / missing-but-evidenced / unevidenced.

    Only the *evidenced* gap is ever asked of the Tailor; asking it to
    surface a skill the CV does not evidence would invite fabrication.

    Args:
        document: The assembled document.
        truth_base: The truth base (the source of evidence).
        job_skills: The skills the job asks for.

    Returns:
        A `KeywordCoverage` of skill labels.
    """
    pieces = [bullet.text for role in document.experience for bullet in role.bullets]
    if document.summary is not None:
        pieces.append(document.summary.text)
    pieces.extend(skill.name for skill in document.skills)
    document_text = " ".join(pieces).casefold()
    document_ids = {s.canonical_id for s in document.skills if s.canonical_id}

    truth_text = " ".join(
        bullet.text for role in truth_base.experience for bullet in role.bullets
    ).casefold()
    truth_ids = {s.canonical_id for s in truth_base.skills if s.canonical_id}
    truth_names = {s.name.casefold() for s in truth_base.skills}

    covered: list[str] = []
    missing_evidenced: list[str] = []
    missing_unevidenced: list[str] = []
    for job_skill in job_skills:
        label = job_skill.label
        if job_skill.skill_id in document_ids or _mentions(document_text, label):
            covered.append(label)
        elif (
            job_skill.skill_id in truth_ids
            or label.casefold() in truth_names
            or _mentions(truth_text, label)
        ):
            missing_evidenced.append(label)
        else:
            missing_unevidenced.append(label)
    return KeywordCoverage(
        covered=covered,
        missing_evidenced=missing_evidenced,
        missing_unevidenced=missing_unevidenced,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_checks -v`
Expected: 19 tests OK.

- [ ] **Step 5: Lint and commit**

```bash
arch -arm64 ../../venv/bin/python -m ruff check core/tailoring/checks.py tests/test_tailoring_checks.py
arch -arm64 ../../venv/bin/python -m black core/tailoring/checks.py tests/test_tailoring_checks.py
arch -arm64 ../../venv/bin/python -m isort core/tailoring/checks.py tests/test_tailoring_checks.py
arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_checks 2>&1 | tail -3
git add core/tailoring/checks.py tests/test_tailoring_checks.py
git commit -m "feat(job_search): Step 17 — code-only checks and keyword coverage

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The Tailor (prompt rendering + LLM call)

**Files:**
- Modify: `config/llm_tasks.yml` (add `cv_tailoring`)
- Create: `prompts/cv_tailoring/local.v1.md`, `prompts/cv_tailoring/claude.v1.md`
- Create: `packages/core/core/tailoring/tailor.py`
- Test: `packages/core/tests/test_tailoring_tailor.py`

**Interfaces:**
- Consumes: `core.llm.gateway.complete`, `core.llm.prompts.load_prompt`, `core.llm.task_config.load_task_config`, Task 2 `parse_tailor_output`/`TailorOutputError`/`JobContext`.
- Produces (`core.tailoring.tailor`):
  - `TASK = "cv_tailoring"`
  - `render_truth_base(truth_base: CVTruthBase) -> str`
  - `render_job_skills(job_skills: list[JobSkill]) -> str`
  - `render_feedback(feedback: list[str]) -> str`
  - `TailorResult(output: TailorOutput, model: str, prompt_version: str)` — frozen dataclass
  - `run_tailor(truth_base, job: JobContext, feedback: list[str], *, adapters: dict[str, LLMAdapter], config_path: Path | None = None) -> TailorResult` — raises `TailorOutputError` on truncated or unusable replies.

- [ ] **Step 1: Add the task config**

Edit `config/llm_tasks.yml`: append under `tasks:` (after `job_scoring`):

```yaml
  # Step 17. Local in dev per DECISIONS.md §1; to move to Claude change
  # these three lines to provider: anthropic, model: claude-sonnet-5,
  # prompt_family: claude (prompts/cv_tailoring/claude.v1.md exists).
  cv_tailoring:
    provider: ollama
    model: llama3.1:8b
    prompt_family: local
```

- [ ] **Step 2: Write the two prompt files**

Create `prompts/cv_tailoring/local.v1.md` **and** `prompts/cv_tailoring/claude.v1.md` with identical content (per DECISIONS.md §1 prompts are never converted between families; they start identical and diverge as each family is tuned):

```
You tailor a candidate's CV to one job WITHOUT inventing anything.

Rules (strict):
- Every bullet you output must cite, in "evidence_refs", the bullet id(s) it is based on, copied exactly from the CV below.
- You may reorder, drop, merge and reword existing bullets, and choose which skills to show.
- You must NOT add any fact, number, technology, tool, team size, scope or outcome that is not in the cited bullets.
- You must NOT change any company, job title or date. They are not part of your output.
- Use the job's own wording for skills the candidate genuinely has.
- A bullet may cite bullet ids from its own role only.

CV (roles are numbered; bullet ids are in parentheses):
{cv_text}

Target job title: {job_title}

Job description:
{job_description}

Skills the job asks for:
{job_skills}
{feedback}
Respond with ONLY a JSON object, no other text:
{{"summary": {{"text": "<two or three sentences>", "evidence_refs": [<bullet id>, ...]}}, "experience": [{{"truth_index": <role number>, "bullets": [{{"text": "<bullet>", "evidence_refs": [<bullet id>, ...]}}]}}], "skills": [<skill name copied from the CV's skills>, ...]}}
```

- [ ] **Step 3: Write the failing test**

Create `packages/core/tests/test_tailoring_tailor.py`:

```python
"""Unit tests for the Tailor: prompt rendering and the LLM call."""

from __future__ import annotations

import json
import unittest

from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.llm.types import LLMResponse
from core.tailoring.schema import JobContext, JobSkill, TailorOutputError
from core.tailoring.tailor import (
    TASK,
    render_feedback,
    render_job_skills,
    render_truth_base,
    run_tailor,
)


class _ScriptedAdapter:
    """Returns canned replies in order and records every prompt."""

    def __init__(self, replies: list[str], *, truncated: bool = False) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.truncated = truncated

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LLMResponse(
            text=reply,
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
            truncated=self.truncated,
        )


def _job() -> JobContext:
    return JobContext(
        job_group_id="zzfixture-job",
        title_for_display="Lead Data Engineer",
        company="Gamma",
        description="Own the data platform.",
        skills=[
            JobSkill("s1", "dbt", "must_have"),
            JobSkill("s2", "Kubernetes", "nice_to_have"),
        ],
    )


class TestRenderers(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()

    def test_truth_base_rendering_numbers_roles_and_shows_bullet_ids(self) -> None:
        rendered = render_truth_base(self.truth_base)
        self.assertIn("[0] Senior Data Engineer at Acme Bank (2019-01 – present)", rendered)
        self.assertIn("[1] Data Analyst at Beta Retail (2015-06 – 2018-12)", rendered)
        self.assertIn(
            f"({bullet_id(self.truth_base, 0, 0)}) Built dbt models for risk reporting",
            rendered,
        )
        self.assertIn("Skills: dbt, Airflow, SQL", rendered)

    def test_job_skills_rendering_includes_the_requirement_level(self) -> None:
        rendered = render_job_skills(_job().skills)
        self.assertIn("- dbt (must_have)", rendered)
        self.assertIn("- Kubernetes (nice_to_have)", rendered)

    def test_no_feedback_renders_empty_and_feedback_is_listed(self) -> None:
        self.assertEqual(render_feedback([]), "")
        rendered = render_feedback(["bullet e0b1 has no evidence", "surface SQL"])
        self.assertIn("Fix these problems from your previous attempt", rendered)
        self.assertIn("- bullet e0b1 has no evidence", rendered)
        self.assertIn("- surface SQL", rendered)


class TestRunTailor(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        ref = bullet_id(self.truth_base, 0, 0)
        self.reply = json.dumps(
            {
                "summary": {"text": "Engineer.", "evidence_refs": [ref]},
                "experience": [
                    {
                        "truth_index": 0,
                        "bullets": [{"text": "Built dbt models", "evidence_refs": [ref]}],
                    }
                ],
                "skills": ["dbt"],
            }
        )

    def test_returns_the_parsed_output_and_the_versions_used(self) -> None:
        adapter = _ScriptedAdapter([self.reply])
        result = run_tailor(
            self.truth_base, _job(), [], adapters={"ollama": adapter}
        )
        self.assertEqual(result.output.skills, ["dbt"])
        self.assertEqual(result.prompt_version, "local.v1")
        self.assertEqual(result.model, "llama3.1:8b")

    def test_the_prompt_carries_the_cv_the_job_and_the_feedback(self) -> None:
        adapter = _ScriptedAdapter([self.reply])
        run_tailor(
            self.truth_base,
            _job(),
            ["surface SQL"],
            adapters={"ollama": adapter},
        )
        prompt = adapter.prompts[0]
        self.assertIn("Target job title: Lead Data Engineer", prompt)
        self.assertIn("Own the data platform.", prompt)
        self.assertIn(bullet_id(self.truth_base, 0, 1), prompt)
        self.assertIn("- surface SQL", prompt)
        self.assertNotIn("{cv_text}", prompt)

    def test_a_truncated_reply_raises(self) -> None:
        adapter = _ScriptedAdapter([self.reply], truncated=True)
        with self.assertRaises(TailorOutputError):
            run_tailor(self.truth_base, _job(), [], adapters={"ollama": adapter})

    def test_an_unusable_reply_raises(self) -> None:
        adapter = _ScriptedAdapter(["I cannot help with that."])
        with self.assertRaises(TailorOutputError):
            run_tailor(self.truth_base, _job(), [], adapters={"ollama": adapter})

    def test_the_task_is_registered_in_the_task_config(self) -> None:
        from core.llm.task_config import load_task_config

        self.assertEqual(load_task_config(TASK).task, "cv_tailoring")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_tailor -v`
Expected: `ModuleNotFoundError: No module named 'core.tailoring.tailor'`.

- [ ] **Step 5: Write the implementation**

Create `packages/core/core/tailoring/tailor.py`:

```python
"""The Tailor: renders the truth base and job into a prompt, calls the
`cv_tailoring` LLM task, and parses the reply (Step 17).

The Tailor never sees, and so can never change, companies' titles or
dates as output fields — see core.tailoring.assemble.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.cv.schema import CVTruthBase
from core.llm import gateway
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.tailoring.schema import (
    JobContext,
    JobSkill,
    TailorOutput,
    TailorOutputError,
    parse_tailor_output,
)

TASK = "cv_tailoring"
PROMPT_VERSION_NUMBER = 1
MAX_TOKENS = 4096
"""Reply cap. A reply that hits it comes back truncated and is rejected —
a model stuck repeating itself must not be half-parsed."""


@dataclass(frozen=True)
class TailorResult:
    """One successful Tailor call.

    Attributes:
        output: The parsed instructions.
        model: The model that produced them.
        prompt_version: The prompt file version used, e.g. `local.v1`.
    """

    output: TailorOutput
    model: str
    prompt_version: str


def render_truth_base(truth_base: CVTruthBase) -> str:
    """Render the truth base for the prompt, with roles numbered and every
    bullet's ID shown so the model can cite it.

    Args:
        truth_base: The user's truth base.

    Returns:
        A plain-text rendering.
    """
    lines: list[str] = [f"Headline: {truth_base.headline}"]
    if truth_base.summary:
        lines.append(f"Summary: {truth_base.summary}")
    lines.append("Experience:")
    for index, role in enumerate(truth_base.experience):
        lines.append(
            f"[{index}] {role.title} at {role.company} "
            f"({role.start or '?'} – {role.end or 'present'})"
        )
        for bullet in role.bullets:
            lines.append(f"  - ({bullet.bullet_id}) {bullet.text}")
    lines.append("Skills: " + ", ".join(skill.name for skill in truth_base.skills))
    return "\n".join(lines)


def render_job_skills(job_skills: list[JobSkill]) -> str:
    """Render a job's skills for the prompt.

    Args:
        job_skills: The job's skills.

    Returns:
        One `- label (level)` line per skill, or `- (none listed)`.
    """
    if not job_skills:
        return "- (none listed)"
    return "\n".join(
        f"- {skill.label} ({skill.requirement_level or 'unspecified'})"
        for skill in job_skills
    )


def render_feedback(feedback: list[str]) -> str:
    """Render retry feedback for the prompt.

    Args:
        feedback: Concrete problems from the previous attempt.

    Returns:
        An empty string when there is none, else a block listing them.
    """
    if not feedback:
        return ""
    listed = "\n".join(f"- {item}" for item in feedback)
    return f"\nFix these problems from your previous attempt:\n{listed}\n"


def run_tailor(
    truth_base: CVTruthBase,
    job: JobContext,
    feedback: list[str],
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> TailorResult:
    """Ask the Tailor for per-bullet instructions.

    Args:
        truth_base: The user's truth base.
        job: The target job.
        feedback: Problems from the previous attempt, if any.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).

    Returns:
        The parsed result with the model and prompt version used.

    Raises:
        TailorOutputError: If the reply was truncated or unusable.
    """
    config = load_task_config(TASK, config_path)
    template = load_prompt(TASK, config.prompt_family, PROMPT_VERSION_NUMBER)
    prompt_version = f"{config.prompt_family}.v{PROMPT_VERSION_NUMBER}"
    prompt = template.format(
        cv_text=render_truth_base(truth_base),
        job_title=job.title_for_display or "",
        job_description=job.description,
        job_skills=render_job_skills(job.skills),
        feedback=render_feedback(feedback),
    )
    response = gateway.complete(
        TASK,
        prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        config_path=config_path,
        max_tokens=MAX_TOKENS,
    )
    if response.truncated:
        raise TailorOutputError("the Tailor's reply hit the output cap (truncated)")
    return TailorResult(
        output=parse_tailor_output(response.text),
        model=response.model,
        prompt_version=prompt_version,
    )
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_tailor -v`
Expected: 8 tests OK.

- [ ] **Step 7: Lint and commit**

```bash
arch -arm64 ../../venv/bin/python -m ruff check core/tailoring/tailor.py tests/test_tailoring_tailor.py
arch -arm64 ../../venv/bin/python -m black core/tailoring/tailor.py tests/test_tailoring_tailor.py
arch -arm64 ../../venv/bin/python -m isort core/tailoring/tailor.py tests/test_tailoring_tailor.py
arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_tailor 2>&1 | tail -3
git add ../../config/llm_tasks.yml ../../prompts/cv_tailoring core/tailoring/tailor.py tests/test_tailoring_tailor.py
git commit -m "feat(job_search): Step 17 — the Tailor (cv_tailoring task, prompts)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The fabrication critic (Claude only)

**Files:**
- Create: `prompts/fabrication_critic/claude.v1.md`
- Create: `packages/core/core/tailoring/critic.py`
- Test: `packages/core/tests/test_tailoring_critic.py`

**Interfaces:**
- Consumes: `core.llm.gateway.complete`, `load_task_config`, Task 2/3 models, `bullet_index`.
- Produces (`core.tailoring.critic`):
  - `TASK = "fabrication_critic"`, `REQUIRED_PROVIDER = "anthropic"`
  - `CriticConfigError(RuntimeError)`, `CriticError(RuntimeError)`
  - `CriticItem(item_id: str, text: str, sources: list[str])` frozen dataclass
  - `Verdict(item_id: str, supported: bool, issue: str)` frozen dataclass
  - `CriticResult(verdicts: dict[str, Verdict], stretch: StretchAssessment, model: str, prompt_version: str)` frozen dataclass
  - `assert_critic_provider(config_path: Path | None = None) -> None`
  - `critic_items(document, truth_base) -> list[CriticItem]` — item ids `"summary"` and `"e{role}b{bullet}"`; only `reworded` lines.
  - `run_critic(document, truth_base, job, *, adapters, config_path=None) -> CriticResult`

Behaviours pinned (Review Focus 2): a missing verdict = unsupported ("the critic gave no verdict"); an unparseable reply raises `CriticError`; a non-Anthropic route raises `CriticConfigError` before any call.

- [ ] **Step 1: Write the critic prompt**

Create `prompts/fabrication_critic/claude.v1.md`:

```
You are a strict fact-checker for a tailored CV. A candidate's real employment history is below; nothing outside it is true.

Role history (the only roles that exist):
{role_history}

Target job title: {job_title}

Below is a JSON list of generated CV lines. Each has an id, the generated text, and "sources": the original bullet text(s) it was derived from.
{items}

For EACH item decide whether EVERYTHING the generated text claims follows from its sources. Rewording, reordering and emphasis are fine. An item is NOT supported if it adds any metric, number, percentage, technology, tool, scale, team size, ownership, seniority, scope or outcome that its sources do not state.

Also judge whether the target job title is a stretch: true when it implies seniority or scope (for example "Head of", management of people, or a more senior level) that the role history does not evidence. This is advisory.

Respond with ONLY a JSON object, no other text:
{{"verdicts": [{{"id": "<item id>", "supported": <true or false>, "issue": "<the unsupported claim, or empty if supported>"}}, ...], "stretch": {{"is_stretch": <true or false>, "reason": "<one sentence>"}}}}
```

- [ ] **Step 2: Write the failing test**

Create `packages/core/tests/test_tailoring_critic.py`:

```python
"""Unit tests for the fabrication critic."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.llm.types import LLMResponse
from core.tailoring.assemble import assemble
from core.tailoring.critic import (
    REQUIRED_PROVIDER,
    TASK,
    CriticConfigError,
    CriticError,
    assert_critic_provider,
    critic_items,
    run_critic,
)
from core.tailoring.schema import (
    JobContext,
    TailorBullet,
    TailorExperience,
    TailorOutput,
    TailorSummary,
)


class _Adapter:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(
            text=self.reply,
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


def _job() -> JobContext:
    return JobContext(
        job_group_id="zzfixture-job",
        title_for_display="Head of Data",
        company="Gamma",
        description="Lead the data function.",
        skills=[],
    )


class TestCriticProvider(unittest.TestCase):
    def test_the_real_config_routes_the_critic_to_anthropic(self) -> None:
        # Asserted against config/llm_tasks.yml so config drift cannot
        # silently downgrade the guard (DECISIONS.md §1).
        assert_critic_provider()
        from core.llm.task_config import load_task_config

        self.assertEqual(load_task_config(TASK).provider, REQUIRED_PROVIDER)

    def test_a_non_anthropic_route_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.yml"
            path.write_text(
                "tasks:\n  fabrication_critic:\n    provider: ollama\n"
                "    model: llama3.1:8b\n    prompt_family: local\n"
            )
            with self.assertRaises(CriticConfigError):
                assert_critic_provider(path)

    def test_run_critic_refuses_before_calling_a_weaker_model(self) -> None:
        truth_base = make_truth_base()
        document = assemble(truth_base, TailorOutput(), target_title="T")
        adapter = _Adapter("{}")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.yml"
            path.write_text(
                "tasks:\n  fabrication_critic:\n    provider: ollama\n"
                "    model: llama3.1:8b\n    prompt_family: claude\n"
            )
            with self.assertRaises(CriticConfigError):
                run_critic(
                    document,
                    truth_base,
                    _job(),
                    adapters={"ollama": adapter},
                    config_path=path,
                )
        self.assertEqual(adapter.prompts, [])


class TestCriticItems(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        self.ref1 = bullet_id(self.truth_base, 0, 1)

    def test_only_reworded_lines_are_judged_and_they_carry_their_sources(self) -> None:
        output = TailorOutput(
            summary=TailorSummary(text="Pipeline specialist.", evidence_refs=[self.ref0]),
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[
                        TailorBullet(
                            text="Migrated nightly batch jobs to Airflow",
                            evidence_refs=[self.ref1],
                        ),
                        TailorBullet(
                            text="Built dbt models powering risk reporting",
                            evidence_refs=[self.ref0],
                        ),
                        TailorBullet(text="Led a team of 12", evidence_refs=[]),
                    ],
                )
            ],
        )
        document = assemble(self.truth_base, output, target_title="T")
        items = critic_items(document, self.truth_base)
        self.assertEqual([i.item_id for i in items], ["summary", "e0b1"])
        self.assertEqual(items[1].sources, ["Built dbt models for risk reporting"])


class TestRunCritic(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        output = TailorOutput(
            experience=[
                TailorExperience(
                    truth_index=0,
                    bullets=[
                        TailorBullet(
                            text="Built dbt models powering risk reporting",
                            evidence_refs=[self.ref0],
                        ),
                        TailorBullet(
                            text="Led 12 engineers, cutting costs 40%",
                            evidence_refs=[self.ref0],
                        ),
                    ],
                )
            ]
        )
        self.document = assemble(self.truth_base, output, target_title="Head of Data")

    def _reply(self, verdicts: list[dict], stretch: dict | None = None) -> str:
        return json.dumps(
            {
                "verdicts": verdicts,
                "stretch": stretch or {"is_stretch": False, "reason": ""},
            }
        )

    def test_parses_verdicts_and_stretch(self) -> None:
        adapter = _Adapter(
            self._reply(
                [
                    {"id": "e0b0", "supported": True, "issue": ""},
                    {"id": "e0b1", "supported": False, "issue": "adds 12 engineers"},
                ],
                {"is_stretch": True, "reason": "Head-of implies managing people"},
            )
        )
        result = run_critic(
            self.document, self.truth_base, _job(), adapters={"anthropic": adapter}
        )
        self.assertTrue(result.verdicts["e0b0"].supported)
        self.assertFalse(result.verdicts["e0b1"].supported)
        self.assertEqual(result.verdicts["e0b1"].issue, "adds 12 engineers")
        self.assertTrue(result.stretch.is_stretch)
        self.assertEqual(result.prompt_version, "claude.v1")

    def test_a_missing_verdict_fails_closed_as_unsupported(self) -> None:
        # Review Focus 2: an item the critic did not answer is not approved.
        adapter = _Adapter(
            self._reply([{"id": "e0b0", "supported": True, "issue": ""}])
        )
        result = run_critic(
            self.document, self.truth_base, _job(), adapters={"anthropic": adapter}
        )
        self.assertFalse(result.verdicts["e0b1"].supported)
        self.assertIn("no verdict", result.verdicts["e0b1"].issue)

    def test_verdicts_for_unknown_ids_are_ignored(self) -> None:
        adapter = _Adapter(
            self._reply(
                [
                    {"id": "e0b0", "supported": True, "issue": ""},
                    {"id": "e0b1", "supported": True, "issue": ""},
                    {"id": "e9b9", "supported": False, "issue": "ghost"},
                ]
            )
        )
        result = run_critic(
            self.document, self.truth_base, _job(), adapters={"anthropic": adapter}
        )
        self.assertNotIn("e9b9", result.verdicts)

    def test_an_unparseable_reply_raises(self) -> None:
        adapter = _Adapter("looks fine to me!")
        with self.assertRaises(CriticError):
            run_critic(
                self.document, self.truth_base, _job(), adapters={"anthropic": adapter}
            )

    def test_the_prompt_carries_items_sources_and_the_target_title(self) -> None:
        adapter = _Adapter(self._reply([]))
        run_critic(
            self.document, self.truth_base, _job(), adapters={"anthropic": adapter}
        )
        prompt = adapter.prompts[0]
        self.assertIn("Target job title: Head of Data", prompt)
        self.assertIn('"id": "e0b1"', prompt)
        self.assertIn("Built dbt models for risk reporting", prompt)
        self.assertIn("Senior Data Engineer at Acme Bank", prompt)

    def test_with_nothing_to_judge_it_still_assesses_the_stretch(self) -> None:
        document = assemble(self.truth_base, TailorOutput(), target_title="Head of Data")
        adapter = _Adapter(
            self._reply([], {"is_stretch": True, "reason": "senior title"})
        )
        result = run_critic(
            document, self.truth_base, _job(), adapters={"anthropic": adapter}
        )
        self.assertEqual(result.verdicts, {})
        self.assertTrue(result.stretch.is_stretch)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_critic -v`
Expected: `ModuleNotFoundError: No module named 'core.tailoring.critic'`.

- [ ] **Step 4: Write the implementation**

Create `packages/core/core/tailoring/critic.py`:

```python
"""The fabrication critic (Step 17).

Runs on the target provider (Claude) from day one even while the Tailor
runs local: a safety guard validated on a weaker model than the one
running it is worse than no guard (DECISIONS.md §1). This module refuses
to run if `fabrication_critic` is routed anywhere else, and the test
suite asserts the same against the real config.

The critic covers only the semantic gap — whether a reworded line's
claims follow from its cited sources. Everything checkable in code is in
core.tailoring.checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from core.cv.schema import CVTruthBase
from core.llm import gateway
from core.llm.json_response import parse_json_response
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.tailoring.assemble import bullet_index
from core.tailoring.schema import JobContext, StretchAssessment, TailoredDocument

TASK = "fabrication_critic"
REQUIRED_PROVIDER = "anthropic"
PROMPT_VERSION_NUMBER = 1


class CriticConfigError(RuntimeError):
    """The critic is routed to a provider other than Anthropic."""


class CriticError(RuntimeError):
    """The critic's reply could not be used."""


@dataclass(frozen=True)
class CriticItem:
    """One generated line for the critic to judge.

    Attributes:
        item_id: `summary` or `e{role}b{bullet}`.
        text: The generated text.
        sources: The truth-base bullet text(s) it was derived from.
    """

    item_id: str
    text: str
    sources: list[str]


@dataclass(frozen=True)
class Verdict:
    """The critic's judgement of one item.

    Attributes:
        item_id: Which item.
        supported: Whether everything it claims follows from its sources.
        issue: What is unsupported; empty when supported.
    """

    item_id: str
    supported: bool
    issue: str


@dataclass(frozen=True)
class CriticResult:
    """One critic call's outcome.

    Attributes:
        verdicts: A verdict for every judged item (missing answers are
            filled in as unsupported).
        stretch: The seniority/scope judgement of the target title.
        model: The model that answered.
        prompt_version: The prompt file version used, e.g. `claude.v1`.
    """

    verdicts: dict[str, Verdict]
    stretch: StretchAssessment
    model: str
    prompt_version: str


def assert_critic_provider(config_path: Path | None = None) -> None:
    """Refuse to proceed unless the critic is routed to Anthropic.

    Args:
        config_path: Task-config override (tests).

    Raises:
        CriticConfigError: If `fabrication_critic` resolves elsewhere.
    """
    provider = load_task_config(TASK, config_path).provider
    if provider != REQUIRED_PROVIDER:
        raise CriticConfigError(
            f"{TASK} must run on {REQUIRED_PROVIDER!r} (a guard validated on a "
            f"weaker model gives false confidence), but is routed to {provider!r}"
        )


def critic_items(
    document: TailoredDocument, truth_base: CVTruthBase
) -> list[CriticItem]:
    """List the lines the critic must judge: every `reworded` line.

    `original` lines are the user's own text; `orphan` lines are already
    surfaced; `linked` lines were decided by the user.

    Args:
        document: The assembled document.
        truth_base: The truth base (the source of each line's evidence).

    Returns:
        The items, summary first, then bullets in document order.
    """
    known = bullet_index(truth_base)

    def sources(refs: list[str]) -> list[str]:
        return [known[ref][1] for ref in refs if ref in known]

    items: list[CriticItem] = []
    if document.summary is not None and document.summary.origin == "reworded":
        items.append(
            CriticItem(
                "summary", document.summary.text, sources(document.summary.evidence_refs)
            )
        )
    for role in document.experience:
        for position, bullet in enumerate(role.bullets):
            if bullet.origin == "reworded":
                items.append(
                    CriticItem(
                        f"e{role.truth_index}b{position}",
                        bullet.text,
                        sources(bullet.evidence_refs),
                    )
                )
    return items


def _render_role_history(truth_base: CVTruthBase) -> str:
    """Render the roles (no bullets) the critic compares the title against.

    Args:
        truth_base: The truth base.

    Returns:
        One line per role.
    """
    return "\n".join(
        f"[{index}] {role.title} at {role.company} "
        f"({role.start or '?'} – {role.end or 'present'})"
        for index, role in enumerate(truth_base.experience)
    )


def run_critic(
    document: TailoredDocument,
    truth_base: CVTruthBase,
    job: JobContext,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> CriticResult:
    """Judge a tailored document's reworded lines and the title's stretch.

    Args:
        document: The assembled document.
        truth_base: The truth base.
        job: The target job.
        adapters: LLM adapters keyed by provider; must include `anthropic`.
        config_path: Task-config override (tests).

    Returns:
        The critic's verdicts. An item the critic did not answer is
        recorded as unsupported (fail closed).

    Raises:
        CriticConfigError: If the critic is not routed to Anthropic.
        CriticError: If the reply is not usable JSON of the expected shape.
    """
    assert_critic_provider(config_path)
    config = load_task_config(TASK, config_path)
    template = load_prompt(TASK, config.prompt_family, PROMPT_VERSION_NUMBER)
    prompt_version = f"{config.prompt_family}.v{PROMPT_VERSION_NUMBER}"
    items = critic_items(document, truth_base)
    prompt = template.format(
        role_history=_render_role_history(truth_base),
        job_title=document.target_title,
        items=json.dumps(
            [
                {"id": i.item_id, "text": i.text, "sources": i.sources}
                for i in items
            ],
            indent=2,
        ),
    )
    response = gateway.complete(
        TASK,
        prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        config_path=config_path,
    )
    try:
        data = parse_json_response(response.text.strip())
        answered = {
            str(entry["id"]): Verdict(
                item_id=str(entry["id"]),
                supported=bool(entry["supported"]),
                issue=str(entry.get("issue", "")),
            )
            for entry in data.get("verdicts", [])
        }
        stretch = StretchAssessment.model_validate(data.get("stretch") or {})
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        ValidationError,
    ) as exc:
        raise CriticError(f"unusable critic reply: {exc}") from exc

    verdicts = {
        item.item_id: answered.get(
            item.item_id,
            Verdict(item.item_id, False, "the critic gave no verdict for this line"),
        )
        for item in items
    }
    return CriticResult(
        verdicts=verdicts,
        stretch=stretch,
        model=response.model,
        prompt_version=prompt_version,
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_critic -v`
Expected: 9 tests OK.

- [ ] **Step 6: Lint and commit**

```bash
arch -arm64 ../../venv/bin/python -m ruff check core/tailoring/critic.py tests/test_tailoring_critic.py
arch -arm64 ../../venv/bin/python -m black core/tailoring/critic.py tests/test_tailoring_critic.py
arch -arm64 ../../venv/bin/python -m isort core/tailoring/critic.py tests/test_tailoring_critic.py
arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_critic 2>&1 | tail -3
git add ../../prompts/fabrication_critic core/tailoring/critic.py tests/test_tailoring_critic.py
git commit -m "feat(job_search): Step 17 — the Claude-only fabrication critic

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Job context and candidate queries

**Files:**
- Create: `packages/core/core/tailoring/context.py`
- Test: `packages/core/tests/integration/test_tailoring_context.py`

**Interfaces:**
- Consumes: Task 2 `JobContext`/`JobSkill`; `core.db.session.session_scope`; tables `gold.dim_job`, `silver.silver__bridge_job_skill`, `silver.silver__skill`, `scoring.job_score`, `tailoring.tailored_cv`.
- Produces (`core.tailoring.context`):
  - `load_job_context(engine: Engine, job_group_id: str) -> JobContext | None` — skills ordered `must_have` first, then by `mention_count` desc, then label; label falls back to the skill id when no `silver__skill` row exists.
  - `CandidateJob(job_group_id: str, title_for_display: str | None, company: str | None, final_score: float, latest_run_id: uuid.UUID | None, latest_status: str | None)` frozen dataclass
  - `list_candidates(engine: Engine, user_id: uuid.UUID, *, limit: int = 25) -> list[CandidateJob]` — the user's `hard_filter_passed` jobs with a non-NULL `final_score`, highest first, each with the user's latest tailoring run (if any).

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_tailoring_context.py`:

```python
"""Integration tests for tailoring's job-context and candidate queries."""

from __future__ import annotations

import time
import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.db.session import session_scope
from core.tailoring.context import list_candidates, load_job_context

_PREFIX = "zzfixture-tlr-ctx"


class TestTailoringContext(unittest.TestCase):
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
                        "VALUES (:id, :email, 'zzfixture ctx user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )
            for suffix, title, company in (
                ("1", "Lead Data Engineer", "Gamma"),
                ("2", "Analytics Engineer", "Delta"),
                ("3", "Data Scientist", "Eps"),
                ("4", None, "NoTitleCo"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO gold.dim_job "
                        "(job_group_id, title_for_display, company, description) "
                        "VALUES (:id, :title, :company, :description)"
                    ),
                    {
                        "id": f"{_PREFIX}-{suffix}",
                        "title": title,
                        "company": company,
                        "description": f"description {suffix}",
                    },
                )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__skill "
                    "(skill_id, canonical_label, source) VALUES "
                    "('zzfixture-sk-dbt', 'dbt', 'custom')"
                )
            )
            for skill_id, level, mentions in (
                ("zzfixture-sk-dbt", "nice_to_have", 5),
                ("zzfixture-sk-k8s", "must_have", 1),
                ("zzfixture-sk-aws", "must_have", 3),
            ):
                conn.execute(
                    text(
                        "INSERT INTO silver.silver__bridge_job_skill "
                        "(job_group_id, skill_id, requirement_level, mention_count) "
                        "VALUES (:job, :skill, :level, :mentions)"
                    ),
                    {
                        "job": f"{_PREFIX}-1",
                        "skill": skill_id,
                        "level": level,
                        "mentions": mentions,
                    },
                )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM tailoring.tailored_cv WHERE user_id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__bridge_job_skill "
                    "WHERE job_group_id LIKE :p"
                ),
                {"p": f"{_PREFIX}-%"},
            )
            conn.execute(
                text("DELETE FROM silver.silver__skill WHERE skill_id LIKE 'zzfixture-sk-%'")
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE :p"),
                {"p": f"{_PREFIX}-%"},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user_a, "b": self.user_b},
            )

    def _score(self, user_id, suffix: str, score, passed: bool = True) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score "
                    "(user_id, job_group_id, hard_filter_passed, final_score) "
                    "VALUES (:u, :j, :passed, :score)"
                ),
                {"u": user_id, "j": f"{_PREFIX}-{suffix}", "passed": passed, "score": score},
            )

    def test_load_job_context_returns_title_company_description(self) -> None:
        context = load_job_context(self.app_engine, f"{_PREFIX}-1")
        self.assertEqual(context.title_for_display, "Lead Data Engineer")
        self.assertEqual(context.company, "Gamma")
        self.assertEqual(context.description, "description 1")

    def test_skills_are_ordered_must_have_first_then_by_mentions(self) -> None:
        context = load_job_context(self.app_engine, f"{_PREFIX}-1")
        self.assertEqual(
            [(s.skill_id, s.requirement_level) for s in context.skills],
            [
                ("zzfixture-sk-aws", "must_have"),
                ("zzfixture-sk-k8s", "must_have"),
                ("zzfixture-sk-dbt", "nice_to_have"),
            ],
        )

    def test_a_skill_label_comes_from_silver_skill_or_falls_back_to_the_id(self) -> None:
        labels = {
            s.skill_id: s.label
            for s in load_job_context(self.app_engine, f"{_PREFIX}-1").skills
        }
        self.assertEqual(labels["zzfixture-sk-dbt"], "dbt")
        self.assertEqual(labels["zzfixture-sk-k8s"], "zzfixture-sk-k8s")

    def test_an_unknown_job_returns_none(self) -> None:
        self.assertIsNone(load_job_context(self.app_engine, f"{_PREFIX}-nope"))

    def test_a_job_with_no_title_loads_with_a_none_title(self) -> None:
        context = load_job_context(self.app_engine, f"{_PREFIX}-4")
        self.assertIsNone(context.title_for_display)

    def test_candidates_are_the_users_passed_jobs_best_first(self) -> None:
        self._score(self.user_a, "1", 0.4)
        self._score(self.user_a, "2", 0.9)
        self._score(self.user_a, "3", 0.99, passed=False)
        self._score(self.user_a, "4", None)
        candidates = [
            c
            for c in list_candidates(self.app_engine, self.user_a, limit=500)
            if c.job_group_id.startswith(_PREFIX)
        ]
        self.assertEqual(
            [c.job_group_id for c in candidates], [f"{_PREFIX}-2", f"{_PREFIX}-1"]
        )
        self.assertEqual(candidates[0].title_for_display, "Analytics Engineer")
        self.assertAlmostEqual(candidates[0].final_score, 0.9)

    def test_candidates_do_not_include_another_users_scores(self) -> None:
        self._score(self.user_a, "1", 0.4)
        mine = [
            c
            for c in list_candidates(self.app_engine, self.user_b, limit=500)
            if c.job_group_id.startswith(_PREFIX)
        ]
        self.assertEqual(mine, [])

    def test_candidates_respect_the_limit(self) -> None:
        self._score(self.user_a, "1", 0.4)
        self._score(self.user_a, "2", 0.9)
        self.assertEqual(len(list_candidates(self.app_engine, self.user_a, limit=1)), 1)

    def test_a_candidate_reports_the_latest_run(self) -> None:
        self._score(self.user_a, "1", 0.4)
        # One transaction per insert: now() is fixed for a transaction, so
        # two inserts in one would share a created_at and the "latest" run
        # would be ambiguous.
        for status in ("failed", "needs_review"):
            with session_scope(self.app_engine, user_id=self.user_a) as conn:
                conn.execute(
                    text(
                        "INSERT INTO tailoring.tailored_cv "
                        "(user_id, job_group_id, truth_base_version, target_title, "
                        "status) VALUES (:u, :j, 1, 'T', :s)"
                    ),
                    {"u": self.user_a, "j": f"{_PREFIX}-1", "s": status},
                )
            time.sleep(0.05)
        candidate = next(
            c
            for c in list_candidates(self.app_engine, self.user_a, limit=500)
            if c.job_group_id == f"{_PREFIX}-1"
        )
        self.assertEqual(candidate.latest_status, "needs_review")
        self.assertIsNotNone(candidate.latest_run_id)

    def test_a_candidate_with_no_run_has_none(self) -> None:
        self._score(self.user_a, "1", 0.4)
        candidate = next(
            c
            for c in list_candidates(self.app_engine, self.user_a, limit=500)
            if c.job_group_id == f"{_PREFIX}-1"
        )
        self.assertIsNone(candidate.latest_run_id)
        self.assertIsNone(candidate.latest_status)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_context -v`
Expected: `ModuleNotFoundError: No module named 'core.tailoring.context'`.

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/tailoring/context.py`:

```python
"""Database reads for tailoring: the target job's context and the user's
top-scored candidate jobs (Step 17)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.tailoring.schema import JobContext, JobSkill

_SELECT_JOB = text(
    "SELECT job_group_id, title_for_display, company, description "
    "FROM gold.dim_job WHERE job_group_id = :job_group_id"
)
# `must_have` first (requirement_level = 'must_have' is true for them and
# sorts first under DESC), then the most-mentioned, then alphabetical so
# the order is stable. The label falls back to the skill id when the
# skill has no silver__skill row, so a job is never left with an
# unlabelled skill.
_SELECT_JOB_SKILLS = text(
    "SELECT b.skill_id, COALESCE(s.canonical_label, b.skill_id) AS label, "
    "b.requirement_level "
    "FROM silver.silver__bridge_job_skill AS b "
    "LEFT JOIN silver.silver__skill AS s ON s.skill_id = b.skill_id "
    "WHERE b.job_group_id = :job_group_id "
    "ORDER BY (b.requirement_level = 'must_have') DESC NULLS LAST, "
    "b.mention_count DESC NULLS LAST, label"
)
# The user's latest tailoring run per job comes from a LATERAL subquery;
# both tailoring.tailored_cv and scoring.job_score are RLS-scoped, so this
# must run inside session_scope for the user.
_SELECT_CANDIDATES = text(
    "SELECT js.job_group_id, d.title_for_display, d.company, js.final_score, "
    "t.id AS latest_run_id, t.status AS latest_status "
    "FROM scoring.job_score AS js "
    "JOIN gold.dim_job AS d ON d.job_group_id = js.job_group_id "
    "LEFT JOIN LATERAL ("
    "  SELECT id, status FROM tailoring.tailored_cv "
    "  WHERE user_id = js.user_id AND job_group_id = js.job_group_id "
    "  ORDER BY created_at DESC LIMIT 1"
    ") AS t ON true "
    "WHERE js.user_id = :user_id AND js.hard_filter_passed = true "
    "AND js.final_score IS NOT NULL "
    "ORDER BY js.final_score DESC, js.job_group_id "
    "LIMIT :limit"
)


@dataclass(frozen=True)
class CandidateJob:
    """One job the user could tailor a CV for.

    Attributes:
        job_group_id: The job's id.
        title_for_display: The job's display title (may be None).
        company: The employer (may be None).
        final_score: The user's blended score for the job.
        latest_run_id: The user's most recent tailoring run for it, if any.
        latest_status: That run's status, if any.
    """

    job_group_id: str
    title_for_display: str | None
    company: str | None
    final_score: float
    latest_run_id: uuid.UUID | None
    latest_status: str | None


def load_job_context(engine: Engine, job_group_id: str) -> JobContext | None:
    """Load what the Tailor and critic need to know about a job.

    Args:
        engine: The app-role engine.
        job_group_id: The job to load.

    Returns:
        The `JobContext`, or None when no such job exists. A job whose
        `title_for_display` is NULL loads with that field None — callers
        must treat that as "cannot be tailored".
    """
    with engine.connect() as conn:
        job = conn.execute(_SELECT_JOB, {"job_group_id": job_group_id}).one_or_none()
        if job is None:
            return None
        skills = [
            JobSkill(
                skill_id=row.skill_id,
                label=row.label,
                requirement_level=row.requirement_level,
            )
            for row in conn.execute(_SELECT_JOB_SKILLS, {"job_group_id": job_group_id})
        ]
    return JobContext(
        job_group_id=job.job_group_id,
        title_for_display=job.title_for_display,
        company=job.company,
        description=job.description or "",
        skills=skills,
    )


def list_candidates(
    engine: Engine, user_id: uuid.UUID, *, limit: int = 25
) -> list[CandidateJob]:
    """List a user's top-scored jobs, best first.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose scores to read.
        limit: Maximum jobs to return.

    Returns:
        Jobs that passed the hard filters and have a final score, each with
        the user's latest tailoring run for it, if any.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            _SELECT_CANDIDATES, {"user_id": user_id, "limit": limit}
        ).all()
    return [
        CandidateJob(
            job_group_id=row.job_group_id,
            title_for_display=row.title_for_display,
            company=row.company,
            final_score=float(row.final_score),
            latest_run_id=row.latest_run_id,
            latest_status=row.latest_status,
        )
        for row in rows
    ]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_context -v`
Expected: 9 tests OK.

- [ ] **Step 5: Lint and commit**

```bash
arch -arm64 ../../venv/bin/python -m ruff check core/tailoring/context.py tests/integration/test_tailoring_context.py
arch -arm64 ../../venv/bin/python -m black core/tailoring/context.py tests/integration/test_tailoring_context.py
arch -arm64 ../../venv/bin/python -m isort core/tailoring/context.py tests/integration/test_tailoring_context.py
arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_context 2>&1 | tail -3
git add core/tailoring/context.py tests/integration/test_tailoring_context.py
git commit -m "feat(job_search): Step 17 — job context and candidate queries

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Orphan decisions (pure logic) and run persistence

**Files:**
- Create: `packages/core/core/tailoring/decisions.py`
- Create: `packages/core/core/tailoring/store.py`
- Test: `packages/core/tests/test_tailoring_decisions.py`
- Test: `packages/core/tests/integration/test_tailoring_store.py`

**Interfaces:**
- Consumes: Task 2 models, Task 3 `bullet_index`, Task 1 tables.
- Produces (`core.tailoring.store`):
  - `OrphanDraft(kind: str, section: str, experience_index: int | None, bullet_index: int | None, text: str, claimed_refs: list[str], issue: str | None)` frozen dataclass
  - `StoredOrphan(id: uuid.UUID, tailored_cv_id: uuid.UUID, kind: str, section: str, experience_index: int | None, bullet_index: int | None, text: str, claimed_refs: list[str], issue: str | None, status: str, evidence_ref: str | None)` frozen dataclass
  - `StoredRun(id, user_id, job_group_id, truth_base_version, target_title, status, attempts, error_message, document: TailoredDocument | None, tailor_model, tailor_prompt_version, critic_model, critic_prompt_version, created_at, orphans: list[StoredOrphan])` frozen dataclass
  - `create_run(engine, user_id, *, job_group_id: str, truth_base_version: int, target_title: str) -> uuid.UUID`
  - `finish_run(engine, user_id, run_id, *, status: str, document: TailoredDocument | None, orphans: list[OrphanDraft], attempts: int, tailor_model: str | None = None, tailor_prompt_version: str | None = None, critic_model: str | None = None, critic_prompt_version: str | None = None, error_message: str | None = None) -> None`
  - `read_run(engine, user_id, run_id) -> StoredRun | None`
  - `read_orphan(engine, user_id, orphan_id) -> StoredOrphan | None`
  - `save_decision(engine, user_id, *, orphan: StoredOrphan, status: str, evidence_ref: str | None, document: TailoredDocument, removed_position: tuple[int, int] | None) -> str` — returns the run's new status (`approved` when no orphan is left pending, else `needs_review`).
- Produces (`core.tailoring.decisions`):
  - `DecisionError(ValueError)`
  - `DecisionResult(document: TailoredDocument, removed_position: tuple[int, int] | None)` frozen dataclass
  - `apply_decision(document, orphan: StoredOrphan, *, action: str, evidence_ref: str | None, truth_base: CVTruthBase) -> DecisionResult` — `action` is `"link"` or `"reject"`.

Rules (spec "Orphan decisions"): **link** requires an `evidence_ref` that exists in the truth base and — for an experience orphan — belongs to the *same role*; sets `evidence_refs=[ref]`, `origin="linked"`, text unchanged. **reject** drops the line, except an `unsupported` experience bullet citing exactly one valid source reverts to that source bullet's original text (`origin="original"`); for the summary, reject restores the truth-base summary as `original` (or removes it when the truth base has none). A decision on a bullet whose current text differs from the orphan's recorded text raises (the document changed since). Removing a bullet shifts later bullet indexes of the same role, so `save_decision` decrements `bullet_index` of the run's other orphans in that role.

- [ ] **Step 1: Write the failing decisions test**

Create `packages/core/tests/test_tailoring_decisions.py`:

```python
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

    def test_reject_of_an_unsupported_bullet_without_one_source_removes_it(self) -> None:
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_decisions -v`
Expected: `ModuleNotFoundError` (`core.tailoring.decisions`/`store`).

- [ ] **Step 3: Write `store.py` (dataclasses first; functions after the next step's test)**

Create `packages/core/core/tailoring/store.py`:

```python
"""Persistence for tailoring runs and their orphan bullets (Step 17).

Every function takes the app-role engine and a `user_id` and runs inside
`session_scope`, so both tables' RLS applies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, Engine, text

from core.db.session import session_scope
from core.tailoring.schema import TailoredDocument


@dataclass(frozen=True)
class OrphanDraft:
    """A line the loop could not trace, about to be saved for a decision.

    Attributes:
        kind: `orphan` (no valid evidence ref) or `unsupported` (the critic
            rejected a reworded line).
        section: `summary` or `experience`.
        experience_index: The role, for an experience line.
        bullet_index: The line's position in that role.
        text: The line's text at the time of the run.
        claimed_refs: The `bullet_id`s the Tailor cited.
        issue: What is wrong, in words.
    """

    kind: str
    section: str
    experience_index: int | None
    bullet_index: int | None
    text: str
    claimed_refs: list[str]
    issue: str | None


@dataclass(frozen=True)
class StoredOrphan:
    """A saved orphan row.

    Attributes:
        id: Row id.
        tailored_cv_id: The run it belongs to.
        kind: `orphan` or `unsupported`.
        section: `summary` or `experience`.
        experience_index: The role, for an experience line.
        bullet_index: The line's position in that role.
        text: The line's text when recorded.
        claimed_refs: The `bullet_id`s the Tailor cited.
        issue: What is wrong, in words.
        status: `pending`, `linked` or `rejected`.
        evidence_ref: The truth-base bullet it was linked to, if any.
    """

    id: uuid.UUID
    tailored_cv_id: uuid.UUID
    kind: str
    section: str
    experience_index: int | None
    bullet_index: int | None
    text: str
    claimed_refs: list[str]
    issue: str | None
    status: str
    evidence_ref: str | None


@dataclass(frozen=True)
class StoredRun:
    """A saved tailoring run.

    Attributes:
        id: Run id.
        user_id: The owner.
        job_group_id: The target job.
        truth_base_version: The CV version it was built from.
        target_title: The injected title.
        status: `generating`, `needs_review`, `approved` or `failed`.
        attempts: Tailor attempts made.
        error_message: Why it failed, if it did.
        document: The assembled document, once there is one.
        tailor_model: Model used by the Tailor.
        tailor_prompt_version: Tailor prompt version.
        critic_model: Model used by the critic.
        critic_prompt_version: Critic prompt version.
        created_at: When the run started.
        orphans: Every orphan row, in position order.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    job_group_id: str
    truth_base_version: int
    target_title: str
    status: str
    attempts: int
    error_message: str | None
    document: TailoredDocument | None
    tailor_model: str | None
    tailor_prompt_version: str | None
    critic_model: str | None
    critic_prompt_version: str | None
    created_at: datetime
    orphans: list[StoredOrphan]


_ORPHAN_COLUMNS = (
    "id, tailored_cv_id, kind, section, experience_index, bullet_index, text, "
    "claimed_refs, issue, status, evidence_ref"
)


def _orphan_from_row(row: object) -> StoredOrphan:
    """Build a `StoredOrphan` from a result row.

    Args:
        row: A row selected with `_ORPHAN_COLUMNS`.

    Returns:
        The dataclass.
    """
    return StoredOrphan(
        id=row.id,
        tailored_cv_id=row.tailored_cv_id,
        kind=row.kind,
        section=row.section,
        experience_index=row.experience_index,
        bullet_index=row.bullet_index,
        text=row.text,
        claimed_refs=list(row.claimed_refs or []),
        issue=row.issue,
        status=row.status,
        evidence_ref=row.evidence_ref,
    )


def create_run(
    engine: Engine,
    user_id: uuid.UUID,
    *,
    job_group_id: str,
    truth_base_version: int,
    target_title: str,
) -> uuid.UUID:
    """Insert a new run in `generating` status.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        job_group_id: The target job.
        truth_base_version: The CV version the run will use.
        target_title: The injected title.

    Returns:
        The new run's id.
    """
    with session_scope(engine, user_id=user_id) as conn:
        return conn.execute(
            text(
                "INSERT INTO tailoring.tailored_cv "
                "(user_id, job_group_id, truth_base_version, target_title) "
                "VALUES (:user_id, :job_group_id, :version, :title) RETURNING id"
            ),
            {
                "user_id": user_id,
                "job_group_id": job_group_id,
                "version": truth_base_version,
                "title": target_title,
            },
        ).scalar_one()


def finish_run(
    engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    status: str,
    document: TailoredDocument | None,
    orphans: list[OrphanDraft],
    attempts: int,
    tailor_model: str | None = None,
    tailor_prompt_version: str | None = None,
    critic_model: str | None = None,
    critic_prompt_version: str | None = None,
    error_message: str | None = None,
) -> None:
    """Record a run's outcome and its orphan rows in one transaction.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        run_id: The run to finish.
        status: `needs_review`, `approved` or `failed`.
        document: The assembled document, or None for a failed run.
        orphans: The lines awaiting a decision.
        attempts: Tailor attempts made.
        tailor_model: Model used by the Tailor.
        tailor_prompt_version: Tailor prompt version.
        critic_model: Model used by the critic.
        critic_prompt_version: Critic prompt version.
        error_message: Why the run failed, if it did.
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "UPDATE tailoring.tailored_cv SET status = :status, "
                "content = CAST(:content AS jsonb), attempts = :attempts, "
                "stretch = CAST(:stretch AS jsonb), tailor_model = :tailor_model, "
                "tailor_prompt_version = :tailor_prompt_version, "
                "critic_model = :critic_model, "
                "critic_prompt_version = :critic_prompt_version, "
                "error_message = :error_message, updated_at = now() "
                "WHERE id = :run_id AND user_id = :user_id"
            ),
            {
                "status": status,
                "content": document.model_dump_json() if document else None,
                "attempts": attempts,
                "stretch": document.stretch.model_dump_json() if document else None,
                "tailor_model": tailor_model,
                "tailor_prompt_version": tailor_prompt_version,
                "critic_model": critic_model,
                "critic_prompt_version": critic_prompt_version,
                "error_message": error_message,
                "run_id": run_id,
                "user_id": user_id,
            },
        )
        for draft in orphans:
            conn.execute(
                text(
                    "INSERT INTO tailoring.orphan_bullet "
                    "(tailored_cv_id, user_id, kind, section, experience_index, "
                    "bullet_index, text, claimed_refs, issue) "
                    "VALUES (:run_id, :user_id, :kind, :section, :experience_index, "
                    ":bullet_index, :text, :claimed_refs, :issue)"
                ),
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "kind": draft.kind,
                    "section": draft.section,
                    "experience_index": draft.experience_index,
                    "bullet_index": draft.bullet_index,
                    "text": draft.text,
                    "claimed_refs": draft.claimed_refs,
                    "issue": draft.issue,
                },
            )


def read_run(
    engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID
) -> StoredRun | None:
    """Read a run with its document and orphans.

    Args:
        engine: The app-role engine.
        user_id: The owner (RLS hides other users' runs).
        run_id: The run to read.

    Returns:
        The `StoredRun`, or None if it does not exist for this user.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                "SELECT id, user_id, job_group_id, truth_base_version, target_title, "
                "status, attempts, error_message, content, tailor_model, "
                "tailor_prompt_version, critic_model, critic_prompt_version, "
                "created_at FROM tailoring.tailored_cv WHERE id = :run_id"
            ),
            {"run_id": run_id},
        ).one_or_none()
        if row is None:
            return None
        orphan_rows = conn.execute(
            text(
                f"SELECT {_ORPHAN_COLUMNS} FROM tailoring.orphan_bullet "
                "WHERE tailored_cv_id = :run_id "
                "ORDER BY section DESC, experience_index, bullet_index"
            ),
            {"run_id": run_id},
        ).all()
    return StoredRun(
        id=row.id,
        user_id=row.user_id,
        job_group_id=row.job_group_id,
        truth_base_version=row.truth_base_version,
        target_title=row.target_title,
        status=row.status,
        attempts=row.attempts,
        error_message=row.error_message,
        document=(
            TailoredDocument.model_validate(row.content)
            if row.content is not None
            else None
        ),
        tailor_model=row.tailor_model,
        tailor_prompt_version=row.tailor_prompt_version,
        critic_model=row.critic_model,
        critic_prompt_version=row.critic_prompt_version,
        created_at=row.created_at,
        orphans=[_orphan_from_row(r) for r in orphan_rows],
    )


def read_orphan(
    engine: Engine, user_id: uuid.UUID, orphan_id: uuid.UUID
) -> StoredOrphan | None:
    """Read one orphan row.

    Args:
        engine: The app-role engine.
        user_id: The owner (RLS hides other users' rows).
        orphan_id: The orphan to read.

    Returns:
        The `StoredOrphan`, or None if it does not exist for this user.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                f"SELECT {_ORPHAN_COLUMNS} FROM tailoring.orphan_bullet "
                "WHERE id = :orphan_id"
            ),
            {"orphan_id": orphan_id},
        ).one_or_none()
    return _orphan_from_row(row) if row is not None else None


def _shift_later_bullets(
    conn: Connection, run_id: uuid.UUID, experience_index: int, bullet_index: int
) -> None:
    """Close the gap left by a removed bullet in the run's other orphans.

    Args:
        conn: The open transaction.
        run_id: The run.
        experience_index: The role the bullet was removed from.
        bullet_index: The removed bullet's position.
    """
    conn.execute(
        text(
            "UPDATE tailoring.orphan_bullet SET bullet_index = bullet_index - 1 "
            "WHERE tailored_cv_id = :run_id AND section = 'experience' "
            "AND experience_index = :experience_index "
            "AND bullet_index > :bullet_index"
        ),
        {
            "run_id": run_id,
            "experience_index": experience_index,
            "bullet_index": bullet_index,
        },
    )


def save_decision(
    engine: Engine,
    user_id: uuid.UUID,
    *,
    orphan: StoredOrphan,
    status: str,
    evidence_ref: str | None,
    document: TailoredDocument,
    removed_position: tuple[int, int] | None,
) -> str:
    """Persist one orphan decision and the updated document atomically.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        orphan: The orphan being decided.
        status: `linked` or `rejected`.
        evidence_ref: The linked bullet id, for a link.
        document: The document after the decision was applied.
        removed_position: `(experience_index, bullet_index)` of a removed
            line, so later orphans in that role can be re-indexed.

    Returns:
        The run's new status: `approved` when no orphan is left pending,
        else `needs_review`.
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "UPDATE tailoring.orphan_bullet SET status = :status, "
                "evidence_ref = :evidence_ref, decided_at = now() "
                "WHERE id = :orphan_id"
            ),
            {"status": status, "evidence_ref": evidence_ref, "orphan_id": orphan.id},
        )
        if removed_position is not None:
            _shift_later_bullets(
                conn, orphan.tailored_cv_id, removed_position[0], removed_position[1]
            )
        pending = conn.execute(
            text(
                "SELECT count(*) FROM tailoring.orphan_bullet "
                "WHERE tailored_cv_id = :run_id AND status = 'pending'"
            ),
            {"run_id": orphan.tailored_cv_id},
        ).scalar_one()
        run_status = "approved" if pending == 0 else "needs_review"
        conn.execute(
            text(
                "UPDATE tailoring.tailored_cv SET content = CAST(:content AS jsonb), "
                "status = :status, updated_at = now() WHERE id = :run_id"
            ),
            {
                "content": document.model_dump_json(),
                "status": run_status,
                "run_id": orphan.tailored_cv_id,
            },
        )
    return run_status
```

- [ ] **Step 4: Write `decisions.py`**

Create `packages/core/core/tailoring/decisions.py`:

```python
"""The link/reject rules for orphan lines (Step 17, spec "Orphan decisions").

Pure functions over a TailoredDocument — no database. The store applies the
result; this module only decides what the document becomes.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.cv.schema import CVTruthBase
from core.tailoring.assemble import bullet_index
from core.tailoring.schema import TailoredBullet, TailoredDocument, TailoredSummary
from core.tailoring.store import StoredOrphan


class DecisionError(ValueError):
    """A decision cannot be applied (bad action, bad link, stale orphan)."""


@dataclass(frozen=True)
class DecisionResult:
    """The outcome of one decision.

    Attributes:
        document: The document after the decision (a copy; the input is
            never mutated).
        removed_position: `(experience_index, bullet_index)` when a line was
            removed, so later orphans in that role can be re-indexed.
    """

    document: TailoredDocument
    removed_position: tuple[int, int] | None


def _bullet_at(document: TailoredDocument, orphan: StoredOrphan) -> TailoredBullet:
    """Find the experience bullet an orphan refers to.

    Args:
        document: The current document.
        orphan: The orphan row.

    Returns:
        The bullet at the orphan's position.

    Raises:
        DecisionError: If the position does not exist or its text no longer
            matches what was recorded (the document changed since).
    """
    role_index, position = orphan.experience_index, orphan.bullet_index
    if (
        role_index is None
        or position is None
        or not 0 <= role_index < len(document.experience)
        or not 0 <= position < len(document.experience[role_index].bullets)
    ):
        raise DecisionError("this line no longer exists in the document")
    bullet = document.experience[role_index].bullets[position]
    if bullet.text != orphan.text:
        raise DecisionError("the document has changed since this line was recorded")
    return bullet


def apply_decision(
    document: TailoredDocument,
    orphan: StoredOrphan,
    *,
    action: str,
    evidence_ref: str | None,
    truth_base: CVTruthBase,
) -> DecisionResult:
    """Apply a link or reject decision to a document.

    Args:
        document: The current document.
        orphan: The orphan being decided.
        action: `link` or `reject`.
        evidence_ref: For `link`, the truth-base `bullet_id` that evidences
            the line.
        truth_base: The truth base the document was built from.

    Returns:
        The updated document and any removed position.

    Raises:
        DecisionError: On an unknown action; a link with no ref, an
            unknown ref, or (for an experience line) a ref from another
            role; or a stale/out-of-range orphan.
    """
    if action not in ("link", "reject"):
        raise DecisionError(f"unknown action {action!r}")
    known = bullet_index(truth_base)
    updated = document.model_copy(deep=True)

    if orphan.section == "summary":
        summary = updated.summary
        if summary is None or summary.text != orphan.text:
            raise DecisionError("the summary has changed since this line was recorded")
        if action == "link":
            if evidence_ref is None or evidence_ref not in known:
                raise DecisionError("link needs an existing truth-base bullet id")
            summary.evidence_refs = [evidence_ref]
            summary.origin = "linked"
        elif truth_base.summary:
            updated.summary = TailoredSummary(
                text=truth_base.summary, evidence_refs=[], origin="original"
            )
        else:
            updated.summary = None
        return DecisionResult(updated, None)

    bullet = _bullet_at(updated, orphan)
    role_index = orphan.experience_index
    position = orphan.bullet_index
    assert role_index is not None and position is not None

    if action == "link":
        if evidence_ref is None or evidence_ref not in known:
            raise DecisionError("link needs an existing truth-base bullet id")
        if known[evidence_ref][0] != role_index:
            raise DecisionError(
                "the evidence must come from the same role as the line"
            )
        bullet.evidence_refs = [evidence_ref]
        bullet.origin = "linked"
        return DecisionResult(updated, None)

    valid = [ref for ref in orphan.claimed_refs if ref in known]
    if orphan.kind == "unsupported" and len(valid) == 1:
        bullet.text = known[valid[0]][1]
        bullet.evidence_refs = [valid[0]]
        bullet.origin = "original"
        return DecisionResult(updated, None)

    updated.experience[role_index].bullets.pop(position)
    return DecisionResult(updated, (role_index, position))
```

- [ ] **Step 5: Run the decisions test**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_decisions -v`
Expected: 14 tests OK.

Note: `decisions.py` imports `StoredOrphan` from `store.py`, and `store.py` does not import `decisions.py`, so there is no import cycle.

- [ ] **Step 6: Write the store integration test**

Create `packages/core/tests/integration/test_tailoring_store.py`:

```python
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
    create_run,
    finish_run,
    read_orphan,
    read_run,
    save_decision,
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
            document = result.document
            statuses.append(
                save_decision(
                    self.app_engine,
                    self.user_a,
                    orphan=orphan,
                    status="linked",
                    evidence_ref=self.ref0,
                    document=document,
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7: Run the store test**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_store -v`
Expected: 7 tests OK.

- [ ] **Step 8: Lint and commit**

```bash
F="core/tailoring/store.py core/tailoring/decisions.py tests/test_tailoring_decisions.py tests/integration/test_tailoring_store.py"
arch -arm64 ../../venv/bin/python -m ruff check $F
arch -arm64 ../../venv/bin/python -m black $F
arch -arm64 ../../venv/bin/python -m isort $F
arch -arm64 ../../venv/bin/python -m unittest tests.test_tailoring_decisions tests.integration.test_tailoring_store 2>&1 | tail -3
git add $F
git commit -m "feat(job_search): Step 17 — orphan decisions and run persistence

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
Expected: ruff clean; both suites OK. (`$F` is intentionally unquoted so zsh/bash split it into paths.)

---

### Task 9: The loop

**Files:**
- Create: `packages/core/core/tailoring/loop.py`
- Test: `packages/core/tests/integration/test_tailoring_loop.py`

**Interfaces:**
- Consumes: everything above.
- Produces (`core.tailoring.loop`):
  - `MAX_RETRIES = 2`
  - `TailoringError(Exception)`; subclasses `NoCvError`, `UnknownJobError`, `NoTargetTitleError`
  - `TailoringOutcome(run_id: uuid.UUID, status: str, attempts: int)` frozen dataclass
  - `start_tailoring(app_engine, user_id, job_group_id: str) -> uuid.UUID` — validates preconditions and creates the `generating` run. Raises `NoCvError` / `UnknownJobError` / `NoTargetTitleError` (Review Focus 3, 4).
  - `execute_tailoring(app_engine, user_id, run_id, *, adapters, config_path=None, max_retries=MAX_RETRIES) -> TailoringOutcome` — never raises; any failure ends the run `failed` with a message.
  - `run_tailoring(app_engine, user_id, job_group_id, *, adapters, config_path=None, max_retries=MAX_RETRIES) -> TailoringOutcome` — `start` + `execute`.

Loop rules: up to `max_retries + 1` Tailor attempts. The critic is skipped on a non-final attempt when code checks already failed (saves paid calls) and always runs on the final attempt. Retry feedback lists code-check problems, orphan lines, critic issues and *evidenced* missing keywords. Keyword gaps alone never block approval. After the last attempt, leftover problems become `OrphanDraft` rows; a structural problem (`STRUCTURAL_CODES`) fails the run. Approved means no orphan rows.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_tailoring_loop.py`:

```python
"""Integration tests for the tailoring loop (real Postgres, fake LLMs)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.cv.store import write_truth_base
from core.llm.types import LLMResponse
from core.tailoring.loop import (
    NoCvError,
    NoTargetTitleError,
    UnknownJobError,
    execute_tailoring,
    run_tailoring,
    start_tailoring,
)
from core.tailoring.store import read_run

_JOB = "zzfixture-tlr-loop-1"
_NO_TITLE_JOB = "zzfixture-tlr-loop-2"


class _Tailor:
    """Replays tailor replies in order (the last repeats) and records prompts."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LLMResponse(
            text=reply, provider="ollama", model=model, input_tokens=1, output_tokens=1
        )


class _Critic:
    """Marks the given item ids unsupported and everything else supported."""

    def __init__(self, unsupported: set[str] | None = None, reply: str | None = None):
        self.unsupported = unsupported or set()
        self.reply = reply
        self.calls = 0

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.calls += 1
        if self.reply is not None:
            return LLMResponse(
                text=self.reply,
                provider="anthropic",
                model=model,
                input_tokens=1,
                output_tokens=1,
            )
        ids = re.findall(r'"id": "([^"]+)"', prompt)
        verdicts = [
            {
                "id": i,
                "supported": i not in self.unsupported,
                "issue": "adds a claim" if i in self.unsupported else "",
            }
            for i in ids
        ]
        return LLMResponse(
            text=json.dumps(
                {"verdicts": verdicts, "stretch": {"is_stretch": False, "reason": ""}}
            ),
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestTailoringLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        self.ref1 = bullet_id(self.truth_base, 0, 1)
        self.ref_old = bullet_id(self.truth_base, 1, 0)
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture loop user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job "
                    "(job_group_id, title_for_display, company, description) VALUES "
                    "(:j1, 'Lead Data Engineer', 'Gamma', 'Own the data platform.'), "
                    "(:j2, NULL, 'NoTitleCo', 'No title here.')"
                ),
                {"j1": _JOB, "j2": _NO_TITLE_JOB},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__bridge_job_skill "
                    "(job_group_id, skill_id, requirement_level, mention_count) "
                    "VALUES (:j, 'zzfixture-skill-sql', 'must_have', 2), "
                    "(:j, 'zzfixture-skill-k8s', 'must_have', 1)"
                ),
                {"j": _JOB},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.silver__skill (skill_id, canonical_label, source) "
                    "VALUES ('zzfixture-skill-sql', 'SQL', 'custom'), "
                    "('zzfixture-skill-k8s', 'Kubernetes', 'custom')"
                )
            )

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
                text("DELETE FROM cv_truth_base WHERE user_id = :u"), {"u": self.user_id}
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__bridge_job_skill "
                    "WHERE job_group_id LIKE 'zzfixture-tlr-loop-%'"
                )
            )
            conn.execute(
                text("DELETE FROM silver.silver__skill WHERE skill_id LIKE 'zzfixture-skill-%'")
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE 'zzfixture-tlr-loop-%'")
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :u"), {"u": self.user_id})

    def _store_cv(self) -> None:
        write_truth_base(self.app_engine, self.user_id, "zzfixture markdown", self.truth_base)

    def _reply(
        self,
        bullets: list[dict],
        skills: list[str] | None = None,
        *,
        drop_older_role: bool = False,
    ) -> str:
        """Build a Tailor reply.

        Args:
            bullets: The bullets for role 0.
            skills: Skill names to show; defaults to all three.
            drop_older_role: Also send role 1 with no bullets. An omitted
                role keeps its original bullets (one mentions SQL), so a
                test about SQL being *missing* must empty it explicitly.

        Returns:
            The reply as JSON text.
        """
        experience = [{"truth_index": 0, "bullets": bullets}]
        if drop_older_role:
            experience.append({"truth_index": 1, "bullets": []})
        return json.dumps(
            {
                "summary": {"text": "Data engineer.", "evidence_refs": [self.ref0]},
                "experience": experience,
                "skills": skills if skills is not None else ["dbt", "Airflow", "SQL"],
            }
        )

    def _clean_bullets(self) -> list[dict]:
        return [
            {
                "text": "Built dbt models powering risk reporting",
                "evidence_refs": [self.ref0],
            },
            {
                "text": "Migrated nightly batch jobs to Airflow",
                "evidence_refs": [self.ref1],
            },
        ]

    def _run(self, tailor, critic, **kwargs):
        return run_tailoring(
            self.app_engine,
            self.user_id,
            _JOB,
            adapters={"ollama": tailor, "anthropic": critic},
            **kwargs,
        )

    # --- happy path ------------------------------------------------------

    def test_a_clean_first_attempt_is_approved(self) -> None:
        self._store_cv()
        tailor = _Tailor([self._reply(self._clean_bullets())])
        critic = _Critic()
        outcome = self._run(tailor, critic)
        self.assertEqual((outcome.status, outcome.attempts), ("approved", 1))
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(run.orphans, [])
        self.assertEqual(run.document.headline, "Lead Data Engineer")
        self.assertEqual(run.tailor_prompt_version, "local.v1")
        self.assertEqual(run.critic_prompt_version, "claude.v1")
        self.assertEqual(len(tailor.prompts), 1)
        self.assertEqual(critic.calls, 1)

    def test_keyword_coverage_is_recorded_and_gaps_alone_never_block(self) -> None:
        self._store_cv()
        outcome = self._run(
            _Tailor([self._reply(self._clean_bullets(), skills=["dbt"])]), _Critic()
        )
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(run.document.keyword_coverage.missing_unevidenced, ["Kubernetes"])
        self.assertEqual(run.status, "approved")

    # --- retries ---------------------------------------------------------

    def test_an_uncited_bullet_is_retried_with_feedback_then_approved(self) -> None:
        self._store_cv()
        bad = self._clean_bullets() + [{"text": "Led a team of 12", "evidence_refs": []}]
        tailor = _Tailor([self._reply(bad), self._reply(self._clean_bullets())])
        outcome = self._run(tailor, _Critic())
        self.assertEqual((outcome.status, outcome.attempts), ("approved", 2))
        self.assertIn("Fix these problems", tailor.prompts[1])
        self.assertIn("Led a team of 12", tailor.prompts[1])

    def test_an_evidenced_missing_keyword_is_fed_back(self) -> None:
        self._store_cv()
        first = self._reply(
            self._clean_bullets(), skills=["dbt"], drop_older_role=True
        )
        second = self._reply(self._clean_bullets(), skills=["dbt", "SQL"])
        tailor = _Tailor([first, second])
        outcome = self._run(tailor, _Critic())
        self.assertEqual(outcome.attempts, 2)
        # The whole prompt lists the job's skills; only the feedback block
        # (after "Fix these problems") must name SQL and never Kubernetes.
        feedback_block = tailor.prompts[1].split("Fix these problems")[1]
        self.assertIn("SQL", feedback_block)
        self.assertNotIn("Kubernetes", feedback_block)

    def test_the_critic_is_skipped_on_a_non_final_attempt_with_code_problems(self) -> None:
        self._store_cv()
        # An unknown id is a code problem (a bullet with no refs at all is
        # only an orphan *origin*, which does not stop the critic running).
        bad = [{"text": "Invented", "evidence_refs": ["nope"]}]
        tailor = _Tailor([self._reply(bad), self._reply(self._clean_bullets())])
        critic = _Critic()
        self._run(tailor, critic)
        self.assertEqual(critic.calls, 1)  # only on the clean attempt

    # --- surfaced, never approved ----------------------------------------

    def test_an_orphan_that_survives_every_retry_is_surfaced_not_approved(self) -> None:
        self._store_cv()
        bad = self._clean_bullets() + [{"text": "Led a team of 12", "evidence_refs": []}]
        tailor = _Tailor([self._reply(bad)])
        outcome = self._run(tailor, _Critic())
        self.assertEqual((outcome.status, outcome.attempts), ("needs_review", 3))
        self.assertEqual(len(tailor.prompts), 3)  # first try + two retries, no more
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(
            [(o.kind, o.text, o.status) for o in run.orphans],
            [("orphan", "Led a team of 12", "pending")],
        )
        self.assertEqual((run.orphans[0].experience_index, run.orphans[0].bullet_index), (0, 2))

    def test_a_critic_rejection_becomes_an_unsupported_orphan(self) -> None:
        self._store_cv()
        tailor = _Tailor([self._reply(self._clean_bullets())])
        outcome = self._run(tailor, _Critic(unsupported={"e0b0"}))
        self.assertEqual(outcome.status, "needs_review")
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual(
            [(o.kind, o.issue) for o in run.orphans], [("unsupported", "adds a claim")]
        )
        self.assertEqual(run.orphans[0].claimed_refs, [self.ref0])

    def test_a_cross_role_citation_survives_as_an_orphan(self) -> None:
        # Review Focus 1.
        self._store_cv()
        bad = self._clean_bullets() + [
            {"text": "Wrote SQL reports", "evidence_refs": [self.ref_old]}
        ]
        outcome = self._run(_Tailor([self._reply(bad)]), _Critic())
        self.assertEqual(outcome.status, "needs_review")
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual([o.text for o in run.orphans], ["Wrote SQL reports"])
        self.assertIn("another role", run.orphans[0].issue)

    def test_the_stretch_judgement_is_stored_on_the_document(self) -> None:
        self._store_cv()
        critic = _Critic(
            reply=json.dumps(
                {
                    "verdicts": [
                        {"id": "e0b0", "supported": True, "issue": ""},
                        {"id": "e0b1", "supported": True, "issue": ""},
                    ],
                    "stretch": {"is_stretch": True, "reason": "senior title"},
                }
            )
        )
        outcome = self._run(_Tailor([self._reply(self._clean_bullets())]), critic)
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertTrue(run.document.stretch.is_stretch)
        self.assertEqual(run.status, "approved")  # advisory, not a failure

    # --- failures fail closed --------------------------------------------

    def test_a_critic_that_omits_a_verdict_does_not_approve(self) -> None:
        # Review Focus 2.
        self._store_cv()
        # Only e0b0 (reworded) is judged; the critic answers none of them.
        critic = _Critic(
            reply=json.dumps(
                {"verdicts": [], "stretch": {"is_stretch": False, "reason": ""}}
            )
        )
        outcome = self._run(_Tailor([self._reply(self._clean_bullets())]), critic)
        self.assertEqual(outcome.status, "needs_review")
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertEqual([o.kind for o in run.orphans], ["unsupported"])

    def test_an_unparseable_critic_reply_fails_the_run(self) -> None:
        self._store_cv()
        outcome = self._run(
            _Tailor([self._reply(self._clean_bullets())]), _Critic(reply="lgtm")
        )
        self.assertEqual(outcome.status, "failed")
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertIsNone(run.document)
        self.assertIn("critic", run.error_message)

    def test_malformed_tailor_output_on_every_attempt_fails_the_run(self) -> None:
        # Review Focus 4.
        self._store_cv()
        tailor = _Tailor(["this is not json"])
        outcome = self._run(tailor, _Critic())
        self.assertEqual((outcome.status, outcome.attempts), ("failed", 3))
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertIn("unusable Tailor reply", run.error_message)
        self.assertEqual(run.orphans, [])

    def test_a_tailor_that_recovers_after_a_malformed_reply_succeeds(self) -> None:
        self._store_cv()
        tailor = _Tailor(["not json", self._reply(self._clean_bullets())])
        outcome = self._run(tailor, _Critic())
        self.assertEqual((outcome.status, outcome.attempts), ("approved", 2))

    def test_a_critic_routed_to_a_weaker_model_fails_the_run_without_a_call(self) -> None:
        self._store_cv()
        tailor = _Tailor([self._reply(self._clean_bullets())])
        critic = _Critic()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.yml"
            path.write_text(
                "tasks:\n"
                "  cv_tailoring:\n    provider: ollama\n    model: m\n    prompt_family: local\n"
                "  fabrication_critic:\n    provider: ollama\n    model: m\n    prompt_family: claude\n"
            )
            outcome = self._run(tailor, critic, config_path=path)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(critic.calls, 0)
        run = read_run(self.app_engine, self.user_id, outcome.run_id)
        self.assertIn("anthropic", run.error_message)

    # --- preconditions ---------------------------------------------------

    def test_a_user_with_no_cv_cannot_start(self) -> None:
        # Review Focus 4.
        with self.assertRaises(NoCvError):
            start_tailoring(self.app_engine, self.user_id, _JOB)

    def test_an_unknown_job_cannot_start(self) -> None:
        self._store_cv()
        with self.assertRaises(UnknownJobError):
            start_tailoring(self.app_engine, self.user_id, "zzfixture-tlr-loop-nope")

    def test_a_job_with_no_title_cannot_start(self) -> None:
        # Review Focus 3.
        self._store_cv()
        with self.assertRaises(NoTargetTitleError):
            start_tailoring(self.app_engine, self.user_id, _NO_TITLE_JOB)

    def test_a_blank_title_cannot_start(self) -> None:
        self._store_cv()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "UPDATE gold.dim_job SET title_for_display = '   ' "
                    "WHERE job_group_id = :j"
                ),
                {"j": _JOB},
            )
        with self.assertRaises(NoTargetTitleError):
            start_tailoring(self.app_engine, self.user_id, _JOB)

    def test_start_creates_a_generating_run_and_execute_finishes_it(self) -> None:
        self._store_cv()
        run_id = start_tailoring(self.app_engine, self.user_id, _JOB)
        self.assertEqual(
            read_run(self.app_engine, self.user_id, run_id).status, "generating"
        )
        outcome = execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={
                "ollama": _Tailor([self._reply(self._clean_bullets())]),
                "anthropic": _Critic(),
            },
        )
        self.assertEqual(outcome.run_id, run_id)
        self.assertEqual(
            read_run(self.app_engine, self.user_id, run_id).status, "approved"
        )

    def test_the_run_uses_the_truth_base_version_it_started_with(self) -> None:
        self._store_cv()
        run_id = start_tailoring(self.app_engine, self.user_id, _JOB)
        edited = make_truth_base()
        edited.experience[0].company = "Changed After Start"
        write_truth_base(self.app_engine, self.user_id, "zzfixture markdown", edited)
        execute_tailoring(
            self.app_engine,
            self.user_id,
            run_id,
            adapters={
                "ollama": _Tailor([self._reply(self._clean_bullets())]),
                "anthropic": _Critic(),
            },
        )
        run = read_run(self.app_engine, self.user_id, run_id)
        self.assertEqual(run.document.experience[0].company, "Acme Bank")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_loop -v`
Expected: `ModuleNotFoundError: No module named 'core.tailoring.loop'`.

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/tailoring/loop.py`:

```python
"""The tailoring loop: tailor → assemble → check → critic, retried at most
twice, then persisted (Step 17).

`start_tailoring` validates and creates the run synchronously so the API
can answer immediately; `execute_tailoring` does the slow part (it is what
runs as a background task) and never raises — every failure ends the run
`failed` with a message, and no unchecked line is ever approved.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from core.cv.store import read_truth_base, read_truth_base_version
from core.llm.types import LLMAdapter
from core.tailoring.assemble import assemble
from core.tailoring.checks import (
    STRUCTURAL_CODES,
    Problem,
    check_evidence_refs,
    check_experience_unchanged,
    check_headline,
    compute_keyword_coverage,
)
from core.tailoring.context import load_job_context
from core.tailoring.critic import CriticResult, run_critic
from core.tailoring.schema import (
    JobContext,
    KeywordCoverage,
    TailoredDocument,
    TailorOutputError,
)
from core.tailoring.store import OrphanDraft, create_run, finish_run, read_run
from core.tailoring.tailor import run_tailor

MAX_RETRIES = 2
"""Tailor retries after the first attempt (PLAN.md Step 17: at most twice)."""

_ITEM_ID_RE = re.compile(r"^e(\d+)b(\d+)$")


class TailoringError(Exception):
    """A tailoring run cannot start."""


class NoCvError(TailoringError):
    """The user has no CV truth base."""


class UnknownJobError(TailoringError):
    """No job with that `job_group_id` exists."""


class NoTargetTitleError(TailoringError):
    """The job has no `title_for_display`, so there is nothing to mirror."""


@dataclass(frozen=True)
class TailoringOutcome:
    """How a run ended.

    Attributes:
        run_id: The run.
        status: `approved`, `needs_review` or `failed`.
        attempts: Tailor attempts made.
    """

    run_id: uuid.UUID
    status: str
    attempts: int


def start_tailoring(
    app_engine: Engine, user_id: uuid.UUID, job_group_id: str
) -> uuid.UUID:
    """Validate preconditions and create a `generating` run.

    Args:
        app_engine: The app-role engine.
        user_id: Whose CV to tailor.
        job_group_id: The target job.

    Returns:
        The new run's id.

    Raises:
        NoCvError: If the user has no CV truth base.
        UnknownJobError: If the job does not exist.
        NoTargetTitleError: If the job's `title_for_display` is NULL/blank.
    """
    stored = read_truth_base(app_engine, user_id)
    if stored is None:
        raise NoCvError("this user has no CV truth base yet")
    job = load_job_context(app_engine, job_group_id)
    if job is None:
        raise UnknownJobError(f"no job {job_group_id!r}")
    if not (job.title_for_display or "").strip():
        raise NoTargetTitleError(
            f"job {job_group_id!r} has no title_for_display, so there is no "
            "title to mirror"
        )
    return create_run(
        app_engine,
        user_id,
        job_group_id=job_group_id,
        truth_base_version=stored.version,
        target_title=job.title_for_display.strip(),
    )


def _feedback(
    problems: list[Problem],
    document: TailoredDocument,
    critic: CriticResult | None,
    coverage: KeywordCoverage,
) -> list[str]:
    """Build the concrete feedback for the Tailor's next attempt.

    Args:
        problems: Code-check problems from this attempt.
        document: This attempt's document.
        critic: This attempt's critic result, if the critic ran.
        coverage: This attempt's keyword coverage.

    Returns:
        Messages; empty when nothing needs fixing.
    """
    messages = [problem.message for problem in problems]
    for role in document.experience:
        for position, bullet in enumerate(role.bullets):
            if bullet.origin == "orphan":
                messages.append(
                    f"bullet e{role.truth_index}b{position} ({bullet.text!r}) cites no "
                    "valid bullet id — cite the bullet it is based on, or remove it"
                )
    if document.summary is not None and document.summary.origin == "orphan":
        messages.append(
            "the summary cites no valid bullet id — cite the bullets it is based "
            "on, or reuse the original summary"
        )
    if critic is not None:
        for item_id, verdict in critic.verdicts.items():
            if not verdict.supported:
                messages.append(
                    f"line {item_id} was rejected as unsupported: {verdict.issue}. "
                    "Use only what its cited bullets state"
                )
    if coverage.missing_evidenced:
        messages.append(
            "surface these skills the candidate genuinely has: "
            + ", ".join(coverage.missing_evidenced)
        )
    return messages


def _draft(
    document: TailoredDocument, location: str, kind: str, issue: str | None
) -> OrphanDraft | None:
    """Build an orphan draft for a line, or None if the line is not there.

    Args:
        document: The final document.
        location: `summary` or `e{role}b{bullet}`.
        kind: `orphan` or `unsupported`.
        issue: What is wrong.

    Returns:
        The draft, or None when the location does not exist.
    """
    if location == "summary":
        if document.summary is None:
            return None
        return OrphanDraft(
            kind=kind,
            section="summary",
            experience_index=None,
            bullet_index=None,
            text=document.summary.text,
            claimed_refs=list(document.summary.evidence_refs),
            issue=issue,
        )
    match = _ITEM_ID_RE.match(location)
    if match is None:
        return None
    role, position = int(match.group(1)), int(match.group(2))
    if role >= len(document.experience) or position >= len(
        document.experience[role].bullets
    ):
        return None
    bullet = document.experience[role].bullets[position]
    return OrphanDraft(
        kind=kind,
        section="experience",
        experience_index=role,
        bullet_index=position,
        text=bullet.text,
        claimed_refs=list(bullet.evidence_refs),
        issue=issue,
    )


def _orphan_drafts(
    document: TailoredDocument,
    problems: list[Problem],
    critic: CriticResult | None,
) -> list[OrphanDraft]:
    """Turn every unresolved line into an orphan row.

    One row per line. A critic rejection wins over an evidence problem
    (the line has a valid source but claims too much); an evidence problem
    or missing source is an `orphan`.

    Args:
        document: The final document.
        problems: The final attempt's code-check problems.
        critic: The final attempt's critic result.

    Returns:
        Drafts in document order (summary first).
    """
    issues: dict[str, tuple[str, str | None]] = {}
    if document.summary is not None and document.summary.origin == "orphan":
        issues["summary"] = ("orphan", "no evidence_ref in the CV")
    for role in document.experience:
        for position, bullet in enumerate(role.bullets):
            if bullet.origin == "orphan":
                issues[f"e{role.truth_index}b{position}"] = (
                    "orphan",
                    "no evidence_ref in the CV",
                )
    for problem in problems:
        if problem.code not in STRUCTURAL_CODES:
            issues[problem.location] = ("orphan", problem.message)
    if critic is not None:
        for item_id, verdict in critic.verdicts.items():
            if not verdict.supported:
                issues[item_id] = ("unsupported", verdict.issue)

    drafts = [
        draft
        for location, (kind, issue) in issues.items()
        if (draft := _draft(document, location, kind, issue)) is not None
    ]
    return sorted(
        drafts,
        key=lambda d: (
            d.section != "summary",
            d.experience_index or 0,
            d.bullet_index or 0,
        ),
    )


def _execute(
    app_engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None,
    max_retries: int,
) -> TailoringOutcome:
    """Run the loop for an existing `generating` run.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: The run.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).
        max_retries: Retries after the first attempt.

    Returns:
        The outcome. Raises on any failure — `execute_tailoring` turns that
        into a `failed` run.
    """
    run = read_run(app_engine, user_id, run_id)
    if run is None:
        raise RuntimeError(f"run {run_id} not found")
    stored = read_truth_base_version(app_engine, user_id, run.truth_base_version)
    if stored is None:
        raise RuntimeError(f"CV version {run.truth_base_version} no longer exists")
    truth_base = stored.truth_base
    job: JobContext | None = load_job_context(app_engine, run.job_group_id)
    if job is None:
        raise RuntimeError(f"job {run.job_group_id!r} no longer exists")

    feedback: list[str] = []
    tailor_result = None
    critic: CriticResult | None = None
    document: TailoredDocument | None = None
    problems: list[Problem] = []
    attempts = 0
    for attempts in range(1, max_retries + 2):
        final = attempts == max_retries + 1
        try:
            tailor_result = run_tailor(
                truth_base, job, feedback, adapters=adapters, config_path=config_path
            )
        except TailorOutputError as exc:
            if final:
                raise
            feedback = [f"Your previous reply could not be used: {exc}"]
            continue
        document = assemble(
            truth_base, tailor_result.output, target_title=run.target_title
        )
        problems = (
            check_evidence_refs(document, truth_base)
            + check_experience_unchanged(document, truth_base)
            + check_headline(document)
        )
        coverage = compute_keyword_coverage(document, truth_base, job.skills)
        document = document.model_copy(update={"keyword_coverage": coverage})
        critic = (
            run_critic(
                document, truth_base, job, adapters=adapters, config_path=config_path
            )
            if not problems or final
            else None
        )
        feedback = _feedback(problems, document, critic, coverage)
        if not feedback or final:
            break

    assert document is not None and tailor_result is not None
    structural = [p for p in problems if p.code in STRUCTURAL_CODES]
    if structural:
        raise RuntimeError(
            "the document failed a structural check: "
            + "; ".join(p.message for p in structural)
        )
    if critic is not None:
        document = document.model_copy(update={"stretch": critic.stretch})
    orphans = _orphan_drafts(document, problems, critic)
    status = "needs_review" if orphans else "approved"
    finish_run(
        app_engine,
        user_id,
        run_id,
        status=status,
        document=document,
        orphans=orphans,
        attempts=attempts,
        tailor_model=tailor_result.model,
        tailor_prompt_version=tailor_result.prompt_version,
        critic_model=critic.model if critic else None,
        critic_prompt_version=critic.prompt_version if critic else None,
    )
    return TailoringOutcome(run_id=run_id, status=status, attempts=attempts)


def execute_tailoring(
    app_engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    max_retries: int = MAX_RETRIES,
) -> TailoringOutcome:
    """Run the loop for a started run. Never raises.

    Args:
        app_engine: The app-role engine.
        user_id: The run's owner.
        run_id: A run created by `start_tailoring`.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).
        max_retries: Retries after the first attempt.

    Returns:
        The outcome; `failed` (with the message stored on the run) when
        anything went wrong, including a critic routed away from Anthropic.
    """
    try:
        return _execute(
            app_engine,
            user_id,
            run_id,
            adapters=adapters,
            config_path=config_path,
            max_retries=max_retries,
        )
    except Exception as exc:  # noqa: BLE001 — a background run must record, not raise
        message = f"{type(exc).__name__}: {exc}"[:500]
        finish_run(
            app_engine,
            user_id,
            run_id,
            status="failed",
            document=None,
            orphans=[],
            attempts=max_retries + 1,
            error_message=message,
        )
        return TailoringOutcome(run_id=run_id, status="failed", attempts=max_retries + 1)


def run_tailoring(
    app_engine: Engine,
    user_id: uuid.UUID,
    job_group_id: str,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    max_retries: int = MAX_RETRIES,
) -> TailoringOutcome:
    """Start and execute a tailoring run in one call (CLI, tests).

    Args:
        app_engine: The app-role engine.
        user_id: Whose CV to tailor.
        job_group_id: The target job.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).
        max_retries: Retries after the first attempt.

    Returns:
        The outcome.

    Raises:
        TailoringError: If the run cannot start (see `start_tailoring`).
    """
    run_id = start_tailoring(app_engine, user_id, job_group_id)
    return execute_tailoring(
        app_engine,
        user_id,
        run_id,
        adapters=adapters,
        config_path=config_path,
        max_retries=max_retries,
    )
```

Note on `attempts` for the failed-run path: `execute_tailoring` records `max_retries + 1` because the exception loses the loop counter; the tests above that pin `("failed", 3)` rely on this and on all attempts having been malformed. The critic-config and critic-reply failures also report 3 — acceptable (the field means "attempt budget used or abandoned" for failed runs); the UI shows the error message, not the count, for failures.

- [ ] **Step 4: Run the test to verify it passes**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_loop -v`
Expected: 20 tests OK.

- [ ] **Step 5: Lint and commit**

```bash
F="core/tailoring/loop.py tests/integration/test_tailoring_loop.py"
arch -arm64 ../../venv/bin/python -m ruff check $F
arch -arm64 ../../venv/bin/python -m black $F
arch -arm64 ../../venv/bin/python -m isort $F
arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_loop 2>&1 | tail -3
git add $F
git commit -m "feat(job_search): Step 17 — the tailor/critic loop

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The `/tailoring` API

**Files:**
- Create: `apps/api/app/routers/tailoring.py`
- Modify: `apps/api/app/main.py` (import and include the router)
- Test: `packages/core/tests/integration/test_tailoring_router.py` (run in the API container)

**Interfaces:**
- Consumes: `core.tailoring.{context,loop,store,decisions}`, `apps.api.app.dependencies.get_app_db_engine/get_llm_adapters`, `core.db.session.get_current_user_id`, `core.cv.store.read_truth_base_version`.
- Produces HTTP (all per-user via `get_current_user_id`):
  - `GET /tailoring/candidates?limit=25` → `[CandidateModel]`
  - `POST /tailoring/runs` body `{"job_group_id": str}` → `202 {"run_id": uuid}`; `409` no CV; `404` unknown job; `422` job has no title
  - `GET /tailoring/runs/{run_id}` → `RunModel` (404 if not this user's)
  - `GET /tailoring/jobs/{job_group_id}/latest-run` → `{"run_id": uuid}` or 404
  - `POST /tailoring/orphans/{orphan_id}/decision` body `{"action": "link"|"reject", "evidence_ref": str|null}` → updated `RunModel`; `404` unknown orphan; `409` already decided; `422` for a rule violation
- `RunModel` fields: `run_id, job_group_id, target_title, status, attempts, error_message, document: dict | None, orphans: [OrphanModel], sources: [SourceBullet]`; `SourceBullet(bullet_id, text, experience_index, role)`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_tailoring_router.py`:

```python
"""Router tests for /tailoring. Run in the API container (needs docling via
app.main): `docker exec -w /app/packages/core job_search-api-1 python -m
unittest tests.integration.test_tailoring_router`.

Uses the real app with `get_current_user_id` and `get_llm_adapters`
overridden — a real ASGI request, fake LLMs only. Starlette's TestClient
runs background tasks before returning, so a POST /runs response is
followed by a finished run.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine
from tests.tailoring_fixtures import bullet_id, make_truth_base

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.dependencies import get_llm_adapters  # noqa: E402
from app.main import app  # noqa: E402

from core.cv.store import write_truth_base  # noqa: E402
from core.db.session import get_current_user_id  # noqa: E402
from core.llm.types import LLMResponse  # noqa: E402

_JOB = "zzfixture-tlr-api-1"
_NO_TITLE_JOB = "zzfixture-tlr-api-2"


class _Tailor:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        return LLMResponse(
            text=self.reply, provider="ollama", model=model, input_tokens=1, output_tokens=1
        )


class _Critic:
    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        ids = re.findall(r'"id": "([^"]+)"', prompt)
        return LLMResponse(
            text=json.dumps(
                {
                    "verdicts": [{"id": i, "supported": True, "issue": ""} for i in ids],
                    "stretch": {"is_stretch": False, "reason": ""},
                }
            ),
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestTailoringRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.other_user = uuid.uuid4()
        self.truth_base = make_truth_base()
        self.ref0 = bullet_id(self.truth_base, 0, 0)
        with self.owner.begin() as conn:
            for user_id in (self.user_id, self.other_user):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'zzfixture api user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job "
                    "(job_group_id, title_for_display, company, description) VALUES "
                    "(:j1, 'Lead Data Engineer', 'Gamma', 'Own the platform.'), "
                    "(:j2, NULL, 'NoTitleCo', 'x')"
                ),
                {"j1": _JOB, "j2": _NO_TITLE_JOB},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score "
                    "(user_id, job_group_id, hard_filter_passed, final_score) "
                    "VALUES (:u, :j, true, 0.8)"
                ),
                {"u": self.user_id, "j": _JOB},
            )
        self._set_replies(self._clean_reply())
        app.dependency_overrides[get_current_user_id] = lambda: self.user_id
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_llm_adapters, None)
        with self.owner.begin() as conn:
            for user_id in (self.user_id, self.other_user):
                conn.execute(
                    text("DELETE FROM tailoring.tailored_cv WHERE user_id = :u"),
                    {"u": user_id},
                )
                conn.execute(
                    text("DELETE FROM scoring.job_score WHERE user_id = :u"),
                    {"u": user_id},
                )
                conn.execute(
                    text("DELETE FROM cv_truth_base_history WHERE user_id = :u"),
                    {"u": user_id},
                )
                conn.execute(
                    text("DELETE FROM cv_truth_base WHERE user_id = :u"), {"u": user_id}
                )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE 'zzfixture-tlr-api-%'")
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user_id, "b": self.other_user},
            )

    def _clean_reply(self) -> str:
        return json.dumps(
            {
                "summary": {"text": "Data engineer.", "evidence_refs": [self.ref0]},
                "experience": [
                    {
                        "truth_index": 0,
                        "bullets": [
                            {
                                "text": "Built dbt models powering risk reporting",
                                "evidence_refs": [self.ref0],
                            }
                        ],
                    }
                ],
                "skills": ["dbt"],
            }
        )

    def _orphan_reply(self) -> str:
        return json.dumps(
            {
                "experience": [
                    {
                        "truth_index": 0,
                        "bullets": [{"text": "Led a team of 12", "evidence_refs": []}],
                    }
                ],
                "skills": ["dbt"],
            }
        )

    def _set_replies(self, tailor_reply: str) -> None:
        adapters = {"ollama": _Tailor(tailor_reply), "anthropic": _Critic()}
        app.dependency_overrides[get_llm_adapters] = lambda: adapters

    def _store_cv(self) -> None:
        write_truth_base(self.app_engine, self.user_id, "md", self.truth_base)

    def _start(self, job_group_id: str = _JOB):
        return self.client.post("/tailoring/runs", json={"job_group_id": job_group_id})

    # --- candidates ------------------------------------------------------

    def test_candidates_lists_the_users_scored_jobs(self) -> None:
        response = self.client.get("/tailoring/candidates")
        self.assertEqual(response.status_code, 200)
        mine = [c for c in response.json() if c["job_group_id"] == _JOB]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["title_for_display"], "Lead Data Engineer")
        self.assertIsNone(mine[0]["latest_status"])

    # --- starting runs ---------------------------------------------------

    def test_a_run_without_a_cv_is_409(self) -> None:
        self.assertEqual(self._start().status_code, 409)

    def test_a_run_for_an_unknown_job_is_404(self) -> None:
        self._store_cv()
        self.assertEqual(self._start("zzfixture-tlr-api-nope").status_code, 404)

    def test_a_run_for_a_job_with_no_title_is_422(self) -> None:
        self._store_cv()
        response = self._start(_NO_TITLE_JOB)
        self.assertEqual(response.status_code, 422)
        self.assertIn("title", response.json()["detail"])

    def test_a_started_run_returns_202_and_finishes_approved(self) -> None:
        self._store_cv()
        response = self._start()
        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run_id"]
        body = self.client.get(f"/tailoring/runs/{run_id}").json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["target_title"], "Lead Data Engineer")
        self.assertEqual(body["document"]["headline"], "Lead Data Engineer")
        self.assertEqual(body["orphans"], [])

    def test_the_run_response_lists_source_bullets_for_the_picker(self) -> None:
        self._store_cv()
        run_id = self._start().json()["run_id"]
        sources = self.client.get(f"/tailoring/runs/{run_id}").json()["sources"]
        self.assertIn(
            {
                "bullet_id": self.ref0,
                "text": "Built dbt models for risk reporting",
                "experience_index": 0,
                "role": "Senior Data Engineer at Acme Bank",
            },
            sources,
        )

    def test_latest_run_for_a_job(self) -> None:
        self._store_cv()
        self.assertEqual(
            self.client.get(f"/tailoring/jobs/{_JOB}/latest-run").status_code, 404
        )
        run_id = self._start().json()["run_id"]
        latest = self.client.get(f"/tailoring/jobs/{_JOB}/latest-run")
        self.assertEqual(latest.json()["run_id"], run_id)

    def test_another_users_run_is_404(self) -> None:
        self._store_cv()
        run_id = self._start().json()["run_id"]
        app.dependency_overrides[get_current_user_id] = lambda: self.other_user
        self.assertEqual(self.client.get(f"/tailoring/runs/{run_id}").status_code, 404)

    # --- orphan decisions ------------------------------------------------

    def _orphan_run(self) -> dict:
        self._store_cv()
        self._set_replies(self._orphan_reply())
        run_id = self._start().json()["run_id"]
        return self.client.get(f"/tailoring/runs/{run_id}").json()

    def test_an_orphan_run_needs_review_and_lists_the_orphan(self) -> None:
        body = self._orphan_run()
        self.assertEqual(body["status"], "needs_review")
        self.assertEqual(
            [(o["kind"], o["text"], o["status"]) for o in body["orphans"]],
            [("orphan", "Led a team of 12", "pending")],
        )

    def test_linking_an_orphan_approves_the_run(self) -> None:
        body = self._orphan_run()
        orphan_id = body["orphans"][0]["id"]
        response = self.client.post(
            f"/tailoring/orphans/{orphan_id}/decision",
            json={"action": "link", "evidence_ref": self.ref0},
        )
        self.assertEqual(response.status_code, 200)
        after = response.json()
        self.assertEqual(after["status"], "approved")
        self.assertEqual(after["orphans"][0]["status"], "linked")
        bullet = after["document"]["experience"][0]["bullets"][0]
        self.assertEqual(bullet["origin"], "linked")
        self.assertEqual(bullet["evidence_refs"], [self.ref0])

    def test_rejecting_an_orphan_removes_the_line_and_approves(self) -> None:
        body = self._orphan_run()
        orphan_id = body["orphans"][0]["id"]
        response = self.client.post(
            f"/tailoring/orphans/{orphan_id}/decision", json={"action": "reject"}
        )
        after = response.json()
        self.assertEqual(after["status"], "approved")
        self.assertEqual(after["document"]["experience"][0]["bullets"], [])

    def test_linking_to_another_roles_bullet_is_422(self) -> None:
        body = self._orphan_run()
        other_role = bullet_id(self.truth_base, 1, 0)
        response = self.client.post(
            f"/tailoring/orphans/{body['orphans'][0]['id']}/decision",
            json={"action": "link", "evidence_ref": other_role},
        )
        self.assertEqual(response.status_code, 422)

    def test_a_link_without_a_ref_is_422(self) -> None:
        body = self._orphan_run()
        response = self.client.post(
            f"/tailoring/orphans/{body['orphans'][0]['id']}/decision",
            json={"action": "link"},
        )
        self.assertEqual(response.status_code, 422)

    def test_deciding_twice_is_409(self) -> None:
        body = self._orphan_run()
        url = f"/tailoring/orphans/{body['orphans'][0]['id']}/decision"
        self.client.post(url, json={"action": "reject"})
        self.assertEqual(self.client.post(url, json={"action": "reject"}).status_code, 409)

    def test_an_unknown_orphan_is_404(self) -> None:
        response = self.client.post(
            f"/tailoring/orphans/{uuid.uuid4()}/decision", json={"action": "reject"}
        )
        self.assertEqual(response.status_code, 404)

    def test_an_unknown_action_is_422(self) -> None:
        body = self._orphan_run()
        response = self.client.post(
            f"/tailoring/orphans/{body['orphans'][0]['id']}/decision",
            json={"action": "shrug"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker exec -w /app/packages/core job_search-api-1 python -m unittest tests.integration.test_tailoring_router -v`
Expected: failures — `404` on `/tailoring/...` routes (router not registered) or an import error.

- [ ] **Step 3: Write the router**

Create `apps/api/app/routers/tailoring.py`:

```python
"""Tailored-CV endpoints (PLAN.md Step 17).

Per-user via `get_current_user_id`, exactly like the scoring and CV routers
(501 until Step 22a's identity middleware, except with the local-dev
`DEV_USER_ID` override). A run is created synchronously and executed as a
background task; poll `GET /tailoring/runs/{run_id}`.
"""

from __future__ import annotations

import uuid
from typing import Literal

from app.dependencies import get_app_db_engine, get_llm_adapters
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy import Engine

from core.cv.store import read_truth_base_version
from core.db.session import get_current_user_id
from core.llm.types import LLMAdapter
from core.tailoring.context import list_candidates
from core.tailoring.decisions import DecisionError, apply_decision
from core.tailoring.loop import (
    NoCvError,
    NoTargetTitleError,
    UnknownJobError,
    execute_tailoring,
    start_tailoring,
)
from core.tailoring.store import (
    StoredRun,
    read_orphan,
    read_run,
    save_decision,
)

router = APIRouter()


class CandidateModel(BaseModel):
    """One job the user could tailor a CV for."""

    job_group_id: str
    title_for_display: str | None
    company: str | None
    final_score: float
    latest_run_id: uuid.UUID | None
    latest_status: str | None


class RunRequest(BaseModel):
    """Request body for starting a run."""

    job_group_id: str


class RunAccepted(BaseModel):
    """Response to starting a run."""

    run_id: uuid.UUID


class LatestRun(BaseModel):
    """Response for a job's latest run."""

    run_id: uuid.UUID


class SourceBullet(BaseModel):
    """One truth-base bullet, offered as a link target for an orphan."""

    bullet_id: str
    text: str
    experience_index: int
    role: str


class OrphanModel(BaseModel):
    """One line awaiting (or past) a decision."""

    id: uuid.UUID
    kind: str
    section: str
    experience_index: int | None
    bullet_index: int | None
    text: str
    claimed_refs: list[str]
    issue: str | None
    status: str
    evidence_ref: str | None


class RunModel(BaseModel):
    """A tailoring run with its document, orphans and link targets."""

    run_id: uuid.UUID
    job_group_id: str
    target_title: str
    status: str
    attempts: int
    error_message: str | None
    document: dict | None
    orphans: list[OrphanModel]
    sources: list[SourceBullet]


class DecisionRequest(BaseModel):
    """Request body for deciding an orphan."""

    action: Literal["link", "reject"]
    evidence_ref: str | None = None

    @model_validator(mode="after")
    def _link_needs_a_ref(self) -> DecisionRequest:
        """Reject a `link` that names no bullet.

        Returns:
            The validated request.

        Raises:
            ValueError: If `action` is `link` and `evidence_ref` is empty.
        """
        if self.action == "link" and not self.evidence_ref:
            raise ValueError("a link needs an evidence_ref")
        return self


def _run_model(engine: Engine, user_id: uuid.UUID, run: StoredRun) -> RunModel:
    """Build the response model for a stored run.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        run: The stored run.

    Returns:
        The `RunModel`, with the truth-base bullets of the version the run
        used as link targets.
    """
    stored = read_truth_base_version(engine, user_id, run.truth_base_version)
    sources: list[SourceBullet] = []
    if stored is not None:
        for index, role in enumerate(stored.truth_base.experience):
            label = f"{role.title} at {role.company}"
            sources.extend(
                SourceBullet(
                    bullet_id=bullet.bullet_id,
                    text=bullet.text,
                    experience_index=index,
                    role=label,
                )
                for bullet in role.bullets
            )
    return RunModel(
        run_id=run.id,
        job_group_id=run.job_group_id,
        target_title=run.target_title,
        status=run.status,
        attempts=run.attempts,
        error_message=run.error_message,
        document=run.document.model_dump() if run.document else None,
        orphans=[OrphanModel(**o.__dict__) for o in run.orphans],
        sources=sources,
    )


@router.get("/tailoring/candidates", response_model=list[CandidateModel])
def get_candidates(
    limit: int = 25,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[CandidateModel]:
    """List the user's top-scored jobs, best first.

    Args:
        limit: Maximum jobs to return.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The candidates, each with the user's latest run status, if any.
    """
    return [
        CandidateModel(**c.__dict__) for c in list_candidates(engine, user_id, limit=limit)
    ]


@router.post("/tailoring/runs", response_model=RunAccepted, status_code=202)
def post_run(
    body: RunRequest,
    background_tasks: BackgroundTasks,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> RunAccepted:
    """Start a tailoring run for one job.

    Args:
        body: The job to tailor for.
        background_tasks: Runs the loop after the response is sent.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The run's id; poll `GET /tailoring/runs/{run_id}`.

    Raises:
        HTTPException: 409 if the user has no CV, 404 if the job does not
            exist, 422 if the job has no title to mirror.
    """
    try:
        run_id = start_tailoring(engine, user_id, body.job_group_id)
    except NoCvError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnknownJobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoTargetTitleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(
        execute_tailoring, engine, user_id, run_id, adapters=adapters
    )
    return RunAccepted(run_id=run_id)


@router.get("/tailoring/runs/{run_id}", response_model=RunModel)
def get_run(
    run_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> RunModel:
    """Read a run.

    Args:
        run_id: The run.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The run, its document, orphans and link targets.

    Raises:
        HTTPException: 404 if the run does not exist for this user.
    """
    run = read_run(engine, user_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return _run_model(engine, user_id, run)


@router.get("/tailoring/jobs/{job_group_id}/latest-run", response_model=LatestRun)
def get_latest_run(
    job_group_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> LatestRun:
    """Find the user's most recent run for a job.

    Args:
        job_group_id: The job.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The latest run's id.

    Raises:
        HTTPException: 404 if the user has never tailored for this job.
    """
    for candidate in list_candidates(engine, user_id, limit=1000):
        if candidate.job_group_id == job_group_id and candidate.latest_run_id:
            return LatestRun(run_id=candidate.latest_run_id)
    raise HTTPException(status_code=404, detail="no run for this job")


@router.post("/tailoring/orphans/{orphan_id}/decision", response_model=RunModel)
def post_decision(
    orphan_id: uuid.UUID,
    body: DecisionRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> RunModel:
    """Link or reject an orphan line.

    Args:
        orphan_id: The orphan.
        body: The decision.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The updated run.

    Raises:
        HTTPException: 404 if the orphan is unknown, 409 if it is already
            decided, 422 if the decision breaks a rule (for example linking
            a bullet from another role).
    """
    orphan = read_orphan(engine, user_id, orphan_id)
    if orphan is None:
        raise HTTPException(status_code=404, detail="unknown orphan")
    if orphan.status != "pending":
        raise HTTPException(status_code=409, detail="already decided")
    run = read_run(engine, user_id, orphan.tailored_cv_id)
    stored = read_truth_base_version(
        engine, user_id, run.truth_base_version if run else 0
    )
    if run is None or run.document is None or stored is None:
        raise HTTPException(status_code=409, detail="this run has no document")
    try:
        result = apply_decision(
            run.document,
            orphan,
            action=body.action,
            evidence_ref=body.evidence_ref,
            truth_base=stored.truth_base,
        )
    except DecisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    save_decision(
        engine,
        user_id,
        orphan=orphan,
        status="linked" if body.action == "link" else "rejected",
        evidence_ref=body.evidence_ref if body.action == "link" else None,
        document=result.document,
        removed_position=result.removed_position,
    )
    updated = read_run(engine, user_id, run.id)
    assert updated is not None
    return _run_model(engine, user_id, updated)
```

Edit `apps/api/app/main.py`: add `tailoring` to the `from app.routers import (...)` list (alphabetical, after `skills`) and `app.include_router(tailoring.router)` after `app.include_router(pipeline.router)`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker exec -w /app/packages/core job_search-api-1 python -m unittest tests.integration.test_tailoring_router -v`
Expected: 17 tests OK. (A 422 from pydantic for `{"action": "shrug"}` and for a bare `link` is FastAPI's request validation, which is the behaviour the two 422 tests pin.)

- [ ] **Step 5: Lint and commit**

```bash
F="../../apps/api/app/routers/tailoring.py ../../apps/api/app/main.py tests/integration/test_tailoring_router.py"
arch -arm64 ../../venv/bin/python -m ruff check $F
arch -arm64 ../../venv/bin/python -m black $F
arch -arm64 ../../venv/bin/python -m isort $F
docker exec -w /app/packages/core job_search-api-1 python -m unittest tests.integration.test_tailoring_router 2>&1 | tail -3
git add $F
git commit -m "feat(job_search): Step 17 — /tailoring API

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The `tailor-cv` CLI command and registry exclusion

**Files:**
- Modify: `apps/pipeline/app/cli.py` (new `_cmd_tailor_cv`, parser, dispatch)
- Modify: `packages/core/tests/test_pipeline_registry.py:20` (exclusion set)
- Test: `packages/core/tests/test_pipeline_cli_tailor.py`

**Interfaces:**
- Consumes: `core.tailoring.loop.run_tailoring`, `TailoringError`, the CLI's existing `_build_llm_adapters(http_client)`.
- Produces: `tailor-cv --user-id <uuid> --job-group-id <id>`; exit code `0` when the run reaches `approved` or `needs_review`, `1` when it ends `failed` or cannot start; prints the run id, status, attempts and, when review is needed, the orphan count.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_pipeline_cli_tailor.py`:

```python
"""Unit tests for the `tailor-cv` pipeline CLI subcommand (parsing and
error handling only — the loop has its own integration tests)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

# See test_pipeline_cli_run_evals.py for why sys.modules needs no
# snapshot/restore here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402

from core.tailoring.loop import NoCvError, TailoringOutcome  # noqa: E402


class TestTailorCvSubcommand(unittest.TestCase):
    def test_is_registered(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["tailor-cv", "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_requires_both_arguments(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["tailor-cv", "--user-id", str(uuid.uuid4())])
        self.assertEqual(ctx.exception.code, 2)

    def test_a_precondition_failure_prints_the_reason_and_exits_one(self) -> None:
        out = io.StringIO()
        with mock.patch(
            "app.cli.run_tailoring", side_effect=NoCvError("this user has no CV")
        ), mock.patch("app.cli.build_engine"), mock.patch(
            "app.cli._build_llm_adapters"
        ), contextlib.redirect_stdout(out):
            exit_code = main(
                ["tailor-cv", "--user-id", str(uuid.uuid4()), "--job-group-id", "j1"]
            )
        self.assertEqual(exit_code, 1)
        self.assertIn("this user has no CV", out.getvalue())

    def test_a_failed_run_exits_one_and_a_review_run_exits_zero(self) -> None:
        run_id = uuid.uuid4()
        for status, expected in (("failed", 1), ("needs_review", 0), ("approved", 0)):
            out = io.StringIO()
            with mock.patch(
                "app.cli.run_tailoring",
                return_value=TailoringOutcome(run_id=run_id, status=status, attempts=2),
            ), mock.patch("app.cli.build_engine"), mock.patch(
                "app.cli._build_llm_adapters"
            ), contextlib.redirect_stdout(out):
                exit_code = main(
                    ["tailor-cv", "--user-id", str(uuid.uuid4()), "--job-group-id", "j1"]
                )
            self.assertEqual(exit_code, expected, status)
            self.assertIn(status, out.getvalue())
            self.assertIn(str(run_id), out.getvalue())


if __name__ == "__main__":
    unittest.main()
```

(This unit test patches `run_tailoring`, `build_engine` and `_build_llm_adapters` inside the CLI module — the module under test's own collaborators, not the database; the loop itself is covered against real Postgres in Task 9.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_pipeline_cli_tailor -v`
Expected: failures — `tailor-cv` is not a registered subcommand (`SystemExit(2)` where `0` is expected, and `AttributeError: module 'app.cli' has no attribute 'run_tailoring'`).

- [ ] **Step 3: Implement the subcommand**

In `apps/pipeline/app/cli.py`:

1. Add imports next to the existing `core.*` imports (keep isort order):
```python
from core.tailoring.loop import TailoringError, run_tailoring
```
2. Add the command function before `_EVAL_TASKS` (the comment block above `_cmd_run_evals`/`_report_eval_result`), after `_cmd_score_blend`:
```python
def _cmd_tailor_cv(args: argparse.Namespace) -> int:
    """Run the `tailor-cv` subcommand: tailor one user's CV to one job.

    On demand, not a batch stage, so it is deliberately absent from the
    pipeline dashboard (core.pipeline.registry).

    Args:
        args: Parsed CLI arguments — `user_id`, `job_group_id`.

    Returns:
        0 when the run ends `approved` or `needs_review`; 1 when it ends
        `failed` or cannot start.
    """
    settings = get_settings()
    app_engine = build_engine(settings.app_database_url)
    http_client = httpx.Client(timeout=2000.0)
    try:
        adapters = _build_llm_adapters(http_client)
        outcome = run_tailoring(
            app_engine, args.user_id, args.job_group_id, adapters=adapters
        )
    except TailoringError as exc:
        print(f"tailor-cv: {exc}")
        return 1
    finally:
        http_client.close()
    print(
        f"tailor-cv complete: run_id={outcome.run_id} status={outcome.status} "
        f"attempts={outcome.attempts}"
    )
    return 1 if outcome.status == "failed" else 0
```
3. Register the parser after the `score-blend` parser block (before `args = parser.parse_args(argv)`):
```python
    tailor_cv_parser = subparsers.add_parser(
        "tailor-cv",
        help="Tailor one user's CV to one job, with the fabrication guard "
        "(PLAN.md Step 17); on demand, not a pipeline stage",
    )
    tailor_cv_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    tailor_cv_parser.add_argument("--job-group-id", required=True)
```
4. Dispatch after the `score-blend` branch:
```python
    if args.command == "tailor-cv":
        return _cmd_tailor_cv(args)
```

- [ ] **Step 4: Update the registry completeness test's exclusion set**

In `packages/core/tests/test_pipeline_registry.py`, change line 20 to:
```python
_EXCLUDED_FROM_RUN_BUTTON = {"ingest", "run-evals", "tailor-cv"}
```
and extend the comment above it (lines 17–19) with: `# tailor-cv is on demand per (user, job) and is driven from the Tailored CV Review page, not the dashboard.`

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
arch -arm64 ../../venv/bin/python -m unittest tests.test_pipeline_cli_tailor tests.test_pipeline_registry tests.test_pipeline_cli tests.test_pipeline_cli_skills -v 2>&1 | tail -6
```
Expected: all OK. The registry's `test_excluded_stages_are_still_real_cli_subcommands` now also confirms `tailor-cv` exists in the CLI.

- [ ] **Step 6: Lint and commit**

```bash
F="../../apps/pipeline/app/cli.py tests/test_pipeline_cli_tailor.py tests/test_pipeline_registry.py"
arch -arm64 ../../venv/bin/python -m ruff check $F
arch -arm64 ../../venv/bin/python -m black $F
arch -arm64 ../../venv/bin/python -m isort $F
arch -arm64 ../../venv/bin/python -m unittest tests.test_pipeline_cli_tailor tests.test_pipeline_registry 2>&1 | tail -3
git add $F
git commit -m "feat(job_search): Step 17 — tailor-cv CLI command

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
If `black`/`isort` would reformat unrelated existing lines in `cli.py` (it has pre-existing formatting), run them only to check (`--check`/`--diff`), apply just your own hunks by hand, and commit only those.

---

### Task 12: The review page

**Files:**
- Create: `apps/ui/app/pages/10_Tailored_CV_Review.py`
- Test: `packages/core/tests/test_ui_tailored_cv_review.py`

**Interfaces:**
- Consumes: the `/tailoring/*` HTTP API (Task 10), `core.settings.get_settings().api_base_url`, `core.ui.theme.apply_theme`.
- Produces: a Streamlit page. Session keys: `tailoring_selected_job`, `tailoring_run_id`. Widget keys: `tailor-start` (the Tailor button), `link-select-{orphan_id}`, `link-{orphan_id}`, `reject-{orphan_id}`.

Page behaviour: (1) candidates `st.selectbox` showing `title — company — score (latest status)`; (2) a **Tailor** button posts to `/tailoring/runs`, stores the returned run id, and reruns; (3) while the run is `generating` the page polls every 3 s (like the dashboard); (4) when a run exists it shows status, attempts, an error box on `failed`, a stretch warning, keyword coverage, then the tailored CV — each bullet as plain text (`st.text`, never markdown, so job/CV text can't inject links) with a caption of its origin and source ids; (5) pending orphans each show the text, the issue, a source-bullet `selectbox` (for an experience orphan only the same role's bullets; for the summary all) with **Link** and **Reject** buttons posting to the decision endpoint; HTTP errors show the API's `detail`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_ui_tailored_cv_review.py`:

```python
"""Headless render tests for the Tailored CV Review page (Streamlit AppTest)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import httpx
from streamlit.testing.v1 import AppTest

_PAGE = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "ui"
    / "app"
    / "pages"
    / "10_Tailored_CV_Review.py"
)

_RUN_ID = "11111111-1111-1111-1111-111111111111"
_ORPHAN_ID = "22222222-2222-2222-2222-222222222222"

_CANDIDATE = {
    "job_group_id": "zzfixture-job",
    "title_for_display": "Lead Data Engineer",
    "company": "Gamma",
    "final_score": 0.81,
    "latest_run_id": _RUN_ID,
    "latest_status": "needs_review",
}
_DOCUMENT = {
    "target_title": "Lead Data Engineer",
    "headline": "Lead Data Engineer",
    "identity": "Zz Fixture",
    "summary": {"text": "Data engineer.", "evidence_refs": ["b1"], "origin": "reworded"},
    "experience": [
        {
            "truth_index": 0,
            "company": "Acme Bank",
            "title": "Senior Data Engineer",
            "start": "2019-01",
            "end": None,
            "tech": [],
            "bullets": [
                {
                    "text": "Built dbt models powering risk reporting",
                    "evidence_refs": ["b1"],
                    "origin": "reworded",
                },
                {
                    "text": "[click me](http://evil.example) **x**",
                    "evidence_refs": [],
                    "origin": "orphan",
                },
            ],
        },
        {
            "truth_index": 1,
            "company": "Beta Retail",
            "title": "Data Analyst",
            "start": "2015-06",
            "end": "2018-12",
            "tech": [],
            "bullets": [],
        },
    ],
    "skills": [{"name": "dbt", "canonical_id": None, "years": None,
                "last_used": None, "evidence_refs": []}],
    "stretch": {"is_stretch": True, "reason": "Lead implies managing people"},
    "keyword_coverage": {
        "covered": ["dbt"],
        "missing_evidenced": ["SQL"],
        "missing_unevidenced": ["Kubernetes"],
    },
}
_RUN = {
    "run_id": _RUN_ID,
    "job_group_id": "zzfixture-job",
    "target_title": "Lead Data Engineer",
    "status": "needs_review",
    "attempts": 3,
    "error_message": None,
    "document": _DOCUMENT,
    "orphans": [
        {
            "id": _ORPHAN_ID,
            "kind": "orphan",
            "section": "experience",
            "experience_index": 0,
            "bullet_index": 1,
            "text": "[click me](http://evil.example) **x**",
            "claimed_refs": [],
            "issue": "no evidence_ref in the CV",
            "status": "pending",
            "evidence_ref": None,
        }
    ],
    "sources": [
        {"bullet_id": "b1", "text": "Built dbt models for risk reporting",
         "experience_index": 0, "role": "Senior Data Engineer at Acme Bank"},
        {"bullet_id": "b2", "text": "Wrote SQL reports",
         "experience_index": 1, "role": "Data Analyst at Beta Retail"},
    ],
}


def _fake_get(candidates, run):
    def _fake(url: str, **_kwargs) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url.endswith("/tailoring/candidates") or "/tailoring/candidates?" in url:
            return httpx.Response(200, json=candidates, request=request)
        if "/tailoring/runs/" in url:
            if run is None:
                return httpx.Response(404, json={"detail": "unknown run"}, request=request)
            return httpx.Response(200, json=run, request=request)
        return httpx.Response(200, json=[], request=request)

    return _fake


class TestTailoredCvReviewPage(unittest.TestCase):
    def test_renders_with_no_candidates(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([], None)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("No scored jobs", " ".join(c.value for c in app.info))

    def test_shows_candidates_and_a_tailor_button(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], None)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("tailor-start", [b.key for b in app.button])
        self.assertTrue(
            any("Lead Data Engineer" in o for o in app.selectbox[0].options)
        )

    def test_shows_the_run_the_stretch_warning_and_keyword_coverage(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        warnings = " ".join(w.value for w in app.warning)
        self.assertIn("Lead implies managing people", warnings)
        shown = " ".join(m.value for m in app.markdown) + " ".join(
            c.value for c in app.caption
        )
        self.assertIn("SQL", shown)
        self.assertIn("Kubernetes", shown)

    def test_shows_each_pending_orphan_with_link_and_reject(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        keys = [b.key for b in app.button]
        self.assertIn(f"link-{_ORPHAN_ID}", keys)
        self.assertIn(f"reject-{_ORPHAN_ID}", keys)

    def test_the_link_picker_offers_only_the_same_roles_bullets(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        picker = next(s for s in app.selectbox if s.key == f"link-select-{_ORPHAN_ID}")
        joined = " ".join(picker.options)
        self.assertIn("Built dbt models for risk reporting", joined)
        self.assertNotIn("Wrote SQL reports", joined)

    def test_cv_and_orphan_text_is_rendered_as_plain_text_not_markdown(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        markdown = " ".join(m.value for m in app.markdown)
        self.assertNotIn("[click me](http://evil.example)", markdown)
        self.assertIn(
            "[click me](http://evil.example) **x**", " ".join(t.value for t in app.text)
        )

    def test_a_failed_run_shows_its_error_and_no_orphan_controls(self) -> None:
        failed = {**_RUN, "status": "failed", "document": None, "orphans": [],
                  "error_message": "CriticError: unusable critic reply"}
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], failed)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("unusable critic reply", " ".join(e.value for e in app.error))
        self.assertFalse([b for b in app.button if b.key.startswith("link-")])

    def test_an_approved_run_has_no_orphan_controls(self) -> None:
        approved = {**_RUN, "status": "approved", "orphans": []}
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], approved)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertFalse([b for b in app.button if b.key.startswith("reject-")])
        self.assertIn("approved", " ".join(m.value for m in app.markdown).lower())

    def test_clicking_link_posts_the_decision(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)), mock.patch(
            "httpx.post"
        ) as post:
            post.return_value = httpx.Response(
                200, json=_RUN, request=httpx.Request("POST", "http://x")
            )
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            link = next(b for b in app.button if b.key == f"link-{_ORPHAN_ID}")
            link.click().run()
        url = post.call_args.args[0]
        self.assertTrue(url.endswith(f"/tailoring/orphans/{_ORPHAN_ID}/decision"))
        self.assertEqual(post.call_args.kwargs["json"]["action"], "link")
        self.assertEqual(post.call_args.kwargs["json"]["evidence_ref"], "b1")

    def test_an_api_rejection_shows_the_detail(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _RUN)), mock.patch(
            "httpx.post"
        ) as post:
            post.return_value = httpx.Response(
                422,
                json={"detail": "the evidence must come from the same role"},
                request=httpx.Request("POST", "http://x"),
            )
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == f"link-{_ORPHAN_ID}").click().run()
        self.assertIn("same role", " ".join(e.value for e in app.error))

    def test_the_api_being_down_shows_an_error_not_a_crash(self) -> None:
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_ui_tailored_cv_review -v`
Expected: errors — the page file does not exist.

- [ ] **Step 3: Write the page**

Create `apps/ui/app/pages/10_Tailored_CV_Review.py`:

```python
"""Tailored CV review — pick a top-scored job, tailor your CV to it, and
decide every line the fabrication guard could not trace to your CV
(PLAN.md Step 17).
"""

from __future__ import annotations

import time

import httpx
import streamlit as st

from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Tailored CV Review", layout="wide")
apply_theme()
st.title("Tailored CV Review")

with st.expander("User manual"):
    st.markdown(
        """
Pick one of your top-scored jobs and click **Tailor**. The tailored CV is
built from your CV: companies, titles and dates are copied, never
generated, and the headline is exactly the job's title. A reviewer model
then checks that every reworded bullet claims nothing its source bullets
don't.

Anything that can't be traced to your CV shows up under **Needs your
decision**. For each line either **Link** it to the bullet in your CV that
evidences it (the case where your CV states it obliquely), or **Reject**
it. Nothing is written to your CV itself. The tailored CV is **approved**
once no line is waiting.

A **stretch** warning means the job's title implies more seniority or
scope than your CV shows. That is advice, not an error.
"""
    )

_settings = get_settings()
_base = _settings.api_base_url
_POLL_SECONDS = 3

_flash = st.session_state.pop("tailoring_flash", None)
if _flash:
    st.error(_flash)


def _get(path: str) -> httpx.Response:
    """GET from the API.

    Args:
        path: The path, starting with `/`.

    Returns:
        The response.
    """
    return httpx.get(f"{_base}{path}", timeout=10.0)


def _post(path: str, json: dict) -> httpx.Response:
    """POST to the API.

    Args:
        path: The path, starting with `/`.
        json: The JSON body.

    Returns:
        The response.
    """
    return httpx.post(f"{_base}{path}", json=json, timeout=10.0)


def _detail(response: httpx.Response) -> str:
    """Pull the API's error message out of a failed response.

    Args:
        response: A non-2xx response.

    Returns:
        The `detail` field when present, else the status code.
    """
    try:
        return str(response.json().get("detail", response.status_code))
    except ValueError:
        return f"HTTP {response.status_code}"


def _label(candidate: dict) -> str:
    """Build a candidate's picker label.

    Args:
        candidate: One item from `GET /tailoring/candidates`.

    Returns:
        `title — company — score (latest status)`.
    """
    status = f" ({candidate['latest_status']})" if candidate["latest_status"] else ""
    return (
        f"{candidate['title_for_display'] or '(no title)'} — "
        f"{candidate['company'] or 'unknown company'} — "
        f"{candidate['final_score']:.2f}{status}"
    )


try:
    response = _get("/tailoring/candidates")
    response.raise_for_status()
    candidates = response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load your jobs: {exc}")
    st.stop()

if not candidates:
    st.info(
        "No scored jobs yet — run the scoring stages from the Pipeline "
        "Dashboard first."
    )
    st.stop()

labels = {c["job_group_id"]: _label(c) for c in candidates}
selected = st.selectbox(
    "Job",
    options=list(labels),
    format_func=lambda job_id: labels[job_id],
    key="tailoring_selected_job",
)
chosen = next(c for c in candidates if c["job_group_id"] == selected)

if st.button("Tailor my CV to this job", key="tailor-start", type="primary"):
    started = _post("/tailoring/runs", {"job_group_id": selected})
    if started.status_code == 202:
        st.session_state["tailoring_run_id"] = started.json()["run_id"]
    else:
        st.session_state["tailoring_flash"] = f"Could not start: {_detail(started)}"
    st.rerun()

run_id = st.session_state.get("tailoring_run_id") or chosen["latest_run_id"]
if not run_id:
    st.caption("Not tailored yet.")
    st.stop()

try:
    run_response = _get(f"/tailoring/runs/{run_id}")
    run_response.raise_for_status()
    run = run_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load the tailored CV: {exc}")
    st.stop()

if run["job_group_id"] != selected:
    # A stale run id from another job — fall back to this job's latest.
    st.session_state.pop("tailoring_run_id", None)
    if chosen["latest_run_id"]:
        st.session_state["tailoring_run_id"] = chosen["latest_run_id"]
        st.rerun()
    st.caption("Not tailored yet.")
    st.stop()

status = run["status"]
st.markdown(f"**Status:** {status} · attempts: {run['attempts']}")

if status == "generating":
    st.caption("Working… this page refreshes on its own.")
    time.sleep(_POLL_SECONDS)
    st.rerun()

if status == "failed":
    st.error(f"This run failed: {run['error_message']}")
    st.stop()

document = run["document"]
if document is None:
    st.stop()

if document["stretch"]["is_stretch"]:
    st.warning(f"Stretch: {document['stretch']['reason']}")

coverage = document["keyword_coverage"]
st.subheader("Keyword coverage")
st.caption(f"Covered: {', '.join(coverage['covered']) or 'none'}")
if coverage["missing_evidenced"]:
    st.caption(
        "Your CV evidences these, but the tailored CV doesn't show them: "
        + ", ".join(coverage["missing_evidenced"])
    )
if coverage["missing_unevidenced"]:
    st.caption(
        "The job asks for these, but your CV doesn't evidence them (never "
        "added): " + ", ".join(coverage["missing_unevidenced"])
    )

st.subheader("Tailored CV")
st.text(document["headline"])
if document["summary"]:
    st.text(document["summary"]["text"])
    st.caption(f"summary · {document['summary']['origin']}")
for role in document["experience"]:
    st.markdown("---")
    st.text(f"{role['title']} — {role['company']}")
    st.caption(f"{role['start'] or '?'} – {role['end'] or 'present'}")
    for bullet in role["bullets"]:
        st.text(bullet["text"])
        refs = ", ".join(bullet["evidence_refs"]) or "no source"
        st.caption(f"{bullet['origin']} · source: {refs}")
st.markdown("---")
st.text("Skills: " + ", ".join(skill["name"] for skill in document["skills"]))

pending = [o for o in run["orphans"] if o["status"] == "pending"]
if status == "approved":
    st.success("Approved — every line traces to your CV.")
if pending:
    st.subheader("Needs your decision")
sources = run["sources"]
for orphan in pending:
    st.markdown("---")
    where = (
        "Summary"
        if orphan["section"] == "summary"
        else f"Role {orphan['experience_index']}, bullet {orphan['bullet_index']}"
    )
    st.text(f"{where} ({orphan['kind']})")
    st.text(orphan["text"])
    if orphan["issue"]:
        st.caption(orphan["issue"])
    options = [
        s
        for s in sources
        if orphan["section"] == "summary"
        or s["experience_index"] == orphan["experience_index"]
    ]
    choice = st.selectbox(
        "Evidenced by",
        options=options,
        format_func=lambda s: f"{s['role']}: {s['text']}",
        key=f"link-select-{orphan['id']}",
    )
    link_col, reject_col, _ = st.columns([1, 1, 4])
    if link_col.button(
        "Link", key=f"link-{orphan['id']}", disabled=choice is None
    ):
        decided = _post(
            f"/tailoring/orphans/{orphan['id']}/decision",
            {"action": "link", "evidence_ref": choice["bullet_id"]},
        )
        if decided.status_code != 200:
            st.session_state["tailoring_flash"] = _detail(decided)
        st.rerun()
    if reject_col.button("Reject", key=f"reject-{orphan['id']}"):
        decided = _post(
            f"/tailoring/orphans/{orphan['id']}/decision", {"action": "reject"}
        )
        if decided.status_code != 200:
            st.session_state["tailoring_flash"] = _detail(decided)
        st.rerun()
```

Note: `test_an_api_rejection_shows_the_detail` renders the flash on the *next* run; AppTest's `.click().run()` performs that rerun, so the error is present afterwards.

- [ ] **Step 4: Run the test to verify it passes**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.test_ui_tailored_cv_review -v`
Expected: 10 tests OK. If `test_clicking_link_posts_the_decision` fails because `st.rerun()` inside a click re-fetches via the mocked `httpx.get`, that is expected to work (the fake `get` serves the same run again); the assertion reads `post.call_args` from the click run.

- [ ] **Step 5: Render the whole UI once and lint**

```bash
arch -arm64 ../../venv/bin/python /dev/stdin <<'EOF'
import glob
from streamlit.testing.v1 import AppTest
from unittest import mock
import httpx
for f in sorted(glob.glob("../../apps/ui/app/pages/*.py")):
    if f.endswith("10_Tailored_CV_Review.py"):
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("down")):
            at = AppTest.from_file(f, default_timeout=30).run()
        print(f.split("/")[-1], "exceptions:", len(at.exception))
EOF
F="../../apps/ui/app/pages/10_Tailored_CV_Review.py tests/test_ui_tailored_cv_review.py"
arch -arm64 ../../venv/bin/python -m ruff check $F
arch -arm64 ../../venv/bin/python -m black $F
arch -arm64 ../../venv/bin/python -m isort $F
git add $F
git commit -m "feat(job_search): Step 17 — Tailored CV Review page

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
Expected: `exceptions: 0`; ruff clean.

---

### Task 13: Adversarial tests — the plan's "done when"

**Files:**
- Test: `packages/core/tests/integration/test_tailoring_adversarial.py`

**Interfaces:**
- Consumes: `run_tailoring`, `read_run`, `apply_decision`, `save_decision`, a real `AnthropicAdapter`.
- Produces: the plan's acceptance test: a deliberate prompt toward exaggeration is caught by the critic and surfaced, never emitted as approved.

Two variants. **Offline** (always runs): an exaggerating fake Tailor against a fake critic that applies the stated rule deterministically (any digit or capitalised token not in the sources is unsupported) — proves the loop's surfacing logic and that nothing exaggerated is ever approved. **Paid** (`RUN_PAID_TESTS=1`): the same exaggerating Tailor against the **real Claude critic** — the plan's actual criterion — plus an honest-rewording control. Paid tests make real Anthropic calls.

- [ ] **Step 1: Write the tests**

Create `packages/core/tests/integration/test_tailoring_adversarial.py`:

```python
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
            {"text": "Migrated nightly batch jobs to Airflow", "evidence_refs": [self.ref1]},
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
                    "issue": f"adds numbers not in the source: {sorted(extra)}"
                    if extra
                    else "",
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
                text("DELETE FROM cv_truth_base WHERE user_id = :u"), {"u": self.user_id}
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = :j"), {"j": _JOB}
            )
            conn.execute(text("DELETE FROM app_user WHERE id = :u"), {"u": self.user_id})

    def _run(self, tailor, critic):
        outcome = run_tailoring(
            self.app_engine,
            self.user_id,
            _JOB,
            adapters={"ollama": tailor, "anthropic": critic},
        )
        return outcome, read_run(self.app_engine, self.user_id, outcome.run_id)

    def _assert_exaggeration_was_surfaced(self, outcome, run) -> None:
        self.assertEqual(outcome.status, "needs_review")
        flagged = [o for o in run.orphans if o.kind == "unsupported"]
        self.assertEqual([o.text for o in flagged], [_EXAGGERATED])
        self.assertEqual(flagged[0].status, "pending")
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
            removed_position=result.removed_position,
        )
        after = read_run(self.app_engine, self.user_id, run.id)
        texts = [b.text for b in after.document.experience[0].bullets]
        self.assertNotIn(_EXAGGERATED, texts)
        self.assertIn("Built dbt models for risk reporting", texts)  # reverted
        self.assertEqual(after.status, "approved")


class TestExaggerationIsCaughtOffline(_Base):
    def test_an_exaggerated_bullet_is_surfaced_and_never_approved(self) -> None:
        outcome, run = self._run(
            _ExaggeratingTailor(self.truth_base), _RuleApplyingCritic()
        )
        self._assert_exaggeration_was_surfaced(outcome, run)

    def test_rejecting_it_reverts_to_the_users_own_wording_and_approves(self) -> None:
        _, run = self._run(_ExaggeratingTailor(self.truth_base), _RuleApplyingCritic())
        self._reject_and_check_reversion(run)

    def test_an_honest_rewording_next_to_it_is_not_flagged(self) -> None:
        outcome, run = self._run(
            _ExaggeratingTailor(self.truth_base, honest_second=True),
            _RuleApplyingCritic(),
        )
        self.assertEqual([o.text for o in run.orphans], [_EXAGGERATED])


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
        _, run = self._run(
            _ExaggeratingTailor(self.truth_base, honest_second=True),
            self._real_critic(),
        )
        self.assertNotIn(_HONEST, [o.text for o in run.orphans])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the offline tests**

Run: `arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_adversarial -v`
Expected: the three offline tests PASS; the two paid tests are `skipped`.

If `_RuleApplyingCritic` raises `ValueError: substring not found` the critic prompt's wording changed: the fake locates the items by the literal phrase `JSON list` in `prompts/fabrication_critic/claude.v1.md` ("Below is a JSON list of generated CV lines"). Keep that phrase in the prompt (the test depends on it) or update the fake.

- [ ] **Step 3: Run the paid tests (needs a go-ahead — real, small Anthropic spend)**

Run only with the user's explicit go-ahead (a handful of short `claude-sonnet-5` calls):
`RUN_PAID_TESTS=1 arch -arm64 ../../venv/bin/python -m unittest tests.integration.test_tailoring_adversarial -v`
Expected: all five tests PASS. If `test_the_real_critic_does_not_flag_an_honest_rewording` fails, that is a *finding* about the critic prompt (a false positive), not a flaky test — report it and tune `prompts/fabrication_critic/claude.v1.md` (bump to `claude.v2.md`, never edit v1 in place, and bump `PROMPT_VERSION_NUMBER`).

- [ ] **Step 4: Lint and commit**

```bash
F="tests/integration/test_tailoring_adversarial.py"
arch -arm64 ../../venv/bin/python -m ruff check $F
arch -arm64 ../../venv/bin/python -m black $F
arch -arm64 ../../venv/bin/python -m isort $F
git add $F
git commit -m "test(job_search): Step 17 — adversarial tests for the fabrication guard

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Docs, spec amendment and full regression

**Files:**
- Modify: `README.md` (new "Tailored CV (Step 17)" section)
- Modify: `docs/superpowers/specs/2026-10-01-step17-tailoring-design.md` (one amendment)

**Interfaces:** none (documentation and verification).

- [ ] **Step 1: Amend the spec**

In the spec's "Checks (code only, `checks.py`)" section, replace item 5 (`Content-level ATS rules only …`) with:

```
5. Content-level ATS hygiene is enforced **mechanically at assembly**
   (`assemble.clean_text`: leading bullet glyphs, decorative symbols and
   emoji are stripped from generated text), not checked after the fact —
   there is nothing to retry because the fix is deterministic. Layout rules
   belong to the template in 18a.
```
and in "Testing" nothing changes. Add one line under "Out of scope": `Layout-level ATS rules (single column, no tables, headings, date format) — Step 18a.`

- [ ] **Step 2: Add the README section**

Append to `README.md`:

```markdown
## Tailored CV (Step 17)

Tailors your CV to one of your top-scored jobs with a fabrication guard:
every generated line must trace to a bullet in your CV, and anything that
doesn't is shown to you for an explicit decision. Design:
[docs/superpowers/specs/2026-10-01-step17-tailoring-design.md](docs/superpowers/specs/2026-10-01-step17-tailoring-design.md).

- **UI:** the *Tailored CV Review* page — pick a job, click Tailor, then
  Link or Reject each line under "Needs your decision".
- **CLI:** `docker compose run --rm pipeline tailor-cv --user-id <id>
  --job-group-id <id>` (on demand; deliberately not a dashboard stage).
- **API:** `/tailoring/candidates`, `/tailoring/runs`, `/tailoring/runs/{id}`,
  `/tailoring/orphans/{id}/decision`.

How it works: code assembles the CV from your truth base (companies, titles
and dates are copied, never generated; the headline is the job's
`title_for_display`); the `cv_tailoring` model only returns per-bullet
wording and the bullet ids it draws on; code checks and the
`fabrication_critic` (always Claude) verify it; the loop retries at most
twice. Accepting an orphan means linking it to an existing CV bullet — your
CV is never modified.

Cost: the critic makes one Claude call per attempt (a few cents at most).
The Tailor is local (Ollama) by default; to use Claude instead, change the
`cv_tailoring` entry in `config/llm_tasks.yml` (provider `anthropic`, prompt
family `claude`).

The `fabrication_critic` task **must** stay on `anthropic`: the critic
refuses to run otherwise, and a test asserts it.

The paid adversarial test (a deliberately exaggerating Tailor against the
real critic) runs with `RUN_PAID_TESTS=1`.

This step produces approved *content* only. The ATS `.docx` and the designed
PDF are Steps 18a and 18b.
```

- [ ] **Step 3: Run the whole new test surface**

```bash
# host (prelude exported, in packages/core)
arch -arm64 ../../venv/bin/python -m unittest \
  tests.test_tailoring_schema tests.test_tailoring_assemble tests.test_tailoring_checks \
  tests.test_tailoring_tailor tests.test_tailoring_critic tests.test_tailoring_decisions \
  tests.integration.test_tailoring_schema tests.integration.test_tailoring_context \
  tests.integration.test_tailoring_store tests.integration.test_tailoring_loop \
  tests.integration.test_tailoring_adversarial tests.test_ui_tailored_cv_review \
  tests.test_pipeline_cli_tailor tests.test_pipeline_registry 2>&1 | tail -5
# API router (container)
docker exec -w /app/packages/core job_search-api-1 python -m unittest tests.integration.test_tailoring_router 2>&1 | tail -3
```
Expected: `OK` (paid tests skipped) and the router suite `OK`.

- [ ] **Step 4: Regression — nothing else got worse**

Run the existing suites most likely to be affected, on host and in the container, and compare to the known baseline (`tests.test_ui_skill_review`: 11 failures + 7 errors on `main`, unchanged):

```bash
arch -arm64 ../../venv/bin/python -m unittest tests.test_pipeline_registry tests.test_pipeline_descriptions tests.test_pipeline_cli tests.test_pipeline_cli_skills tests.test_pipeline_cli_run_evals tests.integration.test_pipeline_runner tests.integration.test_pipeline_staleness 2>&1 | tail -3
docker exec -w /app/packages/core job_search-api-1 python -m unittest tests.integration.test_pipeline_router tests.integration.test_scoring_router_calibration tests.integration.test_cv_router 2>&1 | tail -3
arch -arm64 ../../venv/bin/python -m unittest tests.test_ui_skill_review 2>&1 | tail -1
```
Expected: the first two `OK`; the last still `FAILED (failures=11, errors=7)` — identical to the baseline. Any other change is a regression to fix before committing.

- [ ] **Step 5: Final manual pass (UI)**

With the UI container running and `DEV_USER_ID` set in `.env` for a user who has a truth base and scored jobs: open the *Tailored CV Review* page, pick a job and click Tailor. Confirm: the page polls, shows the tailored CV with the headline equal to the job title, and (if any) a "Needs your decision" list whose Link/Reject buttons work. Record what you saw in the commit message. If the local model produces poor output, say so — switching to Claude is the one-line config change in the README.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-01-step17-tailoring-design.md
git commit -m "docs(job_search): Step 17 — README section and spec amendment

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review (run against the spec)

**Spec coverage**

| Spec requirement | Task |
|---|---|
| Decisions: backend + UI, on-demand from top-scored jobs, link-to-source, assemble-don't-generate, Claude critic | Tasks 1–12 (architecture), 7/10/12 (trigger), 8 (link), 3 (assemble), 6 (critic) |
| `TailoredDocument` model, injected title, copied roles/dates | Task 2, 3 |
| Tailor task `cv_tailoring`, prompts, JSON output | Task 5 |
| Checks 1–4 (refs exist, experience unchanged, headline exact, keyword coverage evidenced/unevidenced) | Task 4 |
| Check 5 (ATS content) | Task 3 (`clean_text`, mechanical) + spec amendment Task 14 |
| Critic: Anthropic assertion, per-reworded-line verdict, stretch advisory | Task 6 |
| Loop: ≤2 retries, feedback, never approve unchecked, orphans for leftovers | Task 9 |
| Orphan decisions: link (same role), reject (revert unsupported single-source), approved when none pending | Task 8 |
| Persistence tables (RLS), versions recorded | Task 1, 8 |
| API endpoints, background run, polling | Task 10 |
| CLI + registry exclusion | Task 11 |
| UI page (picker, source beside bullet, orphan controls, stretch, coverage) | Task 12 |
| Tests: unit, fake-adapter loop, config assert, integration/RLS, adversarial offline + paid, AppTest | Tasks 2–13 |
| Decomposition (18a/18b out of scope) | Global Constraints, Task 14 README |

**Placeholder scan:** no TBD/TODO; two notes tell the implementer to delete a stray prefix/unused import in a test file — both are explicit, with the exact text.

**Type consistency:** `Problem`/`STRUCTURAL_CODES` (Task 4) are used with the same names in Task 9; `OrphanDraft`/`StoredOrphan`/`StoredRun`/`save_decision`/`read_orphan` (Task 8) match their use in Tasks 9, 10, 13; `JobContext`/`JobSkill` (Task 2) match Tasks 4–7, 9; `TailoringOutcome`, `start_tailoring`, `execute_tailoring`, `run_tailoring` (Task 9) match Tasks 10, 11; item/location ids `summary` / `e{role}b{bullet}` are the same in Tasks 4, 6, 9. `PROMPT_VERSION_NUMBER` is defined in both `tailor.py` and `critic.py` independently (separate prompt families).

**Review Focus coverage:** (1) cross-role → Tasks 4, 8 (link refusal), 9; (2) critic missing/unparseable → Tasks 6, 9; (3) null title → Tasks 9, 10; (4) no CV / malformed Tailor output → Tasks 9, 10; (5) role index edge cases → Task 3.

**Known limitations to state at execution time**
- A failed run records `attempts = max_retries + 1` regardless of how far it got (Task 9 note).
- The first real-model run will show whether `llama3.1:8b` tailors acceptably; the plan does not promise it.
- `GET /tailoring/jobs/{id}/latest-run` scans up to 1000 candidates; fine at current scale, revisit if a user has more scored jobs.
