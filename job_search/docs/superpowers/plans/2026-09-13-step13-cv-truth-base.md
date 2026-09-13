# Step 13 — CV Truth Base (JOB-202) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the CV truth base: a Pydantic schema with stable bullet
IDs, Docling+LLM extraction from a real CV PDF, DB-enforced one-CV-per-
user versioned storage, a Streamlit correction pass, and a `cv_extraction`
entry in Step 12a's eval harness.

**Architecture:** Docling converts a CV PDF to markdown; one LLM call
(routed to Ollama, never Anthropic — see Global Constraints) parses that
markdown into a `CVTruthBase` Pydantic model; bullet IDs are computed
deterministically from bullet text/position, never trusted from the LLM.
Storage is two tables (`cv_truth_base` current + `cv_truth_base_history`
append-only), RLS-enforced like every other per-user table in this
project. A FastAPI router and a Streamlit page expose read/write/extract
and a manual correction pass.

**Tech Stack:** Python 3.11, Pydantic v2, Docling, SQLAlchemy + Alembic,
FastAPI, Streamlit, the existing `core.llm` gateway/task-config machinery.

**Spec:** `docs/superpowers/specs/2026-09-13-step13-cv-truth-base-design.md`

## Global Constraints

- Docling pin: `docling==2.126.0`. **Already verified** in a scratch venv
  against this project's exact `requirements.txt` pins (minus dbt, which
  is never needed for Python unit/integration tests) — installs with
  zero `pip check` conflicts, and `DocumentConverter().convert(stream)
  .document.export_to_markdown()` with `docling.datamodel.base_models
  .DocumentStream` was smoke-tested end-to-end against a synthetic HTML
  snippet and produced correct markdown. Task 1 repeats this install in
  the project's real venv (not the scratch one) so it's part of the
  tracked dependency set, not a re-verification of feasibility.
- `cv_extraction` is a **local-only** task: `config/llm_tasks.yml` routes
  it to `provider: ollama`, never `anthropic` — per DECISIONS.md's task
  split ("CV truth-base extraction: local, hand-corrected anyway, never
  migrates"). No task in this plan may route this task to Anthropic.
- Every per-user table gets `ENABLE ROW LEVEL SECURITY` plus a policy on
  `current_setting('app.current_user_id', true)::uuid` — the exact
  two-statement pattern in migrations 0001/0002. Every query against
  `cv_truth_base`/`cv_truth_base_history` goes through
  `core.db.session.session_scope(engine, user_id=...)` — never a bare
  `engine.connect()` on the app-role engine.
- The real CV (`~/Documents/Cv_mine/Frederic_Marechal_2026_v3.pdf`) is
  **never** committed to this repository in any form — not as a fixture,
  not as a golden-set case, not in a test, not in a doc excerpt. Every
  committed CV-shaped fixture (golden-set cases, integration-test
  fixtures) is synthetic, invented text with no real personal data.
  Verifying Docling against the real CV is a manual step the plan calls
  out explicitly (end of this document) — not a task any subagent runs.
- Bullet IDs are computed by this codebase (`compute_bullet_id`), never
  taken from an LLM response — see Task 2.
- Follow `.claude/rules/python-style.md` (Google docstrings, 88-column
  black, isort, ruff) and `.claude/rules/python-testing.md` (unittest +
  coverage, no DB mocks in integration tests, run from
  `packages/core/`).
- `evals/golden/cv_extraction.yml` needs **at least 20 cases** —
  `core.evals.runner.MINIMUM_GOLDEN_SET_SIZE = 20` — or `run-evals
  cv_extraction` reports `insufficient_data` instead of a real score.

---

### Task 1: Add and verify the Docling dependency

**Files:**
- Modify: `requirements.txt`
- Test: `packages/core/tests/test_docling_import.py`

**Interfaces:**
- Produces: a working `docling` import in this project's venv, for every
  later task to build on.

- [ ] **Step 1: Write the failing test**

```python
"""Smoke test: docling must be importable and expose DocumentConverter."""

from __future__ import annotations

import unittest


class TestDoclingImport(unittest.TestCase):
    def test_document_converter_is_importable(self) -> None:
        from docling.document_converter import DocumentConverter

        self.assertTrue(callable(DocumentConverter))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.test_docling_import -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'docling'`) — the
dependency isn't installed yet.

- [ ] **Step 3: Add the dependency and install it**

In `requirements.txt`, add one line after `fsspec==2024.10.0`:

```
docling==2.126.0
```

Then, in this project's venv: `pip install -r requirements.txt`. This
has already been verified conflict-free in a scratch venv against this
exact pin set (minus dbt, which install/test never needs), so this step
should simply succeed — if it doesn't, something about the real venv
differs from the scratch one; report the exact error rather than
forcing the install.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/core && python -m unittest tests.test_docling_import -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements.txt packages/core/tests/test_docling_import.py
git commit -m "chore(job_search): add docling dependency for CV extraction (JOB-202)"
```

---

### Task 2: CV truth-base schema and stable bullet IDs

**Files:**
- Create: `packages/core/core/cv/__init__.py` (empty)
- Create: `packages/core/core/cv/schema.py`
- Create: `packages/core/core/cv/bullet_id.py`
- Test: `packages/core/tests/test_cv_schema.py`
- Test: `packages/core/tests/test_bullet_id.py`

**Interfaces:**
- Produces: `core.cv.schema.{Bullet, Experience, Skill, Education,
  Certification, Publication, CVTruthBase}` (all Pydantic `BaseModel`),
  and `core.cv.bullet_id.compute_bullet_id(experience_index: int, text:
  str) -> str`. Every later task imports these.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_bullet_id.py`:

```python
"""compute_bullet_id must be deterministic and position/content-sensitive."""

from __future__ import annotations

import unittest

from core.cv.bullet_id import compute_bullet_id


class TestComputeBulletId(unittest.TestCase):
    def test_same_text_and_position_yields_the_same_id(self) -> None:
        first = compute_bullet_id(0, "Led a team of 3 data engineers.")
        second = compute_bullet_id(0, "Led a team of 3 data engineers.")
        self.assertEqual(first, second)

    def test_different_position_yields_a_different_id(self) -> None:
        same_text = "Led a team of 3 data engineers."
        self.assertNotEqual(
            compute_bullet_id(0, same_text), compute_bullet_id(1, same_text)
        )

    def test_different_text_yields_a_different_id(self) -> None:
        self.assertNotEqual(
            compute_bullet_id(0, "Led a team of 3 data engineers."),
            compute_bullet_id(0, "Led a team of 5 data engineers."),
        )

    def test_whitespace_and_case_differences_do_not_change_the_id(self) -> None:
        first = compute_bullet_id(0, "Led   a team of 3 data engineers.")
        second = compute_bullet_id(0, "led a team of 3 data engineers.")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
```

`packages/core/tests/test_cv_schema.py`:

```python
"""CVTruthBase round-trips through JSON without losing data."""

from __future__ import annotations

import unittest

from core.cv.schema import Bullet, CVTruthBase, Experience, Skill


class TestCVTruthBaseRoundTrip(unittest.TestCase):
    def test_round_trips_to_json_and_back(self) -> None:
        original = CVTruthBase(
            identity="Jane Doe",
            headline="Senior Data Engineer",
            locations=["London, UK"],
            work_auth="UK citizen",
            skills=[Skill(name="Python", years=10.0)],
            experience=[
                Experience(
                    company="Acme Corp",
                    title="Data Engineer",
                    start="2020-01",
                    end=None,
                    bullets=[Bullet(bullet_id="abc123", text="Built a pipeline.")],
                    tech=["Python", "Postgres"],
                    metrics=["50% faster"],
                )
            ],
        )
        restored = CVTruthBase.model_validate_json(original.model_dump_json())
        self.assertEqual(restored, original)

    def test_optional_fields_default_to_empty(self) -> None:
        minimal = CVTruthBase(identity="Jane Doe", headline="Engineer")
        self.assertEqual(minimal.skills, [])
        self.assertEqual(minimal.experience, [])
        self.assertIsNone(minimal.work_auth)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/core && python -m unittest tests.test_bullet_id tests.test_cv_schema -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'core.cv'`)

- [ ] **Step 3: Implement `bullet_id.py`**

```python
"""Deterministic, stable bullet IDs (PLAN.md Step 13).

A bullet's ID is computed from its own text and its position within its
experience entry — never taken from an LLM's output. This is what lets
an ID "survive re-extraction" (PLAN.md's "Done when"): re-running
extraction on an unchanged CV reproduces the same IDs for unchanged
bullets, since both inputs to the hash are stable. A bullet whose text
changes gets a new ID by design — Step 17's fabrication guard should
treat edited text as a different claim, not the one it originally
checked.
"""

from __future__ import annotations

import hashlib
import re


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace, so formatting differences
    between extraction runs don't change a bullet's ID.

    Args:
        text: The raw bullet text.

    Returns:
        The normalized text.
    """
    return re.sub(r"\s+", " ", text.strip().lower())


def compute_bullet_id(experience_index: int, text: str) -> str:
    """Compute a stable ID for one bullet.

    Args:
        experience_index: The zero-based index of this bullet's
            experience entry within `CVTruthBase.experience`.
        text: The bullet's raw text.

    Returns:
        A 16-character hex digest, stable across re-extraction runs as
        long as `experience_index` and the bullet's (normalized) text
        are unchanged.
    """
    normalized = _normalize(text)
    digest = hashlib.sha256(f"{experience_index}:{normalized}".encode("utf-8"))
    return digest.hexdigest()[:16]
```

- [ ] **Step 4: Implement `schema.py`**

```python
"""The CV truth-base schema (PLAN.md Step 13) — the canonical,
structured representation of a user's CV that every later step (skill
normalisation, scoring, tailored-CV generation, the fabrication critic)
reads from instead of re-parsing a PDF.
"""

from __future__ import annotations

from pydantic import BaseModel


class Bullet(BaseModel):
    """One CV bullet point, with a stable ID.

    Attributes:
        bullet_id: Computed by `core.cv.bullet_id.compute_bullet_id` —
            never trusted from an LLM's output.
        text: The bullet's text, verbatim.
    """

    bullet_id: str
    text: str


class Experience(BaseModel):
    """One work-experience entry.

    Attributes:
        company: Employer name.
        title: Job title held.
        start: Start date, "YYYY-MM".
        end: End date, "YYYY-MM", or None for "present".
        bullets: This role's bullet points.
        tech: Technologies mentioned for this role.
        metrics: Quantified achievements mentioned for this role.
    """

    company: str
    title: str
    start: str
    end: str | None = None
    bullets: list[Bullet] = []
    tech: list[str] = []
    metrics: list[str] = []


class Skill(BaseModel):
    """One skill entry.

    Attributes:
        name: The skill's name, as it appears on the CV.
        canonical_id: The ESCO ID this skill maps to — populated by
            Step 14; always None until then.
        years: Years of experience with this skill, if statable.
        last_used: "YYYY-MM" this skill was last used, if statable.
        evidence_refs: `bullet_id`s this skill is evidenced by.
    """

    name: str
    canonical_id: str | None = None
    years: float | None = None
    last_used: str | None = None
    evidence_refs: list[str] = []


class Education(BaseModel):
    """One education entry.

    Attributes:
        institution: School/university name.
        qualification: Degree or qualification name.
        start: Start date, "YYYY" or "YYYY-MM", if statable.
        end: End date, "YYYY" or "YYYY-MM", if statable.
    """

    institution: str
    qualification: str
    start: str | None = None
    end: str | None = None


class Certification(BaseModel):
    """One professional certification.

    Attributes:
        name: Certification name.
        year: Year obtained, if statable.
    """

    name: str
    year: int | None = None


class Publication(BaseModel):
    """One publication.

    Attributes:
        citation: The publication's citation text, verbatim.
    """

    citation: str


class CVTruthBase(BaseModel):
    """The full CV truth base — one user's canonical CV representation.

    Attributes:
        identity: Full name.
        headline: Professional headline, e.g. "Senior Data Engineer".
        locations: Locations associated with this CV.
        work_auth: Work authorization statement, if present.
        skills: Every skill entry.
        experience: Every work-experience entry, in CV order.
        education: Every education entry.
        certifications: Every certification.
        publications: Every publication.
    """

    identity: str
    headline: str
    locations: list[str] = []
    work_auth: str | None = None
    skills: list[Skill] = []
    experience: list[Experience] = []
    education: list[Education] = []
    certifications: list[Certification] = []
    publications: list[Publication] = []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.test_bullet_id tests.test_cv_schema -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/cv/__init__.py packages/core/core/cv/schema.py \
    packages/core/core/cv/bullet_id.py packages/core/tests/test_bullet_id.py \
    packages/core/tests/test_cv_schema.py
git commit -m "feat(job_search): add CV truth-base schema and stable bullet IDs (JOB-202)"
```

---

### Task 3: DB migration for `cv_truth_base` / `cv_truth_base_history`

**Files:**
- Create: `db/migrations/versions/0017_create_cv_truth_base.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: tables `cv_truth_base` (current, `user_id UNIQUE`) and
  `cv_truth_base_history` (append-only, `UNIQUE (user_id, version)`),
  both RLS-enforced. Task 4's store module reads/writes these directly
  by name.

- [ ] **Step 1: Write the migration**

```python
"""create cv_truth_base and cv_truth_base_history

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-13

cv_truth_base (PLAN.md Step 13, JOB-202) holds one user's current CV
truth base. `user_id` carries a UNIQUE constraint — the DB-enforced
"exactly one base CV per user" PLAN.md asks for, so no application code
path (including a future import script) can create a second live row.

cv_truth_base_history is append-only: every version that has ever been
current — including the current one, mirrored here at write time — so
"which version was this artefact generated from" stays answerable, and
a correction-pass edit is traceable the same way a fresh extraction is
(see core.cv.store's docstring for the write path both share).

Both are per-user tables, so both get RLS enabled with a policy on the
`app.current_user_id` GUC — the exact two-statement pattern used by
app_user (0001) and user_quota (0002).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_truth_base",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("extracted_markdown", sa.Text(), nullable=False),
        sa.Column("truth_base", JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute("ALTER TABLE cv_truth_base ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY cv_truth_base_isolation ON cv_truth_base
        USING (user_id = current_setting('app.current_user_id', true)::uuid)
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON cv_truth_base TO job_search_app"
    )

    op.create_table(
        "cv_truth_base_history",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("extracted_markdown", sa.Text(), nullable=False),
        sa.Column("truth_base", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "version", name="uq_cv_truth_base_history_version"),
    )
    op.execute("ALTER TABLE cv_truth_base_history ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY cv_truth_base_history_isolation ON cv_truth_base_history
        USING (user_id = current_setting('app.current_user_id', true)::uuid)
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON cv_truth_base_history TO job_search_app"
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON cv_truth_base_history FROM job_search_app")
    op.drop_table("cv_truth_base_history")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON cv_truth_base FROM job_search_app")
    op.drop_table("cv_truth_base")
```

- [ ] **Step 2: Apply and verify**

Run: `docker compose up -d postgres` (if not already up), then from
`db/`: `alembic upgrade head`.
Expected: migration `0017` applies with no error; `\d cv_truth_base` and
`\d cv_truth_base_history` in `psql` show both tables with RLS enabled
(`Policies:` section populated).

- [ ] **Step 3: Commit**

```bash
git add db/migrations/versions/0017_create_cv_truth_base.py
git commit -m "feat(job_search): add cv_truth_base and cv_truth_base_history tables (JOB-202)"
```

---

### Task 4: `core.cv.store` — the history-then-replace write path

**Files:**
- Create: `packages/core/core/cv/store.py`
- Test: `packages/core/tests/integration/test_cv_store.py`

**Interfaces:**
- Consumes: `core.cv.schema.CVTruthBase` (Task 2); `cv_truth_base` /
  `cv_truth_base_history` tables (Task 3); `core.db.session.session_scope`.
- Produces: `core.cv.store.{StoredTruthBase, read_truth_base,
  write_truth_base}`. Task 8 (API router) and the eval predictor (if it
  ever needs to persist) call these — never raw SQL of their own against
  these tables.

- [ ] **Step 1: Write the failing test**

```python
"""Integration tests for core.cv.store against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.cv.schema import Bullet, CVTruthBase, Experience
from core.cv.store import read_truth_base, write_truth_base
from core.db.session import build_engine, session_scope
from core.settings import get_settings


def _live_migration_engine():
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any connection failure means "skip"
        raise unittest.SkipTest(
            f"Postgres not reachable ({exc}); run `docker compose up -d postgres` first."
        ) from None
    return engine


def _sample_truth_base(identity: str) -> CVTruthBase:
    return CVTruthBase(
        identity=identity,
        headline="Senior Test Engineer",
        experience=[
            Experience(
                company="Fixture Corp",
                title="Test Engineer",
                start="2020-01",
                end=None,
                bullets=[Bullet(bullet_id="fixture01", text="Wrote fixtures.")],
            )
        ],
    )


class TestCvStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration_engine = _live_migration_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        with session_scope(self.migration_engine) as conn:
            self.user_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'Test User')"
                ),
                {"id": self.user_id, "email": f"{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_first_write_creates_version_1_and_no_prior_history(self) -> None:
        truth_base = _sample_truth_base("Jane Doe")
        version = write_truth_base(
            self.app_engine, self.user_id, "# Jane Doe CV", truth_base
        )
        self.assertEqual(version, 1)
        stored = read_truth_base(self.app_engine, self.user_id)
        assert stored is not None
        self.assertEqual(stored.version, 1)
        self.assertEqual(stored.truth_base, truth_base)

    def test_second_write_replaces_current_and_preserves_history(self) -> None:
        first = _sample_truth_base("Jane Doe")
        second = _sample_truth_base("Jane A. Doe")
        write_truth_base(self.app_engine, self.user_id, "# v1", first)
        version = write_truth_base(self.app_engine, self.user_id, "# v2", second)

        self.assertEqual(version, 2)
        current = read_truth_base(self.app_engine, self.user_id)
        assert current is not None
        self.assertEqual(current.version, 2)
        self.assertEqual(current.truth_base.identity, "Jane A. Doe")

        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            history_versions = sorted(
                row.version
                for row in conn.execute(
                    text("SELECT version FROM cv_truth_base_history")
                ).all()
            )
        self.assertEqual(history_versions, [1, 2])

    def test_read_returns_none_when_no_truth_base_exists(self) -> None:
        self.assertIsNone(read_truth_base(self.app_engine, self.user_id))

    def test_rls_isolates_users(self) -> None:
        write_truth_base(
            self.app_engine, self.user_id, "# mine", _sample_truth_base("Jane Doe")
        )
        other_user_id = uuid.uuid4()
        self.assertIsNone(read_truth_base(self.app_engine, other_user_id))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.integration.test_cv_store -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'core.cv.store'`)

- [ ] **Step 3: Implement `store.py`**

```python
"""The CV truth base's history-then-replace write path (PLAN.md Step 13).

A write is always one transaction: insert the new content into
`cv_truth_base_history` at `version = current + 1` (or `1` for a first
write), then make `cv_truth_base` mirror it — never a second live row
in `cv_truth_base`, per the DB's own `user_id UNIQUE` constraint (0017).
Both a fresh extraction and a correction-pass save go through this same
path, so both produce a new version and both are traceable in history.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text

from core.cv.schema import CVTruthBase
from core.db.session import session_scope


@dataclass(frozen=True)
class StoredTruthBase:
    """One user's current `cv_truth_base` row, deserialized.

    Attributes:
        version: The current version number.
        extracted_markdown: The markdown this version was parsed from.
        truth_base: The parsed `CVTruthBase`.
        updated_at: When this version was written.
    """

    version: int
    extracted_markdown: str
    truth_base: CVTruthBase
    updated_at: datetime


def read_truth_base(engine: Engine, user_id: uuid.UUID) -> StoredTruthBase | None:
    """Read a user's current CV truth base.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: The user to read.

    Returns:
        The `StoredTruthBase`, or None if this user has no CV yet.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                "SELECT version, extracted_markdown, truth_base, updated_at "
                "FROM cv_truth_base"
            )
        ).one_or_none()
    if row is None:
        return None
    return StoredTruthBase(
        version=row.version,
        extracted_markdown=row.extracted_markdown,
        truth_base=CVTruthBase.model_validate(row.truth_base),
        updated_at=row.updated_at,
    )


def write_truth_base(
    engine: Engine,
    user_id: uuid.UUID,
    extracted_markdown: str,
    truth_base: CVTruthBase,
) -> int:
    """Write a new version of a user's CV truth base.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: The user this CV belongs to.
        extracted_markdown: The markdown this version was parsed from
            (or the prior version's markdown, unchanged, for a
            correction-pass save that only edits structured fields).
        truth_base: The truth base to store as the new current version.

    Returns:
        The new version number (1 for a user's first CV).
    """
    truth_base_json = truth_base.model_dump_json()
    with session_scope(engine, user_id=user_id) as conn:
        current = conn.execute(
            text("SELECT version FROM cv_truth_base")
        ).one_or_none()
        new_version = (current.version + 1) if current else 1

        conn.execute(
            text(
                "INSERT INTO cv_truth_base_history "
                "(user_id, version, extracted_markdown, truth_base) "
                "VALUES (:user_id, :version, :markdown, CAST(:truth_base AS jsonb))"
            ),
            {
                "user_id": user_id,
                "version": new_version,
                "markdown": extracted_markdown,
                "truth_base": truth_base_json,
            },
        )

        if current:
            conn.execute(
                text(
                    "UPDATE cv_truth_base SET "
                    "version = :version, "
                    "extracted_markdown = :markdown, "
                    "truth_base = CAST(:truth_base AS jsonb), "
                    "updated_at = now()"
                ),
                {
                    "version": new_version,
                    "markdown": extracted_markdown,
                    "truth_base": truth_base_json,
                },
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO cv_truth_base "
                    "(user_id, version, extracted_markdown, truth_base) "
                    "VALUES (:user_id, :version, :markdown, CAST(:truth_base AS jsonb))"
                ),
                {
                    "user_id": user_id,
                    "version": new_version,
                    "markdown": extracted_markdown,
                    "truth_base": truth_base_json,
                },
            )
    return new_version
```

Note: `truth_base` read back from Postgres via psycopg3 already
deserializes JSONB into a Python `dict`/`list` — `row.truth_base` is
passed straight to `CVTruthBase.model_validate` above, no `json.loads`
needed. If the implementer observes psycopg3 returning a raw string
instead (driver-version-dependent), add `json.loads(row.truth_base)`
before `model_validate` and note it in the task's report — this is the
one persistence-layer detail not exercised anywhere else in this repo
yet, so verify it against the actual test run rather than assuming.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/core && python -m unittest tests.integration.test_cv_store -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/cv/store.py packages/core/tests/integration/test_cv_store.py
git commit -m "feat(job_search): add CV truth-base versioned storage (JOB-202)"
```

---

### Task 5: `cv_extraction` prompt and task-config entry

**Files:**
- Create: `prompts/cv_extraction/local.v1.md`
- Modify: `config/llm_tasks.yml`

**Interfaces:**
- Produces: the prompt registry entry and task-config entry Task 6's
  `extract_truth_base` and Task 7's eval predictor both load by name
  (`"cv_extraction"`, family `"local"`).

- [ ] **Step 1: Write the prompt file**

`prompts/cv_extraction/local.v1.md`:

```
Extract structured CV data from the markdown below into JSON matching exactly this shape:

{{
  "identity": "<full name>",
  "headline": "<professional headline, e.g. current or most recent job title>",
  "locations": ["<city, country>", ...],
  "work_auth": "<work authorization statement, or null if not stated>",
  "skills": [{{"name": "<skill name>", "years": <number or null>, "last_used": "<YYYY-MM or null>"}}, ...],
  "experience": [
    {{
      "company": "<employer name>",
      "title": "<job title>",
      "start": "<YYYY-MM>",
      "end": "<YYYY-MM or null if current>",
      "bullets": ["<bullet text, verbatim>", ...],
      "tech": ["<technology mentioned>", ...],
      "metrics": ["<quantified achievement mentioned>", ...]
    }},
    ...
  ],
  "education": [{{"institution": "<name>", "qualification": "<degree/qualification>", "start": "<YYYY or YYYY-MM or null>", "end": "<YYYY or YYYY-MM or null>"}}, ...],
  "certifications": [{{"name": "<certification name>", "year": <YYYY or null>}}, ...],
  "publications": [{{"citation": "<full citation text>"}}, ...]
}}

Rules:
- Every bullet's text must be copied verbatim from the source — do not paraphrase, summarize, or invent bullets.
- If a section is absent from the CV, return an empty list for it.
- Respond with ONLY the JSON object, no other text.

CV markdown:
{markdown}
```

- [ ] **Step 2: Add the task-config entry**

In `config/llm_tasks.yml`, add under `tasks:` (after `job_categorisation`,
before `eval_judge`, matching existing entry order):

```yaml
  cv_extraction:
    provider: ollama
    model: llama3.1:8b
    prompt_family: local
    eval_metric: field_f1
    eval_regression_threshold: 0.05
```

- [ ] **Step 3: Verify config loads**

Run (from `packages/core`):
```bash
python -c "from core.llm.task_config import load_task_config; print(load_task_config('cv_extraction'))"
```
Expected: prints a `TaskConfig(task='cv_extraction', provider='ollama', model='llama3.1:8b', prompt_family='local', eval_metric='field_f1', eval_regression_threshold=0.05, ...)` — no `TaskConfigError`.

- [ ] **Step 4: Commit**

```bash
git add prompts/cv_extraction/local.v1.md config/llm_tasks.yml
git commit -m "feat(job_search): add cv_extraction prompt and task-config entry (JOB-202)"
```

---

### Task 6: CV extraction pipeline (`core.cv.extract`)

**Files:**
- Create: `packages/core/core/cv/extract.py`
- Test: `packages/core/tests/test_cv_extract.py`

**Interfaces:**
- Consumes: `core.cv.schema.{Bullet, CVTruthBase, Experience, ...}`
  (Task 2); `core.cv.bullet_id.compute_bullet_id` (Task 2); the
  `cv_extraction` task-config/prompt entries (Task 5); `core.llm.gateway.complete`.
- Produces: `core.cv.extract.{docling_to_markdown, extract_truth_base}`.
  Task 7's eval predictor and Task 8's API router both call these.

**Docling's API is already confirmed**, not merely sketched: the
`docling.datamodel.base_models.DocumentStream` import and
`DocumentConverter().convert(stream).document.export_to_markdown()`
call below were smoke-tested end-to-end (synthetic HTML in, correct
markdown out) against the real installed `docling==2.126.0` package
before this task was written. Implement `docling_to_markdown` exactly
as shown.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for core.cv.extract — the markdown->schema LLM step, plus a
Docling round-trip test that uses a synthetic HTML input (not a PDF) so
no fixture here is a CV-shaped document that could be confused with, or
need updating in lockstep with, real personal data.

Unit-level, fake adapter only — mirrors test_llm_classifier.py's own
`_FakeAdapter` pattern rather than mocking, since `core.llm.types.LLMAdapter`
is already a seam this codebase's tests implement directly.
"""

from __future__ import annotations

import json
import unittest

from core.cv.extract import docling_to_markdown, extract_truth_base
from core.llm.types import LLMResponse


class _FakeAdapter:
    """A fake `LLMAdapter` that returns a fixed response.

    Attributes:
        calls: `(model, prompt)` pairs passed to every `complete` call.
    """

    def __init__(self, response_text: str) -> None:
        """Initialise the fake adapter.

        Args:
            response_text: The text every `complete` call will return.
        """
        self._response_text = response_text
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Record the call and return the fixed fake response.

        Args:
            model: The provider-specific model identifier.
            prompt: The prompt text.
            temperature: Sampling temperature (unused by the fake).
            seed: A fixed seed (unused by the fake).

        Returns:
            The fixed `LLMResponse` configured at construction time.
        """
        self.calls.append((model, prompt))
        return LLMResponse(
            text=self._response_text,
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestDoclingToMarkdown(unittest.TestCase):
    def test_converts_html_to_markdown(self) -> None:
        html = b"<html><body><h1>Test Document</h1><p>Hello world.</p></body></html>"
        markdown = docling_to_markdown(html, "test.html")
        self.assertIn("Test Document", markdown)
        self.assertIn("Hello world.", markdown)


class TestExtractTruthBase(unittest.TestCase):
    def test_parses_llm_response_and_assigns_stable_bullet_ids(self) -> None:
        payload = {
            "identity": "Jane Doe",
            "headline": "Senior Test Engineer",
            "locations": ["London, UK"],
            "work_auth": None,
            "skills": [{"name": "Python", "years": 5.0, "last_used": "2026-01"}],
            "experience": [
                {
                    "company": "Fixture Corp",
                    "title": "Test Engineer",
                    "start": "2020-01",
                    "end": None,
                    "bullets": ["Wrote extensive test fixtures."],
                    "tech": ["Python"],
                    "metrics": [],
                }
            ],
            "education": [],
            "certifications": [],
            "publications": [],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.identity, "Jane Doe")
        self.assertEqual(len(result.experience), 1)
        bullet = result.experience[0].bullets[0]
        self.assertEqual(bullet.text, "Wrote extensive test fixtures.")

        from core.cv.bullet_id import compute_bullet_id

        self.assertEqual(
            bullet.bullet_id,
            compute_bullet_id(0, "Wrote extensive test fixtures."),
        )

    def test_raises_value_error_on_unparseable_response(self) -> None:
        fake_adapter = _FakeAdapter("not json")
        with self.assertRaises(ValueError):
            extract_truth_base(
                "# Jane Doe CV",
                adapters={"ollama": fake_adapter},
                provider="ollama",
                model="llama3.1:8b",
                prompt_family="local",
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.test_cv_extract -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'core.cv.extract'`)

- [ ] **Step 3: Implement `extract.py`**

```python
"""CV extraction pipeline (PLAN.md Step 13): Docling converts a CV
document to markdown; one LLM call (task "cv_extraction", routed to
Ollama only — DECISIONS.md's task split never migrates this task) parses
that markdown into `core.cv.schema.CVTruthBase`. Bullet IDs are computed
here from the LLM's raw bullet text, never trusted from the LLM's own
output — see `core.cv.bullet_id.compute_bullet_id`.
"""

from __future__ import annotations

import io
import json

from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter
from pydantic import BaseModel

from core.cv.bullet_id import compute_bullet_id
from core.cv.schema import (
    Bullet,
    Certification,
    CVTruthBase,
    Education,
    Experience,
    Publication,
    Skill,
)
from core.llm.gateway import complete
from core.llm.prompts import load_prompt
from core.llm.types import LLMAdapter

_PROMPT_FAMILY = "local"
_PROMPT_VERSION_NUMBER = 1


class _RawExperience(BaseModel):
    """One experience entry as the LLM returns it — bullets are plain
    strings here; `extract_truth_base` attaches stable IDs afterward.
    """

    company: str
    title: str
    start: str
    end: str | None = None
    bullets: list[str] = []
    tech: list[str] = []
    metrics: list[str] = []


class _RawCVTruthBase(BaseModel):
    """The LLM's raw extraction response, before bullet IDs are attached."""

    identity: str
    headline: str
    locations: list[str] = []
    work_auth: str | None = None
    skills: list[Skill] = []
    experience: list[_RawExperience] = []
    education: list[Education] = []
    certifications: list[Certification] = []
    publications: list[Publication] = []


def docling_to_markdown(file_bytes: bytes, filename: str) -> str:
    """Convert a CV document to markdown via Docling.

    Args:
        file_bytes: The raw document bytes. Production traffic is PDF;
            this module's own tests use HTML instead, since Docling
            also handles it and it avoids needing a synthetic PDF
            fixture.
        filename: The original filename — its extension tells Docling
            which format to parse.

    Returns:
        The document's content as markdown text.
    """
    stream = DocumentStream(name=filename, stream=io.BytesIO(file_bytes))
    result = DocumentConverter().convert(stream)
    return result.document.export_to_markdown()


def extract_truth_base(
    markdown: str,
    *,
    adapters: dict[str, LLMAdapter],
    provider: str | None = None,
    model: str | None = None,
    prompt_family: str | None = None,
) -> CVTruthBase:
    """Parse a CV's markdown into a `CVTruthBase` via one LLM call.

    Args:
        markdown: The CV's markdown text (from `docling_to_markdown`,
            or a synthetic snippet in eval/test cases).
        adapters: Every available LLM adapter, keyed by provider.
        provider: Overrides the production-configured provider — used
            by the eval harness to force a specific provider. `None`
            (the default) uses `cv_extraction`'s `config/llm_tasks.yml`
            entry, which is always Ollama (see DECISIONS.md's task
            split — this task never migrates to Anthropic).
        model: The model to use with `provider`. Must be given together
            with `provider`.
        prompt_family: Which prompt file to load — defaults to this
            module's own `_PROMPT_FAMILY` ("local") when `provider` is
            given without an explicit `prompt_family`.

    Returns:
        The parsed `CVTruthBase`, with every bullet's `bullet_id`
        computed deterministically from its own text and position.

    Raises:
        ValueError: If the LLM's response can't be parsed as the
            expected JSON shape.
    """
    resolved_family = prompt_family or _PROMPT_FAMILY
    prompt_template = load_prompt(
        "cv_extraction", resolved_family, _PROMPT_VERSION_NUMBER
    )
    prompt = prompt_template.format(markdown=markdown)
    prompt_version = f"{resolved_family}.v{_PROMPT_VERSION_NUMBER}"
    response = complete(
        task="cv_extraction",
        prompt=prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        provider=provider,
        model=model,
    )
    try:
        parsed = json.loads(response.text.strip())
        raw = _RawCVTruthBase.model_validate(parsed)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"could not parse cv_extraction response: {exc}") from exc

    experience = [
        Experience(
            company=exp.company,
            title=exp.title,
            start=exp.start,
            end=exp.end,
            bullets=[
                Bullet(bullet_id=compute_bullet_id(index, text), text=text)
                for text in exp.bullets
            ],
            tech=exp.tech,
            metrics=exp.metrics,
        )
        for index, exp in enumerate(raw.experience)
    ]
    return CVTruthBase(
        identity=raw.identity,
        headline=raw.headline,
        locations=raw.locations,
        work_auth=raw.work_auth,
        skills=raw.skills,
        experience=experience,
        education=raw.education,
        certifications=raw.certifications,
        publications=raw.publications,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/core && python -m unittest tests.test_cv_extract -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/cv/extract.py packages/core/tests/test_cv_extract.py
git commit -m "feat(job_search): add Docling+LLM CV extraction pipeline (JOB-202)"
```

---

### Task 7: `cv_extraction` golden set and eval-runner predictor

**Files:**
- Create: `evals/golden/cv_extraction.yml`
- Modify: `packages/core/core/evals/runner.py`
- Test: `packages/core/tests/test_evals_runner_cv_extraction.py`

**Interfaces:**
- Consumes: `core.cv.extract.extract_truth_base` (Task 6);
  `core.evals.golden.GoldenCase`/`load_golden_set` (existing);
  `core.evals.runner._PREDICTORS` (existing registry, extended here).
- Produces: a working `run_eval("cv_extraction", ...)` call path.

- [ ] **Step 1: Write the golden set (>= 20 cases, all synthetic)**

`evals/golden/cv_extraction.yml` — each case's `input` is a short
synthetic CV-snippet markdown string; `expected` is a flat dict of the
fields `field_f1` checks. Write 20 cases varying company/title/dates/one
bullet/one skill, e.g.:

```yaml
cases:
  - case_id: cv_001
    input:
      markdown: |
        # Alex Chen
        ## Senior Backend Engineer

        ### Nimbus Systems — Backend Engineer
        Jan 2019 - Dec 2022

        - Rebuilt the checkout service, cutting p99 latency by 40%.

        **Skills:** Go, Kubernetes, PostgreSQL
    expected:
      company: "Nimbus Systems"
      title: "Backend Engineer"
      start: "2019-01"
      end: "2022-12"
      first_bullet: "Rebuilt the checkout service, cutting p99 latency by 40%."
      first_skill: "Go"
  - case_id: cv_002
    input:
      markdown: |
        # Priya Nair
        ## Data Scientist

        ### Alto Analytics — Data Scientist
        Mar 2021 - Present

        - Shipped a churn-prediction model adopted by 3 product teams.

        **Skills:** Python, scikit-learn, SQL
    expected:
      company: "Alto Analytics"
      title: "Data Scientist"
      start: "2021-03"
      end: null
      first_bullet: "Shipped a churn-prediction model adopted by 3 product teams."
      first_skill: "Python"
  # ... 18 more cases, same shape, varying company/title/dates/bullet/skill.
  # The implementer must write all 20+ cases before this task is done —
  # copy the two-case pattern above with fresh, invented names/roles/bullets.
```

**This task is not done until the file has at least 20 cases** — copy
the pattern above with fresh invented content per case (vary industries,
seniority levels, date ranges, and bullet phrasing so the set isn't 20
near-duplicates). No real person's CV content in any case.

- [ ] **Step 2: Write the failing test**

```python
"""cv_extraction's golden set loads and the predictor round-trips it.

Unit-level, fake adapter only — mirrors test_llm_classifier.py's
`_FakeAdapter` pattern rather than mocking.
"""

from __future__ import annotations

import json
import unittest

from core.evals.golden import load_golden_set
from core.evals.runner import _PREDICTORS
from core.llm.types import LLMResponse


class _FakeAdapter:
    """A fake `LLMAdapter` that returns a fixed response.

    Attributes:
        calls: `(model, prompt)` pairs passed to every `complete` call.
    """

    def __init__(self, response_text: str) -> None:
        """Initialise the fake adapter.

        Args:
            response_text: The text every `complete` call will return.
        """
        self._response_text = response_text
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Record the call and return the fixed fake response.

        Args:
            model: The provider-specific model identifier.
            prompt: The prompt text.
            temperature: Sampling temperature (unused by the fake).
            seed: A fixed seed (unused by the fake).

        Returns:
            The fixed `LLMResponse` configured at construction time.
        """
        self.calls.append((model, prompt))
        return LLMResponse(
            text=self._response_text,
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestCvExtractionGoldenSet(unittest.TestCase):
    def test_golden_set_has_at_least_the_minimum_case_count(self) -> None:
        from core.evals.runner import MINIMUM_GOLDEN_SET_SIZE

        cases = load_golden_set("cv_extraction")
        self.assertGreaterEqual(len(cases), MINIMUM_GOLDEN_SET_SIZE)

    def test_cv_extraction_predictor_is_registered(self) -> None:
        self.assertIn("cv_extraction", _PREDICTORS)

    def test_predictor_flattens_extraction_result_to_expected_keys(self) -> None:
        cases = load_golden_set("cv_extraction")
        case = cases[0]
        payload = {
            "identity": "Test Person",
            "headline": "Engineer",
            "locations": [],
            "work_auth": None,
            "skills": [{"name": case.expected["first_skill"]}],
            "experience": [
                {
                    "company": case.expected["company"],
                    "title": case.expected["title"],
                    "start": case.expected["start"],
                    "end": case.expected["end"],
                    "bullets": [case.expected["first_bullet"]],
                    "tech": [],
                    "metrics": [],
                }
            ],
            "education": [],
            "certifications": [],
            "publications": [],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        predictor = _PREDICTORS["cv_extraction"]
        predicted, prompt_version = predictor(
            case,
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
            adapters={"ollama": fake_adapter},
        )
        self.assertEqual(predicted, case.expected)
        self.assertIsNotNone(prompt_version)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.test_evals_runner_cv_extraction -v`
Expected: FAIL (`KeyError: 'cv_extraction'` — not yet in `_PREDICTORS`)

- [ ] **Step 4: Add the predictor to `runner.py`**

In `packages/core/core/evals/runner.py`, add a new predictor function
(placed after `_predict_job_categorisation`, mirroring its shape) and
register it in `_PREDICTORS`:

```python
def _predict_cv_extraction(
    case: GoldenCase,
    *,
    provider: str,
    model: str,
    prompt_family: str,
    adapters: dict[str, LLMAdapter],
) -> tuple[dict[str, object], str | None]:
    """Predict a `cv_extraction` case's flattened fields via the LLM.

    Args:
        case: The golden case to predict — `case.input["markdown"]` is
            the CV markdown snippet to extract from.
        provider: The provider to force `extract_truth_base` to use.
        model: The model to use with `provider`.
        prompt_family: The prompt variant to load for `provider`.
        adapters: Every available LLM adapter, keyed by provider name.

    Returns:
        A tuple of (flattened predicted fields matching this task's
        golden-set expected-key shape, `for field_f1` to compare) and
        the `prompt_version` `extract_truth_base` used. Falls back to
        `({}, None)` if extraction raises `ValueError` — one malformed
        response should not crash a whole eval run.
    """
    from core.cv.extract import extract_truth_base

    try:
        result = extract_truth_base(
            case.input["markdown"],
            adapters=adapters,
            provider=provider,
            model=model,
            prompt_family=prompt_family,
        )
    except ValueError:
        return {}, None

    first_experience = result.experience[0] if result.experience else None
    first_skill = result.skills[0] if result.skills else None
    predicted = {
        "company": first_experience.company if first_experience else None,
        "title": first_experience.title if first_experience else None,
        "start": first_experience.start if first_experience else None,
        "end": first_experience.end if first_experience else None,
        "first_bullet": (
            first_experience.bullets[0].text
            if first_experience and first_experience.bullets
            else None
        ),
        "first_skill": first_skill.name if first_skill else None,
    }
    prompt_version = f"{prompt_family}.v1"
    return predicted, prompt_version
```

Add `"cv_extraction": _predict_cv_extraction,` to the `_PREDICTORS` dict
literal, alongside the existing `"job_categorisation"` entry.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/core && python -m unittest tests.test_evals_runner_cv_extraction -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add evals/golden/cv_extraction.yml packages/core/core/evals/runner.py \
    packages/core/tests/test_evals_runner_cv_extraction.py
git commit -m "feat(job_search): add cv_extraction golden set and eval predictor (JOB-202)"
```

---

### Task 8: API router — `GET/PUT /cv/truth-base`, `POST /cv/extract`

**Files:**
- Create: `apps/api/app/routers/cv.py`
- Modify: `apps/api/app/main.py`
- Test: `packages/core/tests/integration/test_cv_router.py`

**Interfaces:**
- Consumes: `core.cv.store.{read_truth_base, write_truth_base}` (Task
  4); `core.cv.extract.{docling_to_markdown, extract_truth_base}` (Task
  6); `core.cv.schema.CVTruthBase` (Task 2); `core.db.session.get_current_user_id`;
  `app.dependencies.{get_app_db_engine, get_llm_adapters}`.
- Produces: the three endpoints Task 9's Streamlit page calls.

- [ ] **Step 1: Write the failing test**

This project's existing routers (e.g. `classification.py`) serve shared
data with no `get_current_user_id` dependency; `cv.py` is the first
per-user-tenancy router, so its tests must override that dependency the
way FastAPI's `TestClient` supports, rather than hitting the real
(still-501ing) auth path:

```python
"""Integration tests for the CV router, against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.dependencies import get_app_db_engine, get_llm_adapters
from app.main import app
from core.db.session import build_engine, get_current_user_id, session_scope
from core.settings import get_settings


def _live_migration_engine():
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(
            f"Postgres not reachable ({exc}); run `docker compose up -d postgres` first."
        ) from None
    return engine


class TestCvRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration_engine = _live_migration_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        with session_scope(self.migration_engine) as conn:
            self.user_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'Test User')"
                ),
                {"id": self.user_id, "email": f"{self.user_id}@example.com"},
            )
        app.dependency_overrides[get_current_user_id] = lambda: self.user_id
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_get_truth_base_returns_null_when_none_exists(self) -> None:
        response = self.client.get("/cv/truth-base")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json())

    def test_put_then_get_round_trips_the_truth_base(self) -> None:
        payload = {
            "extracted_markdown": "# Jane Doe",
            "truth_base": {
                "identity": "Jane Doe",
                "headline": "Engineer",
                "experience": [],
            },
        }
        put_response = self.client.put("/cv/truth-base", json=payload)
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(put_response.json()["version"], 1)

        get_response = self.client.get("/cv/truth-base")
        self.assertEqual(get_response.status_code, 200)
        body = get_response.json()
        self.assertEqual(body["truth_base"]["identity"], "Jane Doe")
        self.assertEqual(body["version"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.integration.test_cv_router -v`
Expected: FAIL (`ModuleNotFoundError` / 404 — router doesn't exist yet)

- [ ] **Step 3: Implement `cv.py`**

```python
"""GET/PUT /cv/truth-base, POST /cv/extract — the request-serving layer
for PLAN.md Step 13's CV truth base. The first per-user-tenancy router
in this API: every handler resolves `user_id` via `get_current_user_id`
(501s until Step 22a's auth lands, same seam every other per-user
endpoint will use) and reads/writes exclusively through
`core.cv.store`, never raw SQL of its own against `cv_truth_base`.
"""

from __future__ import annotations

import uuid

from app.dependencies import get_app_db_engine, get_llm_adapters
from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel
from sqlalchemy import Engine

from core.cv.extract import docling_to_markdown, extract_truth_base
from core.cv.schema import CVTruthBase
from core.cv.store import read_truth_base, write_truth_base
from core.db.session import get_current_user_id
from core.llm.types import LLMAdapter

router = APIRouter(prefix="/cv")


class TruthBaseResponse(BaseModel):
    """One user's current CV truth base, over the wire.

    Attributes:
        version: The current version number.
        extracted_markdown: The markdown this version was parsed from.
        truth_base: The structured truth base.
    """

    version: int
    extracted_markdown: str
    truth_base: CVTruthBase


class TruthBaseWriteRequest(BaseModel):
    """A correction-pass save, or any other direct truth-base replace.

    Attributes:
        extracted_markdown: The markdown to store alongside this version.
        truth_base: The truth base to store as the new current version.
    """

    extracted_markdown: str
    truth_base: CVTruthBase


class WriteResult(BaseModel):
    """The outcome of a truth-base write.

    Attributes:
        version: The new version number.
    """

    version: int


@router.get("/truth-base", response_model=TruthBaseResponse | None)
def get_truth_base(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> TruthBaseResponse | None:
    """Return the caller's current CV truth base.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The `TruthBaseResponse`, or None if this user has no CV yet.
    """
    stored = read_truth_base(engine, user_id)
    if stored is None:
        return None
    return TruthBaseResponse(
        version=stored.version,
        extracted_markdown=stored.extracted_markdown,
        truth_base=stored.truth_base,
    )


@router.put("/truth-base", response_model=WriteResult)
def put_truth_base(
    request: TruthBaseWriteRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> WriteResult:
    """Replace the caller's CV truth base with a new version.

    Used by both the correction UI's save action and any direct client
    that already has a `CVTruthBase` to store.

    Args:
        request: The new markdown/truth-base pair to store.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The new version number.
    """
    version = write_truth_base(
        engine, user_id, request.extracted_markdown, request.truth_base
    )
    return WriteResult(version=version)


@router.post("/extract", response_model=WriteResult)
async def post_extract(
    file: UploadFile,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> WriteResult:
    """Extract a CV PDF and store the result as a new version.

    Args:
        file: The uploaded CV document.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The new version number.
    """
    file_bytes = await file.read()
    markdown = docling_to_markdown(file_bytes, file.filename or "cv.pdf")
    truth_base = extract_truth_base(markdown, adapters=adapters)
    version = write_truth_base(engine, user_id, markdown, truth_base)
    return WriteResult(version=version)
```

- [ ] **Step 4: Register the router in `main.py`**

In `apps/api/app/main.py`, add `cv` to the import and registration:

```python
from app.routers import classification, cv, dedup, ingest
```

```python
app.include_router(cv.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/core && python -m unittest tests.integration.test_cv_router -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/cv.py apps/api/app/main.py \
    packages/core/tests/integration/test_cv_router.py
git commit -m "feat(job_search): add CV truth-base API router (JOB-202)"
```

---

### Task 9: Streamlit correction-pass UI

**Files:**
- Create: `apps/ui/app/pages/5_CV_Correction.py`

**Interfaces:**
- Consumes: `GET/PUT /cv/truth-base`, `POST /cv/extract` (Task 8), over
  HTTP via `httpx` — same pattern as `4_Categorisation_Review.py`.

- [ ] **Step 1: Implement the page**

```python
"""CV correction pass (PLAN.md Step 13) — upload a CV to extract a
truth base, or edit and re-save an existing one. Uses `st.data_editor`
for the list-shaped sections (skills, experience bullets) rather than
one widget per nested field, so adding/removing a row is a native grid
action instead of bespoke per-field UI.
"""

from __future__ import annotations

import httpx
import pandas as pd
import streamlit as st

from core.cv.bullet_id import compute_bullet_id
from core.settings import get_settings

st.set_page_config(page_title="CV Correction", layout="wide")
st.title("CV Correction")

_settings = get_settings()


def _fetch_truth_base() -> dict | None:
    response = httpx.get(f"{_settings.api_base_url}/cv/truth-base", timeout=10.0)
    response.raise_for_status()
    return response.json()


try:
    current = _fetch_truth_base()
except httpx.HTTPError as exc:
    st.error(f"Failed to load CV truth base: {exc}")
    current = None

if current is None:
    st.info("No CV on file yet — upload one to extract a truth base.")
    uploaded = st.file_uploader("Upload CV (PDF)", type=["pdf"])
    if uploaded is not None and st.button("Extract"):
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/cv/extract",
                files={"file": (uploaded.name, uploaded.getvalue())},
                timeout=120.0,
            )
            response.raise_for_status()
            st.success(f"Extracted as version {response.json()['version']}.")
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Extraction failed: {exc}")
else:
    truth_base = current["truth_base"]
    st.caption(f"Version {current['version']}")

    identity = st.text_input("Identity", value=truth_base["identity"])
    headline = st.text_input("Headline", value=truth_base["headline"])

    st.subheader("Skills")
    skills_df = pd.DataFrame(
        [
            {
                "name": s["name"],
                "years": s["years"],
                "last_used": s["last_used"],
            }
            for s in truth_base["skills"]
        ]
    )
    edited_skills = st.data_editor(skills_df, num_rows="dynamic", key="skills_editor")

    st.subheader("Experience")
    experience_rows = []
    for exp_index, exp in enumerate(truth_base["experience"]):
        with st.expander(f"{exp['company']} — {exp['title']}", expanded=False):
            company = st.text_input("Company", value=exp["company"], key=f"company_{exp_index}")
            title = st.text_input("Title", value=exp["title"], key=f"title_{exp_index}")
            start = st.text_input("Start", value=exp["start"], key=f"start_{exp_index}")
            end = st.text_input("End", value=exp["end"] or "", key=f"end_{exp_index}")
            bullets_df = pd.DataFrame(
                [{"text": b["text"]} for b in exp["bullets"]]
            )
            edited_bullets = st.data_editor(
                bullets_df, num_rows="dynamic", key=f"bullets_{exp_index}"
            )
            experience_rows.append(
                {
                    "company": company,
                    "title": title,
                    "start": start,
                    "end": end or None,
                    "bullets": edited_bullets["text"].tolist(),
                    "tech": exp["tech"],
                    "metrics": exp["metrics"],
                }
            )

    if st.button("Save corrections"):
        new_truth_base = {
            "identity": identity,
            "headline": headline,
            "locations": truth_base["locations"],
            "work_auth": truth_base["work_auth"],
            "skills": [
                {
                    "name": row["name"],
                    "canonical_id": None,
                    "years": row["years"],
                    "last_used": row["last_used"],
                    "evidence_refs": [],
                }
                for row in edited_skills.to_dict("records")
            ],
            "experience": [
                {
                    **row,
                    # Recomputed, not carried over from the pre-edit
                    # bullets: compute_bullet_id is deterministic, so an
                    # unedited bullet gets back the exact ID it already
                    # had, and an edited or reordered one correctly gets
                    # a new one (see core.cv.bullet_id's docstring).
                    "bullets": [
                        {"bullet_id": compute_bullet_id(i, text), "text": text}
                        for i, text in enumerate(row["bullets"])
                    ],
                }
                for row in experience_rows
            ],
            "education": truth_base["education"],
            "certifications": truth_base["certifications"],
            "publications": truth_base["publications"],
        }
        try:
            response = httpx.put(
                f"{_settings.api_base_url}/cv/truth-base",
                json={
                    "extracted_markdown": current["extracted_markdown"],
                    "truth_base": new_truth_base,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            st.success(f"Saved as version {response.json()['version']}.")
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Save failed: {exc}")
```

- [ ] **Step 2: Manual smoke test**

With `docker compose up -d api ui postgres ollama` running and at least
one `app_user` row present (auth still 501s per Task 8's docstring, so
this page only works end-to-end once a test harness or Step 22a
provides a real `user_id` — until then, verify the page renders and its
"no CV yet" / upload path doesn't crash by hitting it directly; full
round-trip verification happens in Task 4/8's automated integration
tests, which already cover the read/write path this page calls).

- [ ] **Step 3: Commit**

```bash
git add apps/ui/app/pages/5_CV_Correction.py
git commit -m "feat(job_search): add CV correction-pass Streamlit page (JOB-202)"
```

---

### Task 10: Full test suite and lint pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
cd packages/core && coverage run -m unittest discover && coverage report -m
```
Expected: all tests pass (the two known pre-existing unrelated failures
from earlier steps, if still present, are not new regressions; every
new test from Tasks 1-9 passes).

- [ ] **Step 2: Run lint**

```bash
cd packages/core && ruff check . && isort --check . && black --check .
```
Fix any violations and re-run until clean.

- [ ] **Step 3: Commit any lint fixes**

```bash
git add -A
git commit -m "chore(job_search): lint fixes for CV truth-base work (JOB-202)"
```
(Skip this commit if there was nothing to fix.)

---

## Manual verification against the real CV (not a task — performed directly by the session controller)

Once Tasks 1-9 are merged to this branch, verify Docling's real-PDF
fidelity once, interactively:

1. `docker compose up -d postgres ollama api`, ensure `ollama pull
   llama3.1:8b` has been run.
2. Insert one `app_user` row directly (owner DSN) to get a real
   `user_id`, and temporarily patch `get_current_user_id` (or call
   `core.cv.extract`/`core.cv.store` directly in a Python shell) to
   drive the pipeline against `~/Documents/Cv_mine/Frederic_Marechal_2026_v3.pdf`
   without needing Step 22a's auth.
3. Confirm the Education section's two-column layout survives Docling's
   conversion in a usable form (this is the one layout risk flagged in
   the design spec).
4. Confirm bullet IDs are stable across a second extraction run on the
   same file.
5. Do **not** commit the file, its markdown, its extracted JSON, or any
   screenshot of its content anywhere in this repository.
