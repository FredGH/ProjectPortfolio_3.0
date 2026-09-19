# Step 14 — ESCO Skill Normalisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give CV skills and JD skills one shared ESCO vocabulary, with must-have/nice-to-have levels on JD skills and a review list for anything that does not map.

**Architecture:** A loader ingests the ESCO CSV release into an `esco` schema; a deterministic mapper (alias → ESCO label → pgvector nearest neighbour → review) resolves normalised skill strings into a shared `silver.skill_mapping` cache. A local-LLM extractor writes raw JD skills per dedup survivor; a dbt model joins them to mapped skills as `silver__bridge_job_skill`. Reviews are resolved through a FastAPI router and a Streamlit page.

**Tech Stack:** Python 3.11, SQLAlchemy 2 + psycopg 3, Alembic, Postgres 16 + pgvector, dbt (dbt_utils), FastAPI, Streamlit, Ollama (`llama3.1:8b`, `nomic-embed-text`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-19-step14-esco-skill-normalisation-design.md`

## Global Constraints

- Python 3.11; black line length 88; isort profile black; ruff `E,F,UP`; Google-style docstrings with `Args`/`Returns`/`Raises` on every function and class; type hints on all public signatures; `from __future__ import annotations`.
- Tests use `unittest` (run from `packages/core`); integration tests use the real Postgres, never a mocked DB; only external HTTP (Ollama) is faked.
- Everything new is the **shared zone**: no `user_id`, no RLS; each migration docstring says why.
- The `skill_extraction` task is local-only (`ollama`, `llama3.1:8b`, prompt family `local`) per DECISIONS.md §1 — never routed to Anthropic.
- Embedding dimension 768 (`nomic-embed-text`); **no ANN index** on `esco.skill_embedding` (Step 15 owns index type); `embedding_model` recorded per row.
- English ESCO only. The real ESCO dataset and any real CV/JD data are never committed; fixtures and golden cases are synthetic.
- Skill IDs: ESCO skills use the trailing UUID of the concept URI; custom skills use `custom:<slug>`.
- dbt SQL follows `.claude/rules/sql-style.md` (uppercase keywords, header comment with grain, CTE comments).
- Commit messages end with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

## Test environment

The integration tests connect to the **shared local dev Postgres** (`localhost:5432`) and Ollama (`localhost:11434`), both already running from the main stack. Do **not** run `docker compose` from a worktree (container-name collision).

Run everything from `job_search/` with the repo venv active and the `.env` variables exported (`set -a; source .env; set +a`). Unit and integration tests run from `packages/core`:

```bash
cd packages/core && python -m unittest tests.test_skills_normalise -v
```

Because the dev DB is shared, every integration test must (a) touch only rows it created — fixture IDs start with `fixture-`, fixture strings with `zzfixture`, fixture jobs with `fixture-job-` — and (b) clean them up in `tearDown` via the helpers in Task 2.

## Deviations from the spec (spec is updated in Tasks 4 and 7)

1. `normalise_skill` lives in `core/skills/normalise.py`; the mapper lives in `core/skills/mapper.py`.
2. `map_skill` takes a `Connection` (not an `Engine`) plus `embedding_model`.
3. Label ties prefer a skill whose *preferred* label matches, then the smallest `skill_id`.
4. `silver.skill_mapping` gains `candidate_skill_id`/`candidate_score` (best below-threshold neighbour, shown as a suggestion in review) and a fifth `review_status`, `rejected` (a human rejected an auto-match; excluded from re-mapping).
5. The tolerant LLM-JSON parser moves from `core/cv/extract.py` to `core/llm/json_response.py` so both extractors share it.

## File structure

```
job_search/
  config/skill_aliases.yml                          # seed aliases (Task 6)
  config/llm_tasks.yml                              # + skill_extraction (Task 8)
  prompts/skill_extraction/local.v1.md              # (Task 8)
  evals/golden/skill_extraction.yml                 # 20 synthetic cases (Task 14)
  db/migrations/versions/0022_create_esco_schema.py         # (Task 2)
  db/migrations/versions/0023_create_silver_skill_tables.py # (Task 4)
  dbt/models/silver/silver__skill.sql, silver__bridge_job_skill.sql  # (Task 11)
  docs/esco.md                                      # (Task 15)
  packages/core/core/skills/
    __init__.py
    normalise.py      # normalise_skill (pure)
    vector.py         # EMBEDDING_DIMENSION, to_pgvector
    esco_load.py      # CSV release -> esco.* tables
    esco_embed.py     # esco.skill_embedding cache
    aliases.py        # config/skill_aliases.yml -> silver.skill_alias
    mapper.py         # map_skill, map_strings, map_pending, remap_unresolved
    jd_extract.py     # LLM extraction + chunking + merging
    write_job_skills.py  # batch writer for silver.job_skill_*
    cv_map.py         # write canonical_id into the CV truth base
    review.py         # list/resolve/dismiss/reject (used by the API)
  packages/core/core/llm/json_response.py           # shared JSON parser (Task 8)
  apps/api/app/routers/skills.py                    # (Task 12)
  apps/ui/app/pages/6_Skill_Review.py               # (Task 13)
  apps/pipeline/app/cli.py                          # + 6 subcommands (Tasks 3,5,7,9,10)
  packages/core/tests/...                           # mirrors the above
```

---

### Task 1: `normalise_skill`

**Files:**
- Create: `packages/core/core/skills/__init__.py`
- Create: `packages/core/core/skills/normalise.py`
- Test: `packages/core/tests/test_skills_normalise.py`

**Interfaces:**
- Produces: `normalise_skill(raw: str) -> str` — the one canonical key form used by every later task (`label_norm`, `alias_norm`, `raw_norm`).

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_skills_normalise.py`:

```python
"""Unit tests for core.skills.normalise."""

from __future__ import annotations

import unittest

from core.skills.normalise import normalise_skill


class TestNormaliseSkill(unittest.TestCase):
    def test_lowercases_and_collapses_whitespace(self) -> None:
        self.assertEqual(
            normalise_skill("  Google   Cloud\tPlatform "), "google cloud platform"
        )

    def test_expands_ampersand(self) -> None:
        self.assertEqual(normalise_skill("Data & Analytics"), "data and analytics")

    def test_strips_surrounding_punctuation(self) -> None:
        self.assertEqual(normalise_skill("(Terraform),"), "terraform")
        self.assertEqual(normalise_skill("Python."), "python")
        self.assertEqual(normalise_skill("(python.)"), "python")

    def test_keeps_leading_dot_plus_and_hash(self) -> None:
        self.assertEqual(normalise_skill(".NET"), ".net")
        self.assertEqual(normalise_skill("C++"), "c++")
        self.assertEqual(normalise_skill("C#"), "c#")
        self.assertEqual(normalise_skill("Node.js"), "node.js")

    def test_applies_nfkc_so_fullwidth_letters_match(self) -> None:
        self.assertEqual(normalise_skill("ＡＷＳ"), "aws")

    def test_empty_and_punctuation_only_input_returns_empty_string(self) -> None:
        self.assertEqual(normalise_skill(""), "")
        self.assertEqual(normalise_skill(" - "), "")

    def test_is_idempotent(self) -> None:
        once = normalise_skill("  Machine  Learning (ML)! ")
        self.assertEqual(normalise_skill(once), once)

    def test_does_not_merge_distinct_skills(self) -> None:
        self.assertNotEqual(normalise_skill("Java"), normalise_skill("JavaScript"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.test_skills_normalise -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'core.skills'`

- [ ] **Step 3: Write minimal implementation**

Create `packages/core/core/skills/__init__.py` (empty file), then `packages/core/core/skills/normalise.py`:

```python
"""Canonical string form for skill names (PLAN.md Step 14).

Every skill string — a CV skill, a JD-extracted skill, an ESCO label, an
alias — is reduced to this form before any comparison, so "Google  Cloud
Platform" and "google cloud platform" are the same key. Deliberately no
stemming or stop-word removal: "Java" and "JavaScript" must stay
distinct, and "Google Cloud" vs "Google Cloud Platform" is handled by
alias entries, not fuzzy string rules.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")

# Punctuation stripped from both ends. "." is NOT here (a leading "." is
# part of ".NET"); a trailing "." is stripped separately below. "+" and
# "#" are never stripped ("C++", "C#").
_EDGE_CHARS = " ,;:!?\"'()[]{}<>-–—/\\|*•·"


def normalise_skill(raw: str) -> str:
    """Reduce a skill string to its canonical comparison key.

    Args:
        raw: The skill string as it appeared in a CV, JD, or ESCO label.

    Returns:
        The NFKC-normalised, lowercased string with "&" expanded to
        "and", whitespace collapsed, and surrounding punctuation
        removed. Empty if nothing meaningful remains.
    """
    text = unicodedata.normalize("NFKC", raw).lower().replace("&", " and ")
    text = _WHITESPACE_RE.sub(" ", text).strip(_EDGE_CHARS)
    return text.rstrip(".").strip(_EDGE_CHARS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/core && python -m unittest tests.test_skills_normalise -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd ../.. && black packages/core/core/skills packages/core/tests/test_skills_normalise.py && isort packages/core/core/skills packages/core/tests/test_skills_normalise.py && ruff check packages/core/core/skills packages/core/tests/test_skills_normalise.py
git add packages/core/core/skills packages/core/tests/test_skills_normalise.py
git commit -m "feat(job_search): add normalise_skill for the Step 14 skill vocabulary (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `esco` schema migration and shared integration-test helpers

**Files:**
- Create: `db/migrations/versions/0022_create_esco_schema.py`
- Create: `packages/core/core/skills/vector.py`
- Create: `packages/core/tests/integration/skills_fixtures.py`
- Test: `packages/core/tests/integration/test_esco_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Tables `esco.skill`, `esco.skill_label`, `esco.occupation`, `esco.occupation_skill`, `esco.skill_embedding` (column definitions in the migration below).
  - `core.skills.vector`: `EMBEDDING_DIMENSION: int = 768`; `to_pgvector(vector: Sequence[float]) -> str` (a pgvector literal like `"[0.1,0.2]"`, used as `CAST(:v AS vector)`).
  - `tests.integration.skills_fixtures`: `live_owner_engine() -> Engine`, `live_app_engine() -> Engine`, `sparse_vector(components: dict[int, float], dimension: int = 768) -> list[float]`, `axis_vector(index: int) -> list[float]`, `purge_fixtures(engine: Engine) -> None`, `FIXTURE_ESCO_DIR: Path`.

- [ ] **Step 1: Write the shared vector helper**

Create `packages/core/core/skills/vector.py`:

```python
"""pgvector helpers for the ESCO skill-embedding cache (Step 14)."""

from __future__ import annotations

from collections.abc import Sequence

EMBEDDING_DIMENSION = 768
"""Dimension of `nomic-embed-text`, the project's embedding model
(DECISIONS.md §2.8). Fixed in `esco.skill_embedding`'s column type; a
different model means re-running `embed-esco` after a migration."""


def to_pgvector(vector: Sequence[float]) -> str:
    """Render a vector as a pgvector text literal.

    Args:
        vector: The embedding values.

    Returns:
        A string like ``"[0.1,0.2]"``, for use as
        ``CAST(:param AS vector)`` — avoids needing a pgvector driver
        adapter.
    """
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"
```

- [ ] **Step 2: Write the shared test helpers**

Create `packages/core/tests/integration/skills_fixtures.py`:

```python
"""Shared helpers for the Step 14 integration tests.

The dev Postgres is shared with the running stack, so every test uses
fixture-prefixed IDs and strings and removes them with `purge_fixtures`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from sqlalchemy import Engine, text

from core.db.session import build_engine
from core.settings import get_settings

FIXTURE_ESCO_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "esco"


def live_owner_engine() -> Engine:
    """Build the owner-role engine, skipping the test if Postgres is down.

    Returns:
        An `Engine` on `Settings.database_url`.

    Raises:
        unittest.SkipTest: If Postgres is not reachable.
    """
    engine = build_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any connection failure means skip
        raise unittest.SkipTest(f"Postgres not reachable ({exc})") from None
    return engine


def live_app_engine() -> Engine:
    """Build the RLS-subject app-role engine.

    Returns:
        An `Engine` on `Settings.app_database_url`.
    """
    return build_engine(get_settings().app_database_url)


def sparse_vector(components: dict[int, float], dimension: int = 768) -> list[float]:
    """Build a vector that is zero except at the given indices.

    Args:
        components: Map of index to value.
        dimension: Total length of the vector.

    Returns:
        The vector. Real embeddings are dense, so a sparse vector on a
        high axis has near-zero cosine similarity with them — fixtures
        therefore win or lose nearest-neighbour searches predictably
        even when real ESCO rows are loaded.
    """
    vector = [0.0] * dimension
    for index, value in components.items():
        vector[index] = value
    return vector


def axis_vector(index: int) -> list[float]:
    """Build a unit vector along one axis.

    Args:
        index: The axis.

    Returns:
        A 768-dim unit vector.
    """
    return sparse_vector({index: 1.0})


def purge_fixtures(engine: Engine) -> None:
    """Delete every fixture row this suite may have created.

    Args:
        engine: The owner-role engine.
    """
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM silver.job_skill_extraction "
                          "WHERE job_group_id LIKE 'fixture-job-%'"))
        conn.execute(text("DELETE FROM silver.job_survivorship "
                          "WHERE job_group_id LIKE 'fixture-job-%'"))
        conn.execute(text("DELETE FROM silver.skill_mapping "
                          "WHERE raw_norm LIKE 'zzfixture%'"))
        conn.execute(text("DELETE FROM silver.skill_alias "
                          "WHERE alias_norm LIKE 'zzfixture%'"))
        conn.execute(text("DELETE FROM silver.custom_skill "
                          "WHERE skill_id LIKE 'custom:fixture-%'"))
        conn.execute(text("DELETE FROM esco.occupation "
                          "WHERE occupation_id LIKE 'fixture-%'"))
        conn.execute(text("DELETE FROM esco.skill WHERE skill_id LIKE 'fixture-%'"))
```

Note: `purge_fixtures` deletes from the `silver.*` skill tables created in Task 4, so it must only be *called* by tests from Task 4 onward. The Task 2 and Task 3 tests clean up with their own `esco`-only deletes and never call it.

- [ ] **Step 3: Write the failing schema test**

Create `packages/core/tests/integration/test_esco_schema.py`:

```python
"""Integration tests for the esco schema (migration 0022)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.skills.vector import to_pgvector
from tests.integration.skills_fixtures import (
    axis_vector,
    live_app_engine,
    live_owner_engine,
)


class TestEscoSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        self.skill_id = f"fixture-schema-{uuid.uuid4().hex[:8]}"
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill (skill_id, concept_uri, preferred_label) "
                    "VALUES (:id, :uri, 'zzfixture schema skill')"
                ),
                {"id": self.skill_id, "uri": f"http://example.test/{self.skill_id}"},
            )

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM esco.skill WHERE skill_id = :id"),
                {"id": self.skill_id},
            )

    def test_skill_embedding_accepts_a_768_dimension_vector(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_embedding "
                    "(skill_id, embedding_model, embedding) "
                    "VALUES (:id, 'test-model', CAST(:v AS vector))"
                ),
                {"id": self.skill_id, "v": to_pgvector(axis_vector(0))},
            )
            count = conn.execute(
                text("SELECT count(*) FROM esco.skill_embedding WHERE skill_id = :id"),
                {"id": self.skill_id},
            ).scalar_one()
        self.assertEqual(count, 1)

    def test_skill_embedding_rejects_a_wrong_dimension_vector(self) -> None:
        with self.assertRaises(DBAPIError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO esco.skill_embedding "
                        "(skill_id, embedding_model, embedding) "
                        "VALUES (:id, 'test-model', CAST('[1,2,3]' AS vector))"
                    ),
                    {"id": self.skill_id},
                )

    def test_deleting_a_skill_cascades_to_labels_and_embeddings(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_label "
                    "(skill_id, label, label_norm, is_preferred) "
                    "VALUES (:id, 'L', 'zzfixture l', true)"
                ),
                {"id": self.skill_id},
            )
            conn.execute(
                text("DELETE FROM esco.skill WHERE skill_id = :id"),
                {"id": self.skill_id},
            )
            remaining = conn.execute(
                text("SELECT count(*) FROM esco.skill_label WHERE skill_id = :id"),
                {"id": self.skill_id},
            ).scalar_one()
        self.assertEqual(remaining, 0)

    def test_app_role_can_read_but_not_write(self) -> None:
        with self.app.connect() as conn:
            found = conn.execute(
                text("SELECT count(*) FROM esco.skill WHERE skill_id = :id"),
                {"id": self.skill_id},
            ).scalar_one()
        self.assertEqual(found, 1)
        with self.assertRaises(DBAPIError):
            with self.app.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO esco.skill (skill_id, concept_uri, "
                        "preferred_label) VALUES ('fixture-x', 'http://x', 'x')"
                    )
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.integration.test_esco_schema -v`
Expected: FAIL/ERROR — `relation "esco.skill" does not exist` (or `schema "esco" does not exist`)

- [ ] **Step 5: Write the migration**

Create `db/migrations/versions/0022_create_esco_schema.py`:

```python
"""create the esco schema

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-19

The ESCO skills/occupations taxonomy (PLAN.md Step 14, JOB-215), loaded
from the bulk CSV release by `pipeline load-esco`. SHARED-zone reference
data: the same vocabulary for every user, so no user_id and no RLS
(docs/tenancy.md — "taxonomy" is shared).

Loader-owned: job_search_app gets SELECT only (the review API reads it to
search skills and label candidates); nothing request-serving writes here.

`esco.skill_embedding` is a derived, rebuildable cache — one vector per
skill, of its preferred label, at `nomic-embed-text`'s 768 dimensions,
with `embedding_model` recorded per row. It deliberately has NO ANN
index: an exact scan over ~14k rows is milliseconds, and DECISIONS.md
fixes the pgvector index type (and the embedding dimension for CV/JD
chunks) at Step 15. If Step 15 changes the model or dimension, this table
is rebuilt with `embed-esco`; no source data is lost.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS esco")
    op.execute("GRANT USAGE ON SCHEMA esco TO job_search_app")

    op.create_table(
        "skill",
        sa.Column("skill_id", sa.Text(), primary_key=True),
        sa.Column("concept_uri", sa.Text(), nullable=False, unique=True),
        sa.Column("preferred_label", sa.Text(), nullable=False),
        sa.Column("skill_type", sa.Text(), nullable=True),
        sa.Column("reuse_level", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        schema="esco",
    )
    op.create_table(
        "skill_label",
        sa.Column(
            "skill_id",
            sa.Text(),
            sa.ForeignKey("esco.skill.skill_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("label_norm", sa.Text(), nullable=False),
        sa.Column("is_preferred", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("label_norm", "skill_id"),
        schema="esco",
    )
    op.create_index(
        "ix_esco_skill_label_skill_id", "skill_label", ["skill_id"], schema="esco"
    )
    op.create_table(
        "occupation",
        sa.Column("occupation_id", sa.Text(), primary_key=True),
        sa.Column("concept_uri", sa.Text(), nullable=False, unique=True),
        sa.Column("preferred_label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        schema="esco",
    )
    op.create_table(
        "occupation_skill",
        sa.Column(
            "occupation_id",
            sa.Text(),
            sa.ForeignKey("esco.occupation.occupation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            sa.Text(),
            sa.ForeignKey("esco.skill.skill_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("occupation_id", "skill_id", "relation_type"),
        schema="esco",
    )
    # The pgvector column type has no SQLAlchemy core equivalent without the
    # pgvector driver package, so this one table is raw DDL.
    op.execute(
        "CREATE TABLE esco.skill_embedding ("
        "skill_id TEXT PRIMARY KEY REFERENCES esco.skill (skill_id) "
        "ON DELETE CASCADE, "
        "embedding_model TEXT NOT NULL, "
        "embedding vector(768) NOT NULL)"
    )

    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA esco TO job_search_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA esco "
        "GRANT SELECT ON TABLES TO job_search_app"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA esco "
        "REVOKE SELECT ON TABLES FROM job_search_app"
    )
    op.execute("DROP TABLE esco.skill_embedding")
    op.drop_table("occupation_skill", schema="esco")
    op.drop_table("occupation", schema="esco")
    op.drop_index("ix_esco_skill_label_skill_id", table_name="skill_label", schema="esco")
    op.drop_table("skill_label", schema="esco")
    op.drop_table("skill", schema="esco")
    op.execute("REVOKE USAGE ON SCHEMA esco FROM job_search_app")
    op.execute("DROP SCHEMA esco")
```

- [ ] **Step 6: Apply the migration and verify the tests pass**

Run (from `job_search/`): `alembic -c db/alembic.ini current` — expect `0021 (head)`.
Run: `alembic -c db/alembic.ini upgrade head` — expect `Running upgrade 0021 -> 0022`.
Run: `cd packages/core && python -m unittest tests.integration.test_esco_schema -v`
Expected: PASS (4 tests)

Note: this shared DB now carries revision 0022, which the main checkout does not have until this branch merges. That is expected; run `alembic -c db/alembic.ini downgrade 0021` from this worktree before switching back if the main checkout needs to run migrations.

- [ ] **Step 7: Commit**

```bash
black db/migrations/versions/0022_create_esco_schema.py packages/core && isort packages/core && ruff check db/migrations/versions/0022_create_esco_schema.py packages/core
git add db/migrations/versions/0022_create_esco_schema.py packages/core/core/skills/vector.py packages/core/tests/integration/skills_fixtures.py packages/core/tests/integration/test_esco_schema.py
git commit -m "feat(job_search): add esco schema for the skills taxonomy (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: ESCO loader and `load-esco` command

**Files:**
- Create: `packages/core/core/skills/esco_load.py`
- Create: `packages/core/tests/fixtures/esco/skills_en.csv`, `occupations_en.csv`, `occupationSkillRelations_en.csv`
- Modify: `apps/pipeline/app/cli.py` (add `_cmd_load_esco`, parser, dispatch)
- Test: `packages/core/tests/integration/test_esco_load.py`, `packages/core/tests/test_pipeline_cli_skills.py`

**Interfaces:**
- Consumes: `normalise_skill`, tables from Task 2, `FIXTURE_ESCO_DIR`, `live_owner_engine`.
- Produces:
  - `EscoLoadError(Exception)`
  - `EscoLoadCounts(skills: int, skill_labels: int, occupations: int, occupation_skills: int, skipped_relations: int)` (frozen dataclass)
  - `concept_id(concept_uri: str) -> str` — trailing path segment of a concept URI.
  - `load_esco(engine: Engine, directory: Path) -> EscoLoadCounts` — idempotent; only touches the skill/occupation IDs present in the release.
  - CLI: `pipeline load-esco <directory>` (exit 1 with a message on `EscoLoadError`).

- [ ] **Step 1: Create the synthetic fixture release**

All labels carry a `zzfixture` prefix so a fixture label can never collide with a real ESCO label if the real dataset is loaded in the dev DB.

`packages/core/tests/fixtures/esco/skills_en.csv`:

```csv
conceptType,conceptUri,skillType,reuseLevel,preferredLabel,altLabels,hiddenLabels,status,modifiedDate,scopeNote,definition,inScheme,description
KnowledgeSkillCompetence,http://data.europa.eu/esco/skill/fixture-cloud,knowledge,sector-specific,zzfixture cloud technologies,"zzfixture cloud platforms
zzfixture cloud computing",,released,2024-01-01T00:00:00Z,,,http://data.europa.eu/esco/concept-scheme/skills,Deploy and operate services on cloud infrastructure.
KnowledgeSkillCompetence,http://data.europa.eu/esco/skill/fixture-python,skill/competence,cross-sectoral,zzfixture python,zzfixture python programming,,released,2024-01-01T00:00:00Z,,,http://data.europa.eu/esco/concept-scheme/skills,Write programs in Python.
KnowledgeSkillCompetence,http://data.europa.eu/esco/skill/fixture-sql,knowledge,cross-sectoral,zzfixture sql,zzfixture structured query language,,released,2024-01-01T00:00:00Z,,,http://data.europa.eu/esco/concept-scheme/skills,Query relational databases.
```

`packages/core/tests/fixtures/esco/occupations_en.csv`:

```csv
conceptType,conceptUri,iscoGroup,preferredLabel,altLabels,hiddenLabels,status,modifiedDate,regulatedProfessionNote,scopeNote,definition,inScheme,description,code
Occupation,http://data.europa.eu/esco/occupation/fixture-occ-data-engineer,2521,zzfixture data engineer,,,released,2024-01-01T00:00:00Z,,,,http://data.europa.eu/esco/concept-scheme/occupations,Builds data pipelines.,2521.1
Occupation,http://data.europa.eu/esco/occupation/fixture-occ-cloud-engineer,2523,zzfixture cloud engineer,,,released,2024-01-01T00:00:00Z,,,,http://data.europa.eu/esco/concept-scheme/occupations,Runs cloud platforms.,2523.1
```

`packages/core/tests/fixtures/esco/occupationSkillRelations_en.csv` (the last row points at a skill not in the release and must be skipped):

```csv
occupationUri,relationType,skillType,skillUri
http://data.europa.eu/esco/occupation/fixture-occ-data-engineer,essential,knowledge,http://data.europa.eu/esco/skill/fixture-python
http://data.europa.eu/esco/occupation/fixture-occ-data-engineer,essential,knowledge,http://data.europa.eu/esco/skill/fixture-sql
http://data.europa.eu/esco/occupation/fixture-occ-data-engineer,optional,knowledge,http://data.europa.eu/esco/skill/fixture-cloud
http://data.europa.eu/esco/occupation/fixture-occ-cloud-engineer,essential,knowledge,http://data.europa.eu/esco/skill/fixture-cloud
http://data.europa.eu/esco/occupation/fixture-occ-cloud-engineer,essential,knowledge,http://data.europa.eu/esco/skill/fixture-unknown
```

- [ ] **Step 2: Write the failing loader test**

Create `packages/core/tests/integration/test_esco_load.py`:

```python
"""Integration tests for core.skills.esco_load against live Postgres."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text

from core.skills.esco_load import EscoLoadError, concept_id, load_esco
from tests.integration.skills_fixtures import FIXTURE_ESCO_DIR, live_owner_engine


def _purge_esco_fixtures(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM esco.occupation WHERE occupation_id LIKE 'fixture-%'"))
        conn.execute(text("DELETE FROM esco.skill WHERE skill_id LIKE 'fixture-%'"))


class TestConceptId(unittest.TestCase):
    def test_returns_trailing_uri_segment(self) -> None:
        self.assertEqual(
            concept_id("http://data.europa.eu/esco/skill/abc-123"), "abc-123"
        )

    def test_ignores_a_trailing_slash(self) -> None:
        self.assertEqual(concept_id("http://x/skill/abc/"), "abc")


class TestLoadEsco(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        _purge_esco_fixtures(self.engine)

    def tearDown(self) -> None:
        _purge_esco_fixtures(self.engine)

    def _count(self, sql: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(text(sql)).scalar_one()

    def test_loads_the_fixture_release_and_reports_counts(self) -> None:
        counts = load_esco(self.engine, FIXTURE_ESCO_DIR)
        self.assertEqual(counts.skills, 3)
        self.assertEqual(counts.skill_labels, 7)
        self.assertEqual(counts.occupations, 2)
        self.assertEqual(counts.occupation_skills, 4)
        self.assertEqual(counts.skipped_relations, 1)

    def test_labels_are_normalised_and_flag_the_preferred_one(self) -> None:
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT label_norm, is_preferred FROM esco.skill_label "
                    "WHERE skill_id = 'fixture-cloud' ORDER BY label_norm"
                )
            ).all()
        self.assertEqual(
            [(r.label_norm, r.is_preferred) for r in rows],
            [
                ("zzfixture cloud computing", False),
                ("zzfixture cloud platforms", False),
                ("zzfixture cloud technologies", True),
            ],
        )

    def test_skill_id_is_the_concept_uri_tail(self) -> None:
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.connect() as conn:
            uri = conn.execute(
                text("SELECT concept_uri FROM esco.skill WHERE skill_id = 'fixture-sql'")
            ).scalar_one()
        self.assertEqual(uri, "http://data.europa.eu/esco/skill/fixture-sql")

    def test_is_idempotent(self) -> None:
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        self.assertEqual(
            self._count("SELECT count(*) FROM esco.skill WHERE skill_id LIKE 'fixture-%'"),
            3,
        )
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.skill_label WHERE skill_id LIKE 'fixture-%'"
            ),
            7,
        )

    def test_reload_drops_labels_removed_from_the_release(self) -> None:
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            for source in FIXTURE_ESCO_DIR.glob("*.csv"):
                shutil.copy(source, release / source.name)
            skills = (release / "skills_en.csv").read_text()
            skills = skills.replace('zzfixture cloud platforms\nzzfixture cloud computing', 'zzfixture cloud computing')
            (release / "skills_en.csv").write_text(skills)
            load_esco(self.engine, release)
        self.assertEqual(
            self._count(
                "SELECT count(*) FROM esco.skill_label "
                "WHERE label_norm = 'zzfixture cloud platforms'"
            ),
            0,
        )

    def test_missing_file_raises_naming_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(EscoLoadError) as ctx:
                load_esco(self.engine, Path(tmp))
        self.assertIn("skills_en.csv", str(ctx.exception))

    def test_missing_column_raises_listing_the_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            for source in FIXTURE_ESCO_DIR.glob("*.csv"):
                shutil.copy(source, release / source.name)
            (release / "skills_en.csv").write_text("conceptUri,preferredLabel\nx,y\n")
            with self.assertRaises(EscoLoadError) as ctx:
                load_esco(self.engine, release)
        self.assertIn("altLabels", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.integration.test_esco_load -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'core.skills.esco_load'`

- [ ] **Step 4: Write the loader**

Create `packages/core/core/skills/esco_load.py`:

```python
"""Load the ESCO bulk CSV release into the `esco` schema (Step 14).

Reads `skills_en.csv`, `occupations_en.csv` and
`occupationSkillRelations_en.csv` (English release) from a directory the
user downloaded from the ESCO portal. Idempotent: upserts on the primary
keys, and replaces the label/relation rows of only the skills and
occupations present in the release, so re-loading a newer release drops
labels ESCO removed without touching anything else in the schema.

Column names are validated up front — if a future release renames one,
the loader fails naming the missing column rather than loading garbage.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, Engine, TextClause, text

from core.skills.normalise import normalise_skill

_SKILLS_FILE = "skills_en.csv"
_OCCUPATIONS_FILE = "occupations_en.csv"
_RELATIONS_FILE = "occupationSkillRelations_en.csv"

_REQUIRED_COLUMNS = {
    _SKILLS_FILE: {
        "conceptUri",
        "skillType",
        "reuseLevel",
        "preferredLabel",
        "altLabels",
        "hiddenLabels",
        "description",
    },
    _OCCUPATIONS_FILE: {"conceptUri", "preferredLabel", "description"},
    _RELATIONS_FILE: {"occupationUri", "relationType", "skillUri"},
}


class EscoLoadError(Exception):
    """Raised when the ESCO release directory is missing a file or column."""


@dataclass(frozen=True)
class EscoLoadCounts:
    """Row counts written by one `load_esco` run.

    Attributes:
        skills: Skills upserted.
        skill_labels: Label rows written (preferred + alt + hidden).
        occupations: Occupations upserted.
        occupation_skills: Occupation-skill relations written.
        skipped_relations: Relations skipped because their skill or
            occupation is not in this release.
    """

    skills: int
    skill_labels: int
    occupations: int
    occupation_skills: int
    skipped_relations: int


def concept_id(concept_uri: str) -> str:
    """Return the identifier part of an ESCO concept URI.

    Args:
        concept_uri: e.g. ``http://data.europa.eu/esco/skill/<uuid>``.

    Returns:
        The trailing path segment (the UUID).
    """
    return concept_uri.rstrip("/").rsplit("/", 1)[-1]


def _read_rows(directory: Path, filename: str) -> list[dict[str, str]]:
    """Read one release CSV, validating that it exists and has its columns.

    Args:
        directory: The release directory.
        filename: Which file to read.

    Returns:
        The rows as dicts keyed by header name.

    Raises:
        EscoLoadError: If the file is missing or lacks a required column.
    """
    path = directory / filename
    if not path.is_file():
        raise EscoLoadError(f"missing ESCO file: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = _REQUIRED_COLUMNS[filename] - set(reader.fieldnames or [])
        if missing:
            raise EscoLoadError(
                f"{filename} is missing required column(s): {', '.join(sorted(missing))}"
            )
        return list(reader)


def _split_labels(cell: str) -> list[str]:
    """Split a newline-separated ESCO label cell.

    Args:
        cell: The raw `altLabels`/`hiddenLabels` cell.

    Returns:
        The non-empty, stripped labels.
    """
    return [line.strip() for line in cell.splitlines() if line.strip()]


def _labels_for(row: dict[str, str]) -> dict[str, tuple[str, bool]]:
    """Collect one skill's labels, keyed by normalised form.

    Args:
        row: A `skills_en.csv` row.

    Returns:
        Map of `label_norm` to `(original label, is_preferred)`, with
        duplicates (after normalisation) collapsed and `is_preferred`
        true if any duplicate was the preferred label.
    """
    candidates = [(row["preferredLabel"].strip(), True)]
    candidates += [(label, False) for label in _split_labels(row["altLabels"])]
    candidates += [(label, False) for label in _split_labels(row["hiddenLabels"])]
    collected: dict[str, tuple[str, bool]] = {}
    for label, is_preferred in candidates:
        norm = normalise_skill(label)
        if not norm:
            continue
        previous = collected.get(norm)
        collected[norm] = (
            previous[0] if previous else label,
            is_preferred or (previous[1] if previous else False),
        )
    return collected


def _execute_many(conn: Connection, statement: TextClause, params: list[dict]) -> None:
    """Execute a statement once per parameter dict, skipping an empty list.

    Args:
        conn: An open connection.
        statement: The statement to run.
        params: Parameter dicts; nothing runs if empty.
    """
    if params:
        conn.execute(statement, params)


_UPSERT_SKILL = text(
    "INSERT INTO esco.skill (skill_id, concept_uri, preferred_label, skill_type, "
    "reuse_level, description) VALUES (:skill_id, :concept_uri, :preferred_label, "
    ":skill_type, :reuse_level, :description) "
    "ON CONFLICT (skill_id) DO UPDATE SET concept_uri = EXCLUDED.concept_uri, "
    "preferred_label = EXCLUDED.preferred_label, skill_type = EXCLUDED.skill_type, "
    "reuse_level = EXCLUDED.reuse_level, description = EXCLUDED.description"
)
_INSERT_LABEL = text(
    "INSERT INTO esco.skill_label (skill_id, label, label_norm, is_preferred) "
    "VALUES (:skill_id, :label, :label_norm, :is_preferred) "
    "ON CONFLICT (label_norm, skill_id) DO NOTHING"
)
_UPSERT_OCCUPATION = text(
    "INSERT INTO esco.occupation (occupation_id, concept_uri, preferred_label, "
    "description) VALUES (:occupation_id, :concept_uri, :preferred_label, "
    ":description) ON CONFLICT (occupation_id) DO UPDATE SET "
    "concept_uri = EXCLUDED.concept_uri, "
    "preferred_label = EXCLUDED.preferred_label, "
    "description = EXCLUDED.description"
)
_INSERT_RELATION = text(
    "INSERT INTO esco.occupation_skill (occupation_id, skill_id, relation_type) "
    "VALUES (:occupation_id, :skill_id, :relation_type) ON CONFLICT DO NOTHING"
)


def load_esco(engine: Engine, directory: Path) -> EscoLoadCounts:
    """Load an ESCO English CSV release into the `esco` schema.

    Args:
        engine: The owner-role engine (the `esco` schema is loader-owned).
        directory: Directory containing the release's CSV files.

    Returns:
        The row counts written.

    Raises:
        EscoLoadError: If a required file or column is missing.
    """
    skills = _read_rows(directory, _SKILLS_FILE)
    occupations = _read_rows(directory, _OCCUPATIONS_FILE)
    relations = _read_rows(directory, _RELATIONS_FILE)

    skill_params: list[dict] = []
    label_params: list[dict] = []
    for row in skills:
        skill_id = concept_id(row["conceptUri"])
        skill_params.append(
            {
                "skill_id": skill_id,
                "concept_uri": row["conceptUri"],
                "preferred_label": row["preferredLabel"].strip(),
                "skill_type": row["skillType"] or None,
                "reuse_level": row["reuseLevel"] or None,
                "description": row["description"] or None,
            }
        )
        for norm, (label, is_preferred) in _labels_for(row).items():
            label_params.append(
                {
                    "skill_id": skill_id,
                    "label": label,
                    "label_norm": norm,
                    "is_preferred": is_preferred,
                }
            )

    occupation_params = [
        {
            "occupation_id": concept_id(row["conceptUri"]),
            "concept_uri": row["conceptUri"],
            "preferred_label": row["preferredLabel"].strip(),
            "description": row["description"] or None,
        }
        for row in occupations
    ]
    skill_ids = {p["skill_id"] for p in skill_params}
    occupation_ids = {p["occupation_id"] for p in occupation_params}

    relation_params: list[dict] = []
    skipped = 0
    for row in relations:
        occupation_id = concept_id(row["occupationUri"])
        skill_id = concept_id(row["skillUri"])
        if occupation_id not in occupation_ids or skill_id not in skill_ids:
            skipped += 1
            continue
        relation_params.append(
            {
                "occupation_id": occupation_id,
                "skill_id": skill_id,
                "relation_type": row["relationType"],
            }
        )

    with engine.begin() as conn:
        _execute_many(conn, _UPSERT_SKILL, skill_params)
        conn.execute(
            text("DELETE FROM esco.skill_label WHERE skill_id = ANY(:ids)"),
            {"ids": sorted(skill_ids)},
        )
        _execute_many(conn, _INSERT_LABEL, label_params)
        _execute_many(conn, _UPSERT_OCCUPATION, occupation_params)
        conn.execute(
            text("DELETE FROM esco.occupation_skill WHERE occupation_id = ANY(:ids)"),
            {"ids": sorted(occupation_ids)},
        )
        _execute_many(conn, _INSERT_RELATION, relation_params)

    return EscoLoadCounts(
        skills=len(skill_params),
        skill_labels=len(label_params),
        occupations=len(occupation_params),
        occupation_skills=len(relation_params),
        skipped_relations=skipped,
    )
```

- [ ] **Step 5: Run the loader tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.integration.test_esco_load -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Write the failing CLI test**

Create `packages/core/tests/test_pipeline_cli_skills.py`:

```python
"""Unit tests for the Step 14 pipeline CLI subcommands.

Only argument parsing and error handling — the underlying functions have
their own integration tests.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

# See test_pipeline_cli_run_evals.py for why sys.modules needs no
# snapshot/restore here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402


class TestLoadEscoSubcommand(unittest.TestCase):
    def test_reports_a_missing_release_directory_and_exits_one(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exit_code = main(["load-esco", "/nonexistent/esco/release"])
        self.assertEqual(exit_code, 1)
        self.assertIn("skills_en.csv", out.getvalue())


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.test_pipeline_cli_skills -v`
Expected: FAIL — the `load-esco` subcommand does not exist (argparse `SystemExit: 2`, or the scaffold message).

- [ ] **Step 7: Add the `load-esco` subcommand**

In `apps/pipeline/app/cli.py`:

1. Add imports (isort will place them): `from pathlib import Path` and `from core.skills.esco_load import EscoLoadError, load_esco`.
2. Add above `_EVAL_TASKS`:

```python
def _cmd_load_esco(args: argparse.Namespace) -> int:
    """Run the `load-esco` subcommand.

    Args:
        args: Parsed CLI arguments — `directory`, the ESCO release folder.

    Returns:
        0 on success, 1 if the release directory is missing a file or column.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        counts = load_esco(engine, Path(args.directory))
    except EscoLoadError as exc:
        print(f"load-esco: {exc}")
        return 1
    print(
        f"load-esco complete: skills={counts.skills} "
        f"skill_labels={counts.skill_labels} occupations={counts.occupations} "
        f"occupation_skills={counts.occupation_skills} "
        f"skipped_relations={counts.skipped_relations}"
    )
    return 0
```

3. In `main`, before `args = parser.parse_args(argv)`:

```python
    load_esco_parser = subparsers.add_parser(
        "load-esco",
        help="Load an ESCO English CSV release directory into the esco schema",
    )
    load_esco_parser.add_argument("directory")
```

4. In the dispatch chain (before `if args.command == "run-evals"`):

```python
    if args.command == "load-esco":
        return _cmd_load_esco(args)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.test_pipeline_cli_skills tests.test_pipeline_cli tests.test_pipeline_cli_run_evals -v`
Expected: PASS (existing CLI tests unaffected)

- [ ] **Step 9: Commit**

```bash
black apps packages/core && isort apps packages/core && ruff check apps packages/core
git add packages/core/core/skills/esco_load.py packages/core/tests/fixtures/esco packages/core/tests/integration/test_esco_load.py packages/core/tests/test_pipeline_cli_skills.py apps/pipeline/app/cli.py
git commit -m "feat(job_search): ESCO CSV loader and load-esco command (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Silver skill tables migration

**Files:**
- Create: `db/migrations/versions/0023_create_silver_skill_tables.py`
- Modify: `packages/core/tests/integration/skills_fixtures.py` (add two insert helpers)
- Modify: `docs/superpowers/specs/2026-09-19-step14-esco-skill-normalisation-design.md` (deviations 4)
- Test: `packages/core/tests/integration/test_skills_silver_schema.py`

**Interfaces:**
- Consumes: migration 0022, `purge_fixtures`, `live_owner_engine`, `live_app_engine`.
- Produces tables (all in schema `silver`):
  - `custom_skill(skill_id TEXT PK CHECK LIKE 'custom:%', canonical_label TEXT NOT NULL, created_at)`
  - `skill_alias(alias_norm TEXT PK, skill_id TEXT NOT NULL, source TEXT CHECK IN ('seed','review'), created_at)`
  - `skill_mapping(raw_norm TEXT PK, raw_example TEXT NOT NULL, skill_id TEXT NULL, method TEXT, score NUMERIC NULL, candidate_skill_id TEXT NULL, candidate_score NUMERIC NULL, review_status TEXT NULL, seen_in_cv BOOL NOT NULL DEFAULT false, mapped_at TIMESTAMPTZ)`
  - `job_skill_extraction(job_group_id, prompt_version, model NULL, extracted_at, PK(job_group_id, prompt_version))`
  - `job_skill_raw(job_group_id, prompt_version, raw_skill, raw_norm, requirement_level, PK(job_group_id, prompt_version, raw_norm), FK→job_skill_extraction ON DELETE CASCADE)`
  - Test helpers `insert_mapping(conn, raw_norm, *, skill_id=None, method="none", score=None, review_status="open", candidate_skill_id=None, candidate_score=None, seen_in_cv=False) -> None` and `insert_job_skills(conn, job_group_id, prompt_version, skills: list[tuple[str, str, str]]) -> None` (tuples are `(raw_skill, raw_norm, requirement_level)`; also inserts the parent `job_skill_extraction` row).

- [ ] **Step 1: Add the insert helpers to the shared test fixtures**

Append to `packages/core/tests/integration/skills_fixtures.py`:

```python
def insert_mapping(
    conn,
    raw_norm: str,
    *,
    skill_id: str | None = None,
    method: str = "none",
    score: float | None = None,
    review_status: str | None = "open",
    candidate_skill_id: str | None = None,
    candidate_score: float | None = None,
    seen_in_cv: bool = False,
) -> None:
    """Insert one `silver.skill_mapping` fixture row.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string (also used as `raw_example`).
        skill_id: The mapped skill, or None if unmapped.
        method: alias | label | embedding | none.
        score: Cosine score for an embedding match.
        review_status: NULL, open, rejected, resolved or dismissed.
        candidate_skill_id: Best below-threshold neighbour, if any.
        candidate_score: Its cosine score.
        seen_in_cv: Whether a CV contained this string.
    """
    conn.execute(
        text(
            "INSERT INTO silver.skill_mapping (raw_norm, raw_example, skill_id, "
            "method, score, candidate_skill_id, candidate_score, review_status, "
            "seen_in_cv) VALUES (:raw_norm, :raw_norm, :skill_id, :method, :score, "
            ":candidate_skill_id, :candidate_score, :review_status, :seen_in_cv)"
        ),
        {
            "raw_norm": raw_norm,
            "skill_id": skill_id,
            "method": method,
            "score": score,
            "candidate_skill_id": candidate_skill_id,
            "candidate_score": candidate_score,
            "review_status": review_status,
            "seen_in_cv": seen_in_cv,
        },
    )


def insert_job_skills(
    conn,
    job_group_id: str,
    prompt_version: str,
    skills: list[tuple[str, str, str]],
) -> None:
    """Insert a `job_skill_extraction` row and its `job_skill_raw` rows.

    Args:
        conn: An open connection inside the caller's transaction.
        job_group_id: The job (use a `fixture-job-` prefix).
        prompt_version: The extraction's prompt version.
        skills: `(raw_skill, raw_norm, requirement_level)` tuples.
    """
    conn.execute(
        text(
            "INSERT INTO silver.job_skill_extraction "
            "(job_group_id, prompt_version, model) "
            "VALUES (:job, :version, 'fixture-model')"
        ),
        {"job": job_group_id, "version": prompt_version},
    )
    for raw_skill, raw_norm, level in skills:
        conn.execute(
            text(
                "INSERT INTO silver.job_skill_raw (job_group_id, prompt_version, "
                "raw_skill, raw_norm, requirement_level) "
                "VALUES (:job, :version, :raw, :norm, :level)"
            ),
            {
                "job": job_group_id,
                "version": prompt_version,
                "raw": raw_skill,
                "norm": raw_norm,
                "level": level,
            },
        )
```

- [ ] **Step 2: Write the failing schema test**

Create `packages/core/tests/integration/test_skills_silver_schema.py`:

```python
"""Integration tests for the silver skill tables (migration 0023)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.skills_fixtures import (
    insert_job_skills,
    insert_mapping,
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)


class TestSkillMappingConstraints(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        self.suffix = uuid.uuid4().hex[:8]

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _insert(self, **kwargs) -> None:
        with self.engine.begin() as conn:
            insert_mapping(conn, f"zzfixture {self.suffix}", **kwargs)

    def test_accepts_an_unmapped_open_row(self) -> None:
        self._insert()

    def test_accepts_a_mapped_label_row_with_no_review_status(self) -> None:
        self._insert(skill_id="fixture-x", method="label", review_status=None)

    def test_accepts_a_rejected_row_with_no_skill(self) -> None:
        self._insert(review_status="rejected", candidate_skill_id="fixture-x")

    def test_rejects_method_none_with_a_skill(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(skill_id="fixture-x", method="none", review_status=None)

    def test_rejects_a_mapped_method_without_a_skill(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(skill_id=None, method="label", review_status=None)

    def test_rejects_open_status_with_a_skill(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(skill_id="fixture-x", method="label", review_status="open")

    def test_rejects_resolved_status_without_a_skill(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(review_status="resolved")

    def test_rejects_an_unknown_review_status(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(review_status="bogus")

    def test_rejects_an_unknown_method(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(skill_id="fixture-x", method="magic", review_status=None)


class TestOtherSilverSkillTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def test_custom_skill_id_must_use_the_custom_prefix(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
                        "VALUES ('not-custom', 'x')"
                    )
                )

    def test_alias_source_must_be_seed_or_review(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
                        "VALUES ('zzfixture a', 'fixture-x', 'other')"
                    )
                )

    def test_job_skill_raw_rows_need_a_parent_extraction_row(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO silver.job_skill_raw (job_group_id, "
                        "prompt_version, raw_skill, raw_norm, requirement_level) "
                        "VALUES (:job, 'local.v1', 'Python', 'python', 'must_have')"
                    ),
                    {"job": self.job},
                )

    def test_requirement_level_is_constrained(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                insert_job_skills(
                    conn, self.job, "local.v1", [("Python", "python", "maybe")]
                )

    def test_deleting_an_extraction_cascades_to_its_raw_skills(self) -> None:
        with self.engine.begin() as conn:
            insert_job_skills(
                conn, self.job, "local.v1", [("Python", "python", "must_have")]
            )
            conn.execute(
                text("DELETE FROM silver.job_skill_extraction WHERE job_group_id = :j"),
                {"j": self.job},
            )
            left = conn.execute(
                text("SELECT count(*) FROM silver.job_skill_raw WHERE job_group_id = :j"),
                {"j": self.job},
            ).scalar_one()
        self.assertEqual(left, 0)

    def test_app_role_can_write_review_tables_but_not_extraction_tables(self) -> None:
        with self.app.begin() as conn:
            insert_mapping(conn, f"zzfixture app {self.job}")
        with self.assertRaises(DBAPIError):
            with self.app.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO silver.job_skill_extraction "
                        "(job_group_id, prompt_version) VALUES (:j, 'local.v1')"
                    ),
                    {"j": self.job},
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.integration.test_skills_silver_schema -v`
Expected: ERROR — `relation "silver.skill_mapping" does not exist`

- [ ] **Step 4: Write the migration**

Create `db/migrations/versions/0023_create_silver_skill_tables.py`:

```python
"""create the silver skill-normalisation tables

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-19

PLAN.md Step 14 (JOB-215). All five tables are SHARED-zone (docs/
tenancy.md: taxonomy and per-job extraction are the same facts for every
user), so no user_id and no RLS.

- custom_skill: skills ESCO lacks (`custom:<slug>`), created by the seed
  file or the review page.
- skill_alias: curated `alias_norm -> skill_id`; outranks ESCO labels.
- skill_mapping: one row per distinct normalised skill string, shared by
  CV and JD skills. `skill_id` is polymorphic (ESCO id or `custom:`), so
  it deliberately has no foreign key; dbt's `silver__skill` relationships
  test covers it. Invariants are CHECK constraints.
- job_skill_extraction / job_skill_raw: the LLM's per-job output. The
  extraction row exists even for a job that yielded zero skills, so it is
  not re-sent to the LLM every run.

Written by the owner role via pipeline CLI subcommands, except the three
review tables (custom_skill, skill_alias, skill_mapping), which the
review API writes — so job_search_app gets INSERT/UPDATE on those only.
SELECT on everything comes from 0010's default privileges on `silver`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.create_table(
        "custom_skill",
        sa.Column("skill_id", sa.Text(), primary_key=True),
        sa.Column("canonical_label", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "skill_id LIKE 'custom:%'", name="ck_custom_skill_id_prefix"
        ),
        schema="silver",
    )
    op.create_table(
        "skill_alias",
        sa.Column("alias_norm", sa.Text(), primary_key=True),
        sa.Column("skill_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "source IN ('seed', 'review')", name="ck_skill_alias_source"
        ),
        schema="silver",
    )
    op.create_table(
        "skill_mapping",
        sa.Column("raw_norm", sa.Text(), primary_key=True),
        sa.Column("raw_example", sa.Text(), nullable=False),
        sa.Column("skill_id", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("score", sa.Numeric(), nullable=True),
        sa.Column("candidate_skill_id", sa.Text(), nullable=True),
        sa.Column("candidate_score", sa.Numeric(), nullable=True),
        sa.Column("review_status", sa.Text(), nullable=True),
        sa.Column(
            "seen_in_cv", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "mapped_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "method IN ('alias', 'label', 'embedding', 'none')",
            name="ck_skill_mapping_method",
        ),
        sa.CheckConstraint(
            "(skill_id IS NULL) = (method = 'none')",
            name="ck_skill_mapping_none_iff_unmapped",
        ),
        sa.CheckConstraint(
            "review_status IS NULL OR review_status IN "
            "('open', 'rejected', 'resolved', 'dismissed')",
            name="ck_skill_mapping_review_status",
        ),
        sa.CheckConstraint(
            "review_status IS NULL OR review_status = 'resolved' "
            "OR skill_id IS NULL",
            name="ck_skill_mapping_unresolved_has_no_skill",
        ),
        sa.CheckConstraint(
            "review_status IS DISTINCT FROM 'resolved' OR skill_id IS NOT NULL",
            name="ck_skill_mapping_resolved_has_skill",
        ),
        schema="silver",
    )
    op.create_index(
        "ix_skill_mapping_review_status",
        "skill_mapping",
        ["review_status"],
        schema="silver",
    )
    op.create_table(
        "job_skill_extraction",
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("job_group_id", "prompt_version"),
        schema="silver",
    )
    op.create_table(
        "job_skill_raw",
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("raw_skill", sa.Text(), nullable=False),
        sa.Column("raw_norm", sa.Text(), nullable=False),
        sa.Column("requirement_level", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("job_group_id", "prompt_version", "raw_norm"),
        sa.ForeignKeyConstraint(
            ["job_group_id", "prompt_version"],
            [
                "silver.job_skill_extraction.job_group_id",
                "silver.job_skill_extraction.prompt_version",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "requirement_level IN ('must_have', 'nice_to_have')",
            name="ck_job_skill_raw_requirement_level",
        ),
        schema="silver",
    )
    op.create_index(
        "ix_job_skill_raw_raw_norm", "job_skill_raw", ["raw_norm"], schema="silver"
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON silver.custom_skill, silver.skill_alias, "
        "silver.skill_mapping TO job_search_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON silver.custom_skill, silver.skill_alias, "
        "silver.skill_mapping FROM job_search_app"
    )
    op.drop_index("ix_job_skill_raw_raw_norm", table_name="job_skill_raw", schema="silver")
    op.drop_table("job_skill_raw", schema="silver")
    op.drop_table("job_skill_extraction", schema="silver")
    op.drop_index(
        "ix_skill_mapping_review_status", table_name="skill_mapping", schema="silver"
    )
    op.drop_table("skill_mapping", schema="silver")
    op.drop_table("skill_alias", schema="silver")
    op.drop_table("custom_skill", schema="silver")
```

- [ ] **Step 5: Apply the migration and verify the tests pass**

Run (from `job_search/`): `alembic -c db/alembic.ini upgrade head` — expect `Running upgrade 0022 -> 0023`.
Run: `cd packages/core && python -m unittest tests.integration.test_skills_silver_schema -v`
Expected: PASS (15 tests)

- [ ] **Step 6: Update the spec for the two schema additions**

In `docs/superpowers/specs/2026-09-19-step14-esco-skill-normalisation-design.md` make these edits (use exact single-line matches):

1. Replace `                     review_status TEXT NULL,     -- NULL|'open'|'resolved'|'dismissed'` with:
```
                     candidate_skill_id TEXT NULL, -- best below-threshold neighbour
                     candidate_score NUMERIC NULL,
                     review_status TEXT NULL,     -- NULL|'open'|'rejected'|'resolved'|'dismissed'
```
2. Replace ``- `review_status = 'open'` only while `skill_id IS NULL`.`` with:
```
- `review_status` of `open`, `rejected` or `dismissed` only while `skill_id IS NULL`; `resolved` only when it is set.
```
3. In the Review list section, replace the sentence fragment ``embedding match) clears `skill_id`, sets method `none` and`` and the following line ``  `review_status = 'open'`, so a bad auto-match is correctable rather than`` with:
```
  embedding match) clears `skill_id`, keeps the rejected skill as
  `candidate_skill_id`, sets method `none` and `review_status = 'rejected'`
  (shown in the Unmapped tab, never re-mapped by `--remap-unresolved`), so a
  bad auto-match is correctable rather than
```

- [ ] **Step 7: Commit**

```bash
black db/migrations/versions/0023_create_silver_skill_tables.py packages/core && isort packages/core && ruff check db/migrations/versions/0023_create_silver_skill_tables.py packages/core
git add db/migrations/versions/0023_create_silver_skill_tables.py packages/core/tests/integration docs/superpowers/specs
git commit -m "feat(job_search): silver skill mapping/alias/extraction tables (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: ESCO label embeddings and `embed-esco` command

**Files:**
- Create: `packages/core/core/skills/esco_embed.py`
- Modify: `apps/pipeline/app/cli.py` (add `_build_embedder`, `_cmd_embed_esco`, parser, dispatch)
- Test: `packages/core/tests/integration/test_esco_embed.py`; extend `packages/core/tests/test_pipeline_cli_skills.py`

**Interfaces:**
- Consumes: `to_pgvector`, `EMBEDDING_DIMENSION`, `esco.skill`, `esco.skill_embedding`, `load_esco` (test), `embed_text` from `core.embedding.ollama`.
- Produces:
  - `embed_esco_skills(engine: Engine, *, embed: Callable[[str], list[float]], model: str, skill_ids: list[str] | None = None, batch_size: int = 200) -> int` — embeds each skill's preferred label that has no embedding, or one for a different `model`; returns rows written. `skill_ids` scopes the run (tests).
  - CLI `_build_embedder(http_client: httpx.Client, settings: Settings) -> Callable[[str], list[float]]` and subcommand `pipeline embed-esco`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_esco_embed.py`:

```python
"""Integration tests for core.skills.esco_embed against live Postgres."""

from __future__ import annotations

import unittest

from sqlalchemy import text

from core.skills.esco_embed import embed_esco_skills
from core.skills.esco_load import load_esco
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    axis_vector,
    live_owner_engine,
    purge_fixtures,
)

_IDS = ["fixture-cloud", "fixture-python", "fixture-sql"]
_VECTORS = {
    "zzfixture cloud technologies": axis_vector(0),
    "zzfixture python": axis_vector(1),
    "zzfixture sql": axis_vector(2),
}


class TestEmbedEscoSkills(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        self.calls: list[str] = []

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _embed(self, text_: str) -> list[float]:
        self.calls.append(text_)
        return _VECTORS[text_]

    def _run(self, model: str) -> int:
        return embed_esco_skills(
            self.engine, embed=self._embed, model=model, skill_ids=_IDS
        )

    def _stored(self) -> dict[str, str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT skill_id, embedding_model FROM esco.skill_embedding "
                    "WHERE skill_id LIKE 'fixture-%'"
                )
            ).all()
        return {r.skill_id: r.embedding_model for r in rows}

    def test_embeds_each_skills_preferred_label(self) -> None:
        written = self._run("zzfixture-model-a")
        self.assertEqual(written, 3)
        self.assertEqual(sorted(self.calls), sorted(_VECTORS))
        self.assertEqual(set(self._stored().values()), {"zzfixture-model-a"})

    def test_second_run_with_the_same_model_does_nothing(self) -> None:
        self._run("zzfixture-model-a")
        self.calls.clear()
        self.assertEqual(self._run("zzfixture-model-a"), 0)
        self.assertEqual(self.calls, [])

    def test_a_different_model_re_embeds_everything(self) -> None:
        self._run("zzfixture-model-a")
        self.assertEqual(self._run("zzfixture-model-b"), 3)
        self.assertEqual(set(self._stored().values()), {"zzfixture-model-b"})

    def test_wrong_dimension_raises_and_writes_nothing(self) -> None:
        with self.assertRaises(ValueError):
            embed_esco_skills(
                self.engine,
                embed=lambda _text: [1.0, 2.0],
                model="zzfixture-model-a",
                skill_ids=_IDS,
            )
        self.assertEqual(self._stored(), {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m unittest tests.integration.test_esco_embed -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'core.skills.esco_embed'`

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/skills/esco_embed.py`:

```python
"""Embed every ESCO skill's preferred label into `esco.skill_embedding`.

A derived, rebuildable cache (see migration 0022): one vector per skill,
recorded with the `embedding_model` that produced it. Only skills with
no embedding — or an embedding from a different model — are embedded,
so an interrupted run resumes where it stopped (each batch commits on
its own). Preferred labels only: alt labels are already matched exactly
by the mapper, and embedding them would be ~10x the calls for little gain.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, text

from core.skills.vector import EMBEDDING_DIMENSION, to_pgvector

_SELECT_PENDING = text(
    "SELECT s.skill_id, s.preferred_label FROM esco.skill AS s "
    "LEFT JOIN esco.skill_embedding AS e ON e.skill_id = s.skill_id "
    "WHERE (e.skill_id IS NULL OR e.embedding_model <> :model) "
    "AND (CAST(:skill_ids AS text[]) IS NULL OR s.skill_id = ANY(:skill_ids)) "
    "ORDER BY s.skill_id"
)
_UPSERT = text(
    "INSERT INTO esco.skill_embedding (skill_id, embedding_model, embedding) "
    "VALUES (:skill_id, :model, CAST(:embedding AS vector)) "
    "ON CONFLICT (skill_id) DO UPDATE SET "
    "embedding_model = EXCLUDED.embedding_model, embedding = EXCLUDED.embedding"
)


def embed_esco_skills(
    engine: Engine,
    *,
    embed: Callable[[str], list[float]],
    model: str,
    skill_ids: list[str] | None = None,
    batch_size: int = 200,
) -> int:
    """Embed ESCO skills that lack an embedding for `model`.

    Args:
        engine: The owner-role engine.
        embed: Maps a label to its embedding vector (Ollama in production,
            a fake in tests).
        model: The embedding model name, recorded on every row.
        skill_ids: Restrict the run to these skills; `None` (what the CLI
            passes) covers every skill. Exists so tests never embed a real
            ESCO dataset loaded in the shared dev DB.
        batch_size: Skills per committed transaction.

    Returns:
        The number of embeddings written.

    Raises:
        ValueError: If `embed` returns a vector that is not
            `EMBEDDING_DIMENSION` long (the batch is not written).
    """
    with engine.connect() as conn:
        pending = conn.execute(
            _SELECT_PENDING, {"model": model, "skill_ids": skill_ids}
        ).all()

    written = 0
    for start in range(0, len(pending), batch_size):
        params = []
        for row in pending[start : start + batch_size]:
            vector = embed(row.preferred_label)
            if len(vector) != EMBEDDING_DIMENSION:
                raise ValueError(
                    f"embedding for {row.skill_id} has {len(vector)} dimensions, "
                    f"expected {EMBEDDING_DIMENSION} — is {model!r} the right model?"
                )
            params.append(
                {
                    "skill_id": row.skill_id,
                    "model": model,
                    "embedding": to_pgvector(vector),
                }
            )
        with engine.begin() as conn:
            conn.execute(_UPSERT, params)
        written += len(params)
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/core && python -m unittest tests.integration.test_esco_embed -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Add the `embed-esco` subcommand and its registration test**

Append to `packages/core/tests/test_pipeline_cli_skills.py` (before the `if __name__` block):

```python
class TestSkillSubcommandsAreRegistered(unittest.TestCase):
    def _help_exits_zero(self, command: str) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main([command, "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_embed_esco_is_registered(self) -> None:
        self._help_exits_zero("embed-esco")
```

Run it to see it fail (`SystemExit: 2`): `cd packages/core && python -m unittest tests.test_pipeline_cli_skills -v`

In `apps/pipeline/app/cli.py` add imports `from core.embedding.ollama import embed_text` and `from core.skills.esco_embed import embed_esco_skills`, then above `_EVAL_TASKS`:

```python
def _build_embedder(
    http_client: httpx.Client, settings: Settings
) -> Callable[[str], list[float]]:
    """Build the text-to-vector function used by the skill commands.

    Args:
        http_client: The shared HTTP client for Ollama calls.
        settings: Application settings (Ollama URL and embedding model).

    Returns:
        A function embedding one string via the local Ollama server.
    """

    def embed(text: str) -> list[float]:
        return embed_text(
            text,
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            client=http_client,
        )

    return embed


def _cmd_embed_esco(args: argparse.Namespace) -> int:
    """Run the `embed-esco` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        written = embed_esco_skills(
            engine,
            embed=_build_embedder(http_client, settings),
            model=settings.embedding_model,
        )
        print(f"embed-esco complete: embeddings_written={written}")
        return 0
    finally:
        http_client.close()
```

In `main`, add the parser and dispatch:

```python
    subparsers.add_parser(
        "embed-esco",
        help="Embed every ESCO skill's preferred label (resumable; ~14k Ollama calls)",
    )
```
```python
    if args.command == "embed-esco":
        return _cmd_embed_esco(args)
```

Run: `cd packages/core && python -m unittest tests.test_pipeline_cli_skills -v` — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
black apps packages/core && isort apps packages/core && ruff check apps packages/core
git add packages/core/core/skills/esco_embed.py packages/core/tests/integration/test_esco_embed.py packages/core/tests/test_pipeline_cli_skills.py apps/pipeline/app/cli.py
git commit -m "feat(job_search): ESCO label embedding cache and embed-esco command (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Seed aliases

**Files:**
- Create: `config/skill_aliases.yml`
- Create: `packages/core/core/skills/aliases.py`
- Test: `packages/core/tests/test_skills_aliases.py` (unit), `packages/core/tests/integration/test_skills_aliases_sync.py`

**Interfaces:**
- Consumes: `normalise_skill`, `silver.custom_skill`, `silver.skill_alias`.
- Produces:
  - `SeedSkill(skill_id: str, label: str | None, aliases: tuple[str, ...])` (frozen dataclass)
  - `load_seed_aliases(path: Path | None = None) -> list[SeedSkill]` — raises `ValueError` on a malformed file (custom id without label; alias claimed by two entries; wrong types).
  - `sync_seed_aliases(engine: Engine, path: Path | None = None) -> int` — upserts `custom_skill` rows and `skill_alias` rows (source `seed`, including each entry's own label); never overwrites a `review`-sourced alias; returns the alias rows attempted.

- [ ] **Step 1: Write the seed file**

Create `config/skill_aliases.yml`:

```yaml
# Curated skill aliases for Step 14's skill mapper (silver.skill_alias,
# source 'seed'). Aliases outrank ESCO's own labels, so an entry here can
# correct or extend ESCO. Each entry maps a set of spellings to one skill:
#   - a `custom:<slug>` id (with a `label`) for a tool ESCO lacks, or
#   - an ESCO skill_id (the UUID at the end of its concept URI).
# An entry's own `label` is always an alias too. This file is a starting
# set; day-to-day additions come from the Skill Review page (source
# 'review'), which is never overwritten by a re-sync of this file.
skills:
  - skill_id: "custom:google-cloud-platform"
    label: "Google Cloud Platform"
    aliases: ["GCP", "Google Cloud", "Google Cloud Platform (GCP)"]
  - skill_id: "custom:amazon-web-services"
    label: "Amazon Web Services"
    aliases: ["AWS", "Amazon AWS"]
  - skill_id: "custom:kubernetes"
    label: "Kubernetes"
    aliases: ["K8s"]
  - skill_id: "custom:postgresql"
    label: "PostgreSQL"
    aliases: ["Postgres"]
```

- [ ] **Step 2: Write the failing unit test**

Create `packages/core/tests/test_skills_aliases.py`:

```python
"""Unit tests for loading config/skill_aliases.yml."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.skills.aliases import load_seed_aliases
from core.skills.normalise import normalise_skill


def _write(text: str) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
    handle.write(text)
    handle.close()
    return Path(handle.name)


class TestCommittedSeedFile(unittest.TestCase):
    def test_every_gcp_spelling_points_at_one_custom_skill(self) -> None:
        seeds = {s.skill_id: s for s in load_seed_aliases()}
        gcp = seeds["custom:google-cloud-platform"]
        spellings = {normalise_skill(a) for a in (gcp.label or "", *gcp.aliases)}
        self.assertTrue(
            {"gcp", "google cloud", "google cloud platform"} <= spellings
        )

    def test_no_alias_is_claimed_by_two_entries(self) -> None:
        load_seed_aliases()  # raises ValueError on a conflict


class TestLoadSeedAliases(unittest.TestCase):
    def test_custom_id_without_a_label_is_rejected(self) -> None:
        path = _write("skills:\n  - skill_id: 'custom:x'\n    aliases: ['x']\n")
        with self.assertRaises(ValueError):
            load_seed_aliases(path)

    def test_alias_claimed_by_two_entries_is_rejected(self) -> None:
        path = _write(
            "skills:\n"
            "  - {skill_id: 'custom:a', label: 'A', aliases: ['shared']}\n"
            "  - {skill_id: 'custom:b', label: 'B', aliases: ['Shared']}\n"
        )
        with self.assertRaises(ValueError):
            load_seed_aliases(path)

    def test_esco_id_entry_needs_no_label(self) -> None:
        path = _write("skills:\n  - {skill_id: 'abc-123', aliases: ['thing']}\n")
        seeds = load_seed_aliases(path)
        self.assertEqual(seeds[0].skill_id, "abc-123")
        self.assertIsNone(seeds[0].label)

    def test_non_list_aliases_are_rejected(self) -> None:
        path = _write("skills:\n  - {skill_id: 'abc', aliases: 'nope'}\n")
        with self.assertRaises(ValueError):
            load_seed_aliases(path)


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.test_skills_aliases -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'core.skills.aliases'`

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/skills/aliases.py`:

```python
"""Seed skill aliases: config/skill_aliases.yml -> silver.skill_alias.

The seed file is the committed starting vocabulary for tools ESCO lacks
(and corrections to ESCO). `sync_seed_aliases` upserts it idempotently and
never overwrites an alias a human created on the Skill Review page.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import Engine, text

from core.skills.normalise import normalise_skill

_DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "skill_aliases.yml"

_UPSERT_CUSTOM = text(
    "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
    "VALUES (:skill_id, :label) "
    "ON CONFLICT (skill_id) DO UPDATE SET canonical_label = EXCLUDED.canonical_label"
)
_UPSERT_ALIAS = text(
    "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
    "VALUES (:alias_norm, :skill_id, 'seed') "
    "ON CONFLICT (alias_norm) DO UPDATE SET skill_id = EXCLUDED.skill_id "
    "WHERE silver.skill_alias.source = 'seed'"
)


@dataclass(frozen=True)
class SeedSkill:
    """One entry of the seed file.

    Attributes:
        skill_id: An ESCO skill id or a `custom:<slug>` id.
        label: Canonical label; required for `custom:` ids.
        aliases: Alternative spellings (the label is implicitly one too).
    """

    skill_id: str
    label: str | None
    aliases: tuple[str, ...]


def load_seed_aliases(path: Path | None = None) -> list[SeedSkill]:
    """Load and validate the seed alias file.

    Args:
        path: The YAML file. Defaults to `config/skill_aliases.yml`.

    Returns:
        The entries, in file order.

    Raises:
        ValueError: If an entry is malformed, a `custom:` id has no label,
            or a normalised alias is claimed by two entries.
    """
    data = yaml.safe_load((path or _DEFAULT_PATH).read_text()) or {}
    entries = data.get("skills")
    if not isinstance(entries, list):
        raise ValueError("skill alias file must contain a top-level 'skills' list")

    seeds: list[SeedSkill] = []
    claimed: dict[str, str] = {}
    for entry in entries:
        skill_id = entry.get("skill_id") if isinstance(entry, dict) else None
        if not isinstance(skill_id, str) or not skill_id:
            raise ValueError(f"alias entry needs a string skill_id: {entry!r}")
        label = entry.get("label")
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise ValueError(f"aliases for {skill_id} must be a list of strings")
        if skill_id.startswith("custom:") and not label:
            raise ValueError(f"custom skill {skill_id} needs a label")
        seed = SeedSkill(skill_id, label, tuple(aliases))
        for spelling in _spellings(seed):
            owner = claimed.setdefault(spelling, skill_id)
            if owner != skill_id:
                raise ValueError(
                    f"alias {spelling!r} is claimed by both {owner} and {skill_id}"
                )
        seeds.append(seed)
    return seeds


def _spellings(seed: SeedSkill) -> list[str]:
    """List a seed entry's normalised, non-empty spellings.

    Args:
        seed: The entry.

    Returns:
        The de-duplicated normalised aliases, including the label.
    """
    raw = list(seed.aliases) + ([seed.label] if seed.label else [])
    return sorted({norm for norm in map(normalise_skill, raw) if norm})


def sync_seed_aliases(engine: Engine, path: Path | None = None) -> int:
    """Upsert the seed file into `silver.custom_skill` and `silver.skill_alias`.

    Args:
        engine: The owner-role engine.
        path: The YAML file. Defaults to `config/skill_aliases.yml`.

    Returns:
        The number of alias rows attempted (a `review`-sourced alias with
        the same key is left unchanged but still counted).

    Raises:
        ValueError: If the file is invalid (see `load_seed_aliases`).
    """
    seeds = load_seed_aliases(path)
    attempted = 0
    with engine.begin() as conn:
        for seed in seeds:
            if seed.skill_id.startswith("custom:"):
                conn.execute(
                    _UPSERT_CUSTOM, {"skill_id": seed.skill_id, "label": seed.label}
                )
            for spelling in _spellings(seed):
                conn.execute(
                    _UPSERT_ALIAS, {"alias_norm": spelling, "skill_id": seed.skill_id}
                )
                attempted += 1
    return attempted
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.test_skills_aliases -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Write and run the integration sync test**

Create `packages/core/tests/integration/test_skills_aliases_sync.py`:

```python
"""Integration tests for core.skills.aliases.sync_seed_aliases."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

from core.skills.aliases import sync_seed_aliases
from tests.integration.skills_fixtures import live_owner_engine, purge_fixtures


class TestSyncSeedAliases(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        sfx = uuid.uuid4().hex[:8]
        self.skill_id = f"custom:fixture-{sfx}"
        handle = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        handle.write(
            f"skills:\n  - skill_id: '{self.skill_id}'\n"
            "    label: 'zzfixture Label'\n"
            "    aliases: ['zzfixture alias one', 'zzfixture alias two']\n"
        )
        handle.close()
        self.path = Path(handle.name)

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _aliases(self) -> dict[str, tuple[str, str]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT alias_norm, skill_id, source FROM silver.skill_alias "
                    "WHERE alias_norm LIKE 'zzfixture%'"
                )
            ).all()
        return {r.alias_norm: (r.skill_id, r.source) for r in rows}

    def test_writes_the_custom_skill_and_every_spelling_including_the_label(
        self,
    ) -> None:
        attempted = sync_seed_aliases(self.engine, self.path)
        self.assertEqual(attempted, 3)
        self.assertEqual(
            self._aliases(),
            {
                "zzfixture alias one": (self.skill_id, "seed"),
                "zzfixture alias two": (self.skill_id, "seed"),
                "zzfixture label": (self.skill_id, "seed"),
            },
        )
        with self.engine.connect() as conn:
            label = conn.execute(
                text("SELECT canonical_label FROM silver.custom_skill WHERE skill_id = :s"),
                {"s": self.skill_id},
            ).scalar_one()
        self.assertEqual(label, "zzfixture Label")

    def test_is_idempotent(self) -> None:
        sync_seed_aliases(self.engine, self.path)
        sync_seed_aliases(self.engine, self.path)
        self.assertEqual(len(self._aliases()), 3)

    def test_never_overwrites_a_review_sourced_alias(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
                    "VALUES ('zzfixture alias one', 'custom:fixture-other', 'review')"
                )
            )
        sync_seed_aliases(self.engine, self.path)
        self.assertEqual(
            self._aliases()["zzfixture alias one"], ("custom:fixture-other", "review")
        )


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.integration.test_skills_aliases_sync -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
black packages/core && isort packages/core && ruff check packages/core
git add config/skill_aliases.yml packages/core/core/skills/aliases.py packages/core/tests/test_skills_aliases.py packages/core/tests/integration/test_skills_aliases_sync.py
git commit -m "feat(job_search): seed skill aliases incl. the GCP variants (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: The mapper and `map-skills` command

**Files:**
- Create: `packages/core/core/skills/mapper.py`
- Modify: `apps/pipeline/app/cli.py` (add `_cmd_map_skills`, parser, dispatch)
- Modify: `docs/superpowers/specs/2026-09-19-step14-esco-skill-normalisation-design.md` (deviations 1–3)
- Test: `packages/core/tests/integration/test_skills_mapper.py`, `packages/core/tests/integration/test_skills_map_strings.py`; extend `test_pipeline_cli_skills.py`

**Interfaces:**
- Consumes: `normalise_skill`, `to_pgvector`, `sync_seed_aliases`, `esco.*`, `silver.skill_alias`, `silver.skill_mapping`, `silver.job_skill_raw`.
- Produces (all in `core.skills.mapper`):
  - `EMBEDDING_ACCEPT_COSINE: float = 0.85` — starting value, **not empirically tuned**.
  - `EmbeddingModelMismatch(RuntimeError)`
  - `SkillMatch(skill_id: str | None, method: str, score: float | None = None, candidate_skill_id: str | None = None, candidate_score: float | None = None)` (frozen dataclass; `method` is `alias|label|embedding|none`)
  - `check_embedding_model(conn: Connection, embedding_model: str) -> None`
  - `map_skill(conn: Connection, raw: str, *, embed: Callable[[str], list[float]], embedding_model: str, accept_threshold: float = EMBEDDING_ACCEPT_COSINE) -> SkillMatch` — read-only cascade.
  - `map_strings(engine: Engine, raws: Sequence[str], *, embed, embedding_model, seen_in_cv: bool = False, accept_threshold=...) -> dict[str, str | None]` — maps normalised string → skill id (or None); inserts a `skill_mapping` row for each new string, never overwrites an existing row, sets `seen_in_cv` when asked.
  - `MapSummary(mapped: int, unmapped: int)`; `map_pending(engine, *, embed, embedding_model, raw_norms: list[str] | None = None, accept_threshold=...) -> MapSummary` — maps every `job_skill_raw` string with no mapping row.
  - `remap_unresolved(engine: Engine, *, raw_norms: list[str] | None = None) -> int` — deletes rows with method `embedding`, or method `none` and status `open` (never `rejected`/`resolved`/`dismissed`) so the next `map_pending` re-maps them; returns rows deleted.
  - CLI `pipeline map-skills [--remap-unresolved]`.

- [ ] **Step 1: Write the failing `map_skill` tests**

Create `packages/core/tests/integration/test_skills_mapper.py`:

```python
"""Integration tests for core.skills.mapper.map_skill against live Postgres."""

from __future__ import annotations

import unittest

from sqlalchemy import text

from core.settings import get_settings
from core.skills.aliases import sync_seed_aliases
from core.skills.esco_load import load_esco
from core.skills.mapper import EMBEDDING_ACCEPT_COSINE, map_skill
from core.skills.vector import to_pgvector
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    axis_vector,
    live_owner_engine,
    purge_fixtures,
    sparse_vector,
)

_MODEL = get_settings().embedding_model


def _no_embed(_text: str) -> list[float]:
    raise AssertionError("the embedding stage must not run for this input")


class TestMapSkill(unittest.TestCase):
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
                        "VALUES (:id, :model, CAST(:v AS vector))"
                    ),
                    {"id": skill_id, "model": _MODEL, "v": to_pgvector(axis_vector(axis))},
                )

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _map(self, raw, embed=_no_embed):
        with self.engine.connect() as conn:
            return map_skill(conn, raw, embed=embed, embedding_model=_MODEL)

    def test_esco_label_match_is_case_and_spacing_insensitive(self) -> None:
        match = self._map("  ZZFixture   Cloud Platforms ")
        self.assertEqual((match.skill_id, match.method), ("fixture-cloud", "label"))

    def test_a_preferred_label_match_wins_a_label_tie(self) -> None:
        # "zzfixture cloud computing" is a non-preferred alt label of
        # fixture-cloud; make it the *preferred* label of fixture-python.
        # fixture-cloud sorts first by id, so only is_preferred can make
        # fixture-python win.
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_label (skill_id, label, label_norm, "
                    "is_preferred) VALUES ('fixture-python', 'zzfixture cloud "
                    "computing', 'zzfixture cloud computing', true)"
                )
            )
        self.assertEqual(
            self._map("zzfixture cloud computing").skill_id, "fixture-python"
        )

    def test_equally_preferred_label_ties_pick_the_smallest_skill_id(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_label (skill_id, label, label_norm, "
                    "is_preferred) VALUES ('fixture-python', 'zzfixture cloud "
                    "computing', 'zzfixture cloud computing', false)"
                )
            )
        self.assertEqual(
            self._map("zzfixture cloud computing").skill_id, "fixture-cloud"
        )

    def test_alias_outranks_an_esco_label(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
                    "VALUES ('custom:fixture-alias', 'A')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
                    "VALUES ('zzfixture cloud platforms', 'custom:fixture-alias', 'seed')"
                )
            )
        match = self._map("ZZFixture Cloud Platforms")
        self.assertEqual(
            (match.skill_id, match.method), ("custom:fixture-alias", "alias")
        )

    def test_embedding_match_at_or_above_threshold_is_accepted(self) -> None:
        match = self._map("zzfixture novel phrase", embed=lambda _t: axis_vector(0))
        self.assertEqual((match.skill_id, match.method), ("fixture-cloud", "embedding"))
        self.assertAlmostEqual(match.score, 1.0, places=5)

    def test_embedding_match_below_threshold_is_unmapped_with_a_candidate(self) -> None:
        # cosine to fixture-cloud's axis-0 vector is exactly 0.6
        vector = sparse_vector({0: 0.6, 767: 0.8})
        match = self._map("zzfixture other phrase", embed=lambda _t: vector)
        self.assertLess(0.6, EMBEDDING_ACCEPT_COSINE)
        self.assertEqual((match.skill_id, match.method), (None, "none"))
        self.assertEqual(match.candidate_skill_id, "fixture-cloud")
        self.assertAlmostEqual(match.candidate_score, 0.6, places=5)

    def test_blank_input_is_unmapped_without_embedding(self) -> None:
        match = self._map(" - ")
        self.assertEqual((match.skill_id, match.method), (None, "none"))


class TestGcpAcceptance(unittest.TestCase):
    """PLAN.md Step 14 "Done when": all three spellings resolve to one ID."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()
        sync_seed_aliases(cls.engine)  # idempotent; real seed data by design

    def test_gcp_google_cloud_and_google_cloud_platform_resolve_to_one_id(self) -> None:
        ids = set()
        with self.engine.connect() as conn:
            for raw in ("GCP", "Google Cloud", "Google Cloud Platform"):
                match = map_skill(conn, raw, embed=_no_embed, embedding_model=_MODEL)
                self.assertEqual(match.method, "alias", raw)
                ids.add(match.skill_id)
        self.assertEqual(ids, {"custom:google-cloud-platform"})


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.integration.test_skills_mapper -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'core.skills.mapper'`

- [ ] **Step 2: Write the mapper**

Create `packages/core/core/skills/mapper.py`:

```python
"""The Step 14 skill mapper: raw skill string -> ESCO / custom skill id.

Cascade (deterministic, no LLM):
  1. `silver.skill_alias`   — curated, outranks ESCO (method "alias")
  2. `esco.skill_label`     — preferred/alt/hidden labels (method "label")
  3. pgvector nearest neighbour over `esco.skill_embedding`, accepted at
     or above `EMBEDDING_ACCEPT_COSINE` (method "embedding")
  4. otherwise unmapped -> `review_status = 'open'` for the review list.

`map_skill` is read-only; `map_strings`/`map_pending` persist results in
`silver.skill_mapping`, one row per distinct normalised string shared by
CV and JD skills, and never overwrite an existing row — a human
resolution is never clobbered by a re-run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, text

from core.skills.normalise import normalise_skill
from core.skills.vector import to_pgvector

EMBEDDING_ACCEPT_COSINE = 0.85
"""Minimum cosine similarity for the embedding stage to accept a match.

A starting value, NOT empirically tuned (same stance as Step 11a's
cutoffs). Set it after inspecting real ESCO neighbours: hand-check about
100 embedding matches on the Skill Review page and raise it if too many
are wrong, lower it if the review list is swamped with obvious matches.
After changing it, run `pipeline map-skills --remap-unresolved`."""


class EmbeddingModelMismatch(RuntimeError):
    """Raised when `esco.skill_embedding` holds a different model's vectors."""


@dataclass(frozen=True)
class SkillMatch:
    """The outcome of mapping one raw skill string.

    Attributes:
        skill_id: The ESCO id or `custom:<slug>`, or None if unmapped.
        method: "alias", "label", "embedding" or "none".
        score: Cosine similarity, for the embedding method only.
        candidate_skill_id: For an unmapped string, the nearest ESCO skill
            below the threshold (a suggestion for the review page).
        candidate_score: The candidate's cosine similarity.
    """

    skill_id: str | None
    method: str
    score: float | None = None
    candidate_skill_id: str | None = None
    candidate_score: float | None = None


@dataclass(frozen=True)
class MapSummary:
    """Counts from one `map_pending` run.

    Attributes:
        mapped: Strings that resolved to a skill.
        unmapped: Strings sent to the review list.
    """

    mapped: int
    unmapped: int


_ALIAS = text("SELECT skill_id FROM silver.skill_alias WHERE alias_norm = :n")
_LABEL = text(
    "SELECT skill_id FROM esco.skill_label WHERE label_norm = :n "
    "ORDER BY is_preferred DESC, skill_id LIMIT 1"
)
_NEAREST = text(
    "SELECT skill_id, 1 - (embedding <=> CAST(:q AS vector)) AS score "
    "FROM esco.skill_embedding WHERE embedding_model = :model "
    "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 1"
)
_OTHER_MODEL = text(
    "SELECT embedding_model FROM esco.skill_embedding "
    "WHERE embedding_model <> :model LIMIT 1"
)
_INSERT_MAPPING = text(
    "INSERT INTO silver.skill_mapping (raw_norm, raw_example, skill_id, method, "
    "score, candidate_skill_id, candidate_score, review_status, seen_in_cv) "
    "VALUES (:raw_norm, :raw_example, :skill_id, :method, :score, "
    ":candidate_skill_id, :candidate_score, :review_status, :seen_in_cv) "
    "ON CONFLICT (raw_norm) DO NOTHING"
)


def check_embedding_model(conn: Connection, embedding_model: str) -> None:
    """Refuse to map if the stored ESCO embeddings use a different model.

    Args:
        conn: An open connection.
        embedding_model: The model the query embeddings will come from.

    Raises:
        EmbeddingModelMismatch: If any stored embedding was made by another
            model (DECISIONS.md §2.8: mixed models corrupt similarity
            silently). An empty embedding table is allowed — the alias and
            label stages still work.
    """
    other = conn.execute(_OTHER_MODEL, {"model": embedding_model}).scalar_one_or_none()
    if other is not None:
        raise EmbeddingModelMismatch(
            f"esco.skill_embedding holds {other!r} vectors but the configured "
            f"model is {embedding_model!r} — run `pipeline embed-esco` to re-embed"
        )


def map_skill(
    conn: Connection,
    raw: str,
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    accept_threshold: float = EMBEDDING_ACCEPT_COSINE,
) -> SkillMatch:
    """Resolve one raw skill string through the cascade (read-only).

    Args:
        conn: An open connection.
        raw: The skill string as written in a CV or JD.
        embed: Maps a string to its embedding (only called if the alias
            and label stages miss).
        embedding_model: The model `embed` uses; only embeddings from this
            model are searched.
        accept_threshold: Minimum cosine similarity to accept an embedding
            match.

    Returns:
        The `SkillMatch`; `method == "none"` means unmapped.
    """
    raw_norm = normalise_skill(raw)
    if not raw_norm:
        return SkillMatch(None, "none")

    alias = conn.execute(_ALIAS, {"n": raw_norm}).scalar_one_or_none()
    if alias is not None:
        return SkillMatch(alias, "alias")
    label = conn.execute(_LABEL, {"n": raw_norm}).scalar_one_or_none()
    if label is not None:
        return SkillMatch(label, "label")

    nearest = conn.execute(
        _NEAREST, {"q": to_pgvector(embed(raw_norm)), "model": embedding_model}
    ).one_or_none()
    if nearest is None:
        return SkillMatch(None, "none")
    score = float(nearest.score)
    if score >= accept_threshold:
        return SkillMatch(nearest.skill_id, "embedding", score=score)
    return SkillMatch(
        None, "none", candidate_skill_id=nearest.skill_id, candidate_score=score
    )


def map_strings(
    engine: Engine,
    raws: Sequence[str],
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    seen_in_cv: bool = False,
    accept_threshold: float = EMBEDDING_ACCEPT_COSINE,
) -> dict[str, str | None]:
    """Map raw strings and persist a `silver.skill_mapping` row for new ones.

    Args:
        engine: The owner-role engine.
        raws: Raw skill strings; duplicates (after normalisation) and
            strings that normalise to empty are ignored.
        embed: Maps a string to its embedding.
        embedding_model: The embedding model in use.
        seen_in_cv: True when the strings come from a CV — flags the
            mapping rows (new or existing) as seen in a CV.
        accept_threshold: See `map_skill`.

    Returns:
        Map of normalised string to its skill id (None if unmapped),
        reflecting existing rows as well as newly mapped ones.

    Raises:
        EmbeddingModelMismatch: If the stored ESCO embeddings are from a
            different model.
    """
    first_spelling: dict[str, str] = {}
    for raw in raws:
        norm = normalise_skill(raw)
        if norm:
            first_spelling.setdefault(norm, raw)
    if not first_spelling:
        return {}

    result: dict[str, str | None] = {}
    with engine.begin() as conn:
        check_embedding_model(conn, embedding_model)
        existing = {
            row.raw_norm: row.skill_id
            for row in conn.execute(
                text(
                    "SELECT raw_norm, skill_id FROM silver.skill_mapping "
                    "WHERE raw_norm = ANY(:norms)"
                ),
                {"norms": sorted(first_spelling)},
            )
        }
        for norm, raw in first_spelling.items():
            if norm in existing:
                result[norm] = existing[norm]
                if seen_in_cv:
                    conn.execute(
                        text(
                            "UPDATE silver.skill_mapping SET seen_in_cv = true "
                            "WHERE raw_norm = :n"
                        ),
                        {"n": norm},
                    )
                continue
            match = map_skill(
                conn,
                raw,
                embed=embed,
                embedding_model=embedding_model,
                accept_threshold=accept_threshold,
            )
            conn.execute(
                _INSERT_MAPPING,
                {
                    "raw_norm": norm,
                    "raw_example": raw.strip(),
                    "skill_id": match.skill_id,
                    "method": match.method,
                    "score": match.score,
                    "candidate_skill_id": match.candidate_skill_id,
                    "candidate_score": match.candidate_score,
                    "review_status": "open" if match.skill_id is None else None,
                    "seen_in_cv": seen_in_cv,
                },
            )
            result[norm] = match.skill_id
    return result


_SELECT_UNMAPPED = text(
    "SELECT r.raw_norm, MIN(r.raw_skill) AS raw_example "
    "FROM silver.job_skill_raw AS r "
    "LEFT JOIN silver.skill_mapping AS m ON m.raw_norm = r.raw_norm "
    "WHERE m.raw_norm IS NULL "
    "AND (CAST(:raw_norms AS text[]) IS NULL OR r.raw_norm = ANY(:raw_norms)) "
    "GROUP BY r.raw_norm ORDER BY r.raw_norm"
)


def map_pending(
    engine: Engine,
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    raw_norms: list[str] | None = None,
    accept_threshold: float = EMBEDDING_ACCEPT_COSINE,
) -> MapSummary:
    """Map every extracted JD skill string that has no mapping row yet.

    Args:
        engine: The owner-role engine.
        embed: Maps a string to its embedding.
        embedding_model: The embedding model in use.
        raw_norms: Restrict to these normalised strings; `None` (what the
            CLI passes) covers everything. Exists so tests never touch
            unrelated rows in the shared dev DB.
        accept_threshold: See `map_skill`.

    Returns:
        How many strings mapped vs. went to the review list.
    """
    with engine.connect() as conn:
        rows = conn.execute(_SELECT_UNMAPPED, {"raw_norms": raw_norms}).all()
    result = map_strings(
        engine,
        [row.raw_example for row in rows],
        embed=embed,
        embedding_model=embedding_model,
        accept_threshold=accept_threshold,
    )
    mapped = sum(1 for skill_id in result.values() if skill_id is not None)
    return MapSummary(mapped=mapped, unmapped=len(result) - mapped)


def remap_unresolved(engine: Engine, *, raw_norms: list[str] | None = None) -> int:
    """Delete auto-made mappings so the next `map_pending` re-maps them.

    Deletes rows with method `embedding`, or method `none` and status
    `open`. Never deletes `rejected`, `resolved` or `dismissed` rows — those
    carry a human decision. CV-only strings (not in `job_skill_raw`) are
    re-mapped by re-running `map-cv-skills`.

    Args:
        engine: The owner-role engine.
        raw_norms: Restrict to these strings; `None` covers all.

    Returns:
        The number of rows deleted.
    """
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "DELETE FROM silver.skill_mapping "
                "WHERE (method = 'embedding' "
                "OR (method = 'none' AND review_status = 'open')) "
                "AND (CAST(:raw_norms AS text[]) IS NULL "
                "OR raw_norm = ANY(:raw_norms))"
            ),
            {"raw_norms": raw_norms},
        )
    return result.rowcount
```

- [ ] **Step 3: Run the `map_skill` tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.integration.test_skills_mapper -v`
Expected: PASS (8 tests)

- [ ] **Step 4: Write and run the `map_strings` / `map_pending` / `remap_unresolved` tests**

Create `packages/core/tests/integration/test_skills_map_strings.py`:

```python
"""Integration tests for map_strings, map_pending and remap_unresolved."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.settings import get_settings
from core.skills.esco_load import load_esco
from core.skills.mapper import (
    EmbeddingModelMismatch,
    map_pending,
    map_strings,
    remap_unresolved,
)
from core.skills.vector import to_pgvector
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    axis_vector,
    insert_job_skills,
    insert_mapping,
    live_owner_engine,
    purge_fixtures,
    sparse_vector,
)

_MODEL = get_settings().embedding_model


def _far_embed(_text: str) -> list[float]:
    """An embedding on an axis no fixture uses — never accepted."""
    return sparse_vector({767: 1.0})


class TestMapStrings(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO esco.skill_embedding "
                    "(skill_id, embedding_model, embedding) "
                    "VALUES ('fixture-cloud', :m, CAST(:v AS vector))"
                ),
                {"m": _MODEL, "v": to_pgvector(axis_vector(0))},
            )

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _row(self, raw_norm: str):
        with self.engine.connect() as conn:
            return conn.execute(
                text("SELECT * FROM silver.skill_mapping WHERE raw_norm = :n"),
                {"n": raw_norm},
            ).one_or_none()

    def test_writes_one_row_per_distinct_string_and_returns_the_skill_ids(self) -> None:
        result = map_strings(
            self.engine,
            ["ZZFixture Cloud Platforms", "zzfixture  cloud platforms", "zzfixture nope"],
            embed=_far_embed,
            embedding_model=_MODEL,
        )
        self.assertEqual(
            result, {"zzfixture cloud platforms": "fixture-cloud", "zzfixture nope": None}
        )
        mapped = self._row("zzfixture cloud platforms")
        self.assertEqual((mapped.method, mapped.review_status), ("label", None))
        unmapped = self._row("zzfixture nope")
        self.assertEqual((unmapped.method, unmapped.review_status), ("none", "open"))

    def test_never_overwrites_an_existing_mapping(self) -> None:
        with self.engine.begin() as conn:
            insert_mapping(
                conn,
                "zzfixture resolved",
                skill_id="custom:fixture-x",
                method="alias",
                review_status="resolved",
            )
        result = map_strings(
            self.engine, ["zzfixture resolved"], embed=_far_embed, embedding_model=_MODEL
        )
        self.assertEqual(result, {"zzfixture resolved": "custom:fixture-x"})
        self.assertEqual(self._row("zzfixture resolved").review_status, "resolved")

    def test_flags_an_existing_row_as_seen_in_cv(self) -> None:
        with self.engine.begin() as conn:
            insert_mapping(conn, "zzfixture cvskill")
        map_strings(
            self.engine,
            ["zzfixture cvskill"],
            embed=_far_embed,
            embedding_model=_MODEL,
            seen_in_cv=True,
        )
        self.assertTrue(self._row("zzfixture cvskill").seen_in_cv)

    def test_strings_that_normalise_to_nothing_are_skipped(self) -> None:
        self.assertEqual(
            map_strings(self.engine, ["  - "], embed=_far_embed, embedding_model=_MODEL),
            {},
        )

    def test_refuses_when_stored_embeddings_use_a_different_model(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE esco.skill_embedding SET embedding_model = 'zzfixture-old' "
                    "WHERE skill_id = 'fixture-cloud'"
                )
            )
        with self.assertRaises(EmbeddingModelMismatch):
            map_strings(
                self.engine, ["zzfixture x"], embed=_far_embed, embedding_model=_MODEL
            )


class TestMapPendingAndRemap(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def test_map_pending_maps_only_scoped_unmapped_job_skills(self) -> None:
        with self.engine.begin() as conn:
            insert_job_skills(
                conn,
                self.job,
                "local.v1",
                [
                    ("ZZFixture Cloud Platforms", "zzfixture cloud platforms", "must_have"),
                    ("zzfixture mystery", "zzfixture mystery", "nice_to_have"),
                ],
            )
        summary = map_pending(
            self.engine,
            embed=_far_embed,
            embedding_model=_MODEL,
            raw_norms=["zzfixture cloud platforms", "zzfixture mystery"],
        )
        self.assertEqual((summary.mapped, summary.unmapped), (1, 1))
        again = map_pending(
            self.engine,
            embed=_far_embed,
            embedding_model=_MODEL,
            raw_norms=["zzfixture cloud platforms", "zzfixture mystery"],
        )
        self.assertEqual((again.mapped, again.unmapped), (0, 0))

    def test_remap_unresolved_keeps_human_decisions(self) -> None:
        norms = {
            "embedding": "zzfixture r embedding",
            "open": "zzfixture r open",
            "rejected": "zzfixture r rejected",
            "resolved": "zzfixture r resolved",
            "dismissed": "zzfixture r dismissed",
        }
        with self.engine.begin() as conn:
            insert_mapping(
                conn, norms["embedding"], skill_id="fixture-cloud",
                method="embedding", score=0.9, review_status=None,
            )
            insert_mapping(conn, norms["open"])
            insert_mapping(conn, norms["rejected"], review_status="rejected")
            insert_mapping(
                conn, norms["resolved"], skill_id="fixture-cloud",
                method="alias", review_status="resolved",
            )
            insert_mapping(conn, norms["dismissed"], review_status="dismissed")
        deleted = remap_unresolved(self.engine, raw_norms=list(norms.values()))
        self.assertEqual(deleted, 2)
        with self.engine.connect() as conn:
            left = {
                r.raw_norm
                for r in conn.execute(
                    text(
                        "SELECT raw_norm FROM silver.skill_mapping "
                        "WHERE raw_norm LIKE 'zzfixture r %'"
                    )
                )
            }
        self.assertEqual(
            left,
            {norms["rejected"], norms["resolved"], norms["dismissed"]},
        )


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.integration.test_skills_map_strings -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Add the `map-skills` subcommand**

Extend `TestSkillSubcommandsAreRegistered` in `packages/core/tests/test_pipeline_cli_skills.py`:

```python
    def test_map_skills_is_registered(self) -> None:
        self._help_exits_zero("map-skills")
```

Run to see it fail, then in `apps/pipeline/app/cli.py` add imports `from core.skills.aliases import sync_seed_aliases` and `from core.skills.mapper import EmbeddingModelMismatch, map_pending, remap_unresolved`, plus above `_EVAL_TASKS`:

```python
def _cmd_map_skills(args: argparse.Namespace) -> int:
    """Run the `map-skills` subcommand.

    Args:
        args: Parsed CLI arguments — `remap_unresolved`.

    Returns:
        0 on success, 1 if the stored ESCO embeddings are from a different
        model than the configured one.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        synced = sync_seed_aliases(engine)
        if args.remap_unresolved:
            cleared = remap_unresolved(engine)
            print(f"map-skills: cleared {cleared} auto-made mappings for re-mapping")
        summary = map_pending(
            engine,
            embed=_build_embedder(http_client, settings),
            embedding_model=settings.embedding_model,
        )
    except EmbeddingModelMismatch as exc:
        print(f"map-skills: {exc}")
        return 1
    finally:
        http_client.close()
    print(
        f"map-skills complete: seed_aliases={synced} "
        f"mapped={summary.mapped} unmapped={summary.unmapped}"
    )
    return 0
```

In `main`:

```python
    map_skills_parser = subparsers.add_parser(
        "map-skills",
        help="Sync seed aliases, then map every extracted JD skill string",
    )
    map_skills_parser.add_argument(
        "--remap-unresolved",
        action="store_true",
        help="First clear auto-made (embedding / open) mappings so they re-map",
    )
```
```python
    if args.command == "map-skills":
        return _cmd_map_skills(args)
```

Run: `cd packages/core && python -m unittest tests.test_pipeline_cli_skills -v` — Expected: PASS.

- [ ] **Step 6: Update the spec for the mapper's file, signature and tie-break**

In `docs/superpowers/specs/2026-09-19-step14-esco-skill-normalisation-design.md`:
1. Change the heading ``## The mapper — `core/skills/normalise.py` `` to ``## The mapper — `core/skills/mapper.py` `` and add one sentence after the cascade list: ``The string normaliser, `normalise_skill`, lives separately in `core/skills/normalise.py`.``
2. Change ``` `map_skill(engine, raw, *, embed) -> SkillMatch` ``` to ``` `map_skill(conn, raw, *, embed, embedding_model) -> SkillMatch` ```.
3. Replace ``pick the one with the`` / ``   lexicographically smallest `skill_id` and log it; ambiguity is`` / ``   resolved by an alias entry.`` with ``prefer a skill whose *preferred* label is the match, then the`` / ``   lexicographically smallest `skill_id`; ambiguity is resolved by an`` / ``   alias entry.``
4. Replace ``` `embedding` or `none` and `review_status` not `resolved`/`dismissed`, then ``` with ``` `embedding`, or `none` with `review_status = 'open'` (never `rejected`, `resolved` or `dismissed`), then ```.

- [ ] **Step 7: Commit**

```bash
black apps packages/core && isort apps packages/core && ruff check apps packages/core
git add packages/core/core/skills/mapper.py packages/core/tests/integration/test_skills_mapper.py packages/core/tests/integration/test_skills_map_strings.py packages/core/tests/test_pipeline_cli_skills.py apps/pipeline/app/cli.py docs/superpowers/specs
git commit -m "feat(job_search): skill mapper cascade and map-skills command (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: JD skill extraction (shared JSON parser, prompt, extractor)

**Files:**
- Create: `packages/core/core/llm/json_response.py`
- Modify: `packages/core/core/cv/extract.py` (use the shared parser)
- Create: `prompts/skill_extraction/local.v1.md`
- Modify: `config/llm_tasks.yml`
- Create: `packages/core/core/skills/jd_extract.py`
- Create: `packages/core/tests/skills_fakes.py`
- Test: `packages/core/tests/test_llm_json_response.py`, `packages/core/tests/test_skills_jd_extract.py`

**Interfaces:**
- Consumes: `normalise_skill`, `core.llm.gateway.complete`, `core.llm.prompts.load_prompt`, `core.text.readable_description`.
- Produces:
  - `core.llm.json_response.parse_json_response(text: str) -> dict[str, object]` (raises `json.JSONDecodeError`).
  - `core.skills.jd_extract`: `CURRENT_PROMPT_VERSION: str = "local.v1"`, `DEFAULT_MAX_CHUNK_CHARS: int = 6000`, `ExtractedSkill(skill: str, requirement_level: Literal["must_have","nice_to_have"])` (pydantic), `JdExtraction(skills: list[ExtractedSkill], prompt_version: str, model: str)` (frozen dataclass), `split_into_chunks(text: str, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> list[str]`, `merge_skills(skills: Iterable[ExtractedSkill]) -> list[ExtractedSkill]`, `extract_jd_skills(description: str, *, adapters: dict[str, LLMAdapter], provider: str | None = None, model: str | None = None, prompt_family: str | None = None, max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> JdExtraction` (raises `ValueError` on an unparseable response).
  - Test helper `tests.skills_fakes.FakeAdapter(*responses: str)` — an `LLMAdapter` returning the given texts in order (repeating the last), recording `.calls: list[tuple[str, str]]` of `(model, prompt)`.

- [ ] **Step 1: Write the failing test for the shared JSON parser**

Create `packages/core/tests/test_llm_json_response.py`:

```python
"""Unit tests for core.llm.json_response."""

from __future__ import annotations

import json
import unittest

from core.llm.json_response import parse_json_response


class TestParseJsonResponse(unittest.TestCase):
    def test_parses_a_bare_object(self) -> None:
        self.assertEqual(parse_json_response('{"a": 1}'), {"a": 1})

    def test_parses_a_fenced_block(self) -> None:
        self.assertEqual(parse_json_response('```json\n{"a": 1}\n```'), {"a": 1})

    def test_parses_a_fence_preceded_by_prose(self) -> None:
        text = 'Sure, here you go:\n```json\n{"a": 1}\n```'
        self.assertEqual(parse_json_response(text), {"a": 1})

    def test_parses_an_object_embedded_in_prose(self) -> None:
        self.assertEqual(
            parse_json_response('Here is the result {"a": 1} hope it helps'), {"a": 1}
        )

    def test_raises_when_no_candidate_parses(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            parse_json_response("no json here")


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.test_llm_json_response -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'core.llm.json_response'`

- [ ] **Step 2: Move the parser into `core/llm/json_response.py`**

Create `packages/core/core/llm/json_response.py`:

```python
"""Tolerant JSON parsing for LLM responses, shared by the extraction tasks.

Local models routinely don't return bare JSON despite being asked to
(observed from llama3.1:8b): a ```json fence, a fence preceded by prose,
or an object embedded in prose. Moved here from core.cv.extract so the CV
and JD-skill extractors share one implementation.
"""

from __future__ import annotations

import json
import re

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def parse_json_response(text: str) -> dict[str, object]:
    """Parse an LLM response into a JSON dict, tolerating common wrapping.

    Tries, in order: the text as-is; the first fenced code block; the
    substring from the first "{" to the last "}". Each candidate is a plain
    `json.loads` attempt — a candidate that parses but isn't the right shape
    still fails the caller's schema validation, so this never turns a
    malformed response into a false success.

    Args:
        text: The raw response text, already `.strip()`-ped.

    Returns:
        The parsed JSON value from the first candidate that parses.

    Raises:
        json.JSONDecodeError: If no candidate parses as JSON.
    """
    candidates = [text]
    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidates.append(text[brace_start : brace_end + 1])

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    assert last_error is not None  # `candidates` always has >= 1 entry
    raise last_error
```

In `packages/core/core/cv/extract.py`: delete `_CODE_FENCE_RE` and the whole `_parse_json_response` function (from `_CODE_FENCE_RE = re.compile(` through `raise last_error`), add `from core.llm.json_response import parse_json_response` to the imports, and change the call `parsed = _parse_json_response(response_text)` to `parsed = parse_json_response(response_text)`. (`re` and `json` are still used elsewhere in that file.)

- [ ] **Step 3: Verify the parser and the CV extractor still pass**

Run: `cd packages/core && python -m unittest tests.test_llm_json_response tests.test_cv_extract tests.test_evals_runner_cv_extraction -v`
Expected: PASS

- [ ] **Step 4: Commit the refactor**

```bash
black packages/core && isort packages/core && ruff check packages/core
git add packages/core/core/llm/json_response.py packages/core/core/cv/extract.py packages/core/tests/test_llm_json_response.py
git commit -m "refactor(job_search): share the tolerant LLM JSON parser (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Add the prompt and the task routing**

Create `prompts/skill_extraction/local.v1.md` (`{{`/`}}` are literal braces — the file is passed through `str.format`; only `{description}` is a placeholder):

```
Extract every skill, technology, tool or method a candidate is expected to have from the job description below, and mark each as required or optional.

Respond with ONLY a JSON object of exactly this shape:

{{
  "skills": [
    {{"skill": "<skill name, e.g. Python>", "requirement_level": "must_have"}},
    {{"skill": "<skill name, e.g. Terraform>", "requirement_level": "nice_to_have"}}
  ]
}}

Rules:
- "requirement_level" is "nice_to_have" ONLY when the text explicitly hedges the skill: "nice to have", "bonus", "preferred", "a plus", "desirable", "advantageous", "would be an asset", "ideally", or the skill sits under a heading such as "Nice to have" or "Bonus points".
- Every other skill that is required, expected, used in the role or its responsibilities, or listed under requirements is "must_have" — including skills stated with no hedging language at all.
- Name each skill once, using its shortest common name (e.g. "Kubernetes", not "experience with Kubernetes clusters"). Do not put years of experience, seniority words or level adjectives in the name.
- Include specific technologies and tools (e.g. "Terraform", "PostgreSQL") and named methods or domains (e.g. "data modelling", "CI/CD"). Do not include soft skills, benefits, company values or job titles.
- If the text names no skills, return {{"skills": []}}.
- Do not invent skills the text does not mention.

Job description:
{description}
```

Append to `config/llm_tasks.yml` under `tasks:` (after `cv_extraction`):

```yaml
  skill_extraction:
    provider: ollama
    model: llama3.1:8b
    prompt_family: local
    eval_metric: field_f1
    eval_regression_threshold: 0.05
```

- [ ] **Step 6: Write the fake adapter and the failing extractor tests**

Create `packages/core/tests/skills_fakes.py`:

```python
"""Test doubles shared by the Step 14 tests."""

from __future__ import annotations

from core.llm.types import LLMResponse


class FakeAdapter:
    """An `LLMAdapter` that returns canned response texts.

    Attributes:
        calls: `(model, prompt)` for every `complete` call, in order.
    """

    def __init__(self, *responses: str) -> None:
        """Initialise the fake.

        Args:
            *responses: Texts to return, one per call in order; the last
                one repeats once the list is exhausted.
        """
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Record the call and return the next canned response.

        Args:
            model: The model identifier.
            prompt: The prompt text.
            temperature: Unused.
            seed: Unused.

        Returns:
            The next canned `LLMResponse`.
        """
        self.calls.append((model, prompt))
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return LLMResponse(
            text=self._responses[index],
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )
```

Create `packages/core/tests/test_skills_jd_extract.py`:

```python
"""Unit tests for core.skills.jd_extract (fake adapter, no network)."""

from __future__ import annotations

import json
import unittest

from core.skills.jd_extract import (
    CURRENT_PROMPT_VERSION,
    ExtractedSkill,
    extract_jd_skills,
    merge_skills,
    split_into_chunks,
)
from tests.skills_fakes import FakeAdapter


def _reply(*skills: tuple[str, str]) -> str:
    return json.dumps(
        {"skills": [{"skill": s, "requirement_level": lvl} for s, lvl in skills]}
    )


def _extract(adapter: FakeAdapter, description: str, **kwargs):
    return extract_jd_skills(
        description,
        adapters={"ollama": adapter},
        provider="ollama",
        model="test-model",
        **kwargs,
    )


class TestExtractJdSkills(unittest.TestCase):
    def test_returns_skills_levels_and_provenance(self) -> None:
        adapter = FakeAdapter(_reply(("Python", "must_have"), ("dbt", "nice_to_have")))
        result = _extract(adapter, "Python required. dbt is a plus.")
        self.assertEqual(
            [(s.skill, s.requirement_level) for s in result.skills],
            [("Python", "must_have"), ("dbt", "nice_to_have")],
        )
        self.assertEqual(result.prompt_version, CURRENT_PROMPT_VERSION)
        self.assertEqual(result.prompt_version, "local.v1")
        self.assertEqual(result.model, "test-model")

    def test_tolerates_a_fenced_response(self) -> None:
        adapter = FakeAdapter("```json\n" + _reply(("SQL", "must_have")) + "\n```")
        self.assertEqual(_extract(adapter, "SQL").skills[0].skill, "SQL")

    def test_level_variants_are_coerced_and_unknown_defaults_to_must_have(self) -> None:
        adapter = FakeAdapter(
            _reply(("A", "preferred"), ("B", "Nice-to-have"), ("C", "required"), ("D", "???"))
        )
        levels = {s.skill: s.requirement_level for s in _extract(adapter, "x").skills}
        self.assertEqual(
            levels,
            {"A": "nice_to_have", "B": "nice_to_have", "C": "must_have", "D": "must_have"},
        )

    def test_html_escaped_descriptions_are_cleaned_before_prompting(self) -> None:
        adapter = FakeAdapter(_reply())
        _extract(adapter, "&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;")
        prompt = adapter.calls[0][1]
        self.assertIn("Python", prompt)
        self.assertNotIn("<li>", prompt)

    def test_an_empty_description_makes_no_llm_call(self) -> None:
        adapter = FakeAdapter(_reply())
        result = _extract(adapter, "   ")
        self.assertEqual(result.skills, [])
        self.assertEqual(adapter.calls, [])

    def test_a_malformed_response_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _extract(FakeAdapter("I could not find any skills."), "Some job text")

    def test_long_descriptions_are_chunked_without_dropping_text(self) -> None:
        paragraphs = [f"Paragraph {i} " + "x" * 30 for i in range(3)]
        adapter = FakeAdapter(
            _reply(("Python", "must_have")),
            _reply(("SQL", "must_have")),
            _reply(("Go", "nice_to_have")),
        )
        result = _extract(adapter, "\n\n".join(paragraphs), max_chunk_chars=50)
        self.assertEqual(len(adapter.calls), 3)
        all_prompts = " ".join(prompt for _model, prompt in adapter.calls)
        for paragraph in paragraphs:
            self.assertIn(paragraph, all_prompts)
        self.assertEqual({s.skill for s in result.skills}, {"Python", "SQL", "Go"})

    def test_must_have_wins_when_chunks_disagree(self) -> None:
        adapter = FakeAdapter(
            _reply(("Python", "nice_to_have")), _reply(("python", "must_have"))
        )
        result = _extract(adapter, "aaaa\n\nbbbb", max_chunk_chars=5)
        self.assertEqual(
            [(s.skill, s.requirement_level) for s in result.skills],
            [("Python", "must_have")],
        )


class TestSplitIntoChunks(unittest.TestCase):
    def test_packs_paragraphs_up_to_the_limit(self) -> None:
        self.assertEqual(split_into_chunks("aa\n\nbb\n\ncc", max_chars=6), ["aa\n\nbb", "cc"])

    def test_splits_a_paragraph_longer_than_the_limit(self) -> None:
        chunks = split_into_chunks("x" * 25, max_chars=10)
        self.assertEqual(chunks, ["x" * 10, "x" * 10, "x" * 5])

    def test_blank_text_yields_no_chunks(self) -> None:
        self.assertEqual(split_into_chunks(" \n\n "), [])


class TestMergeSkills(unittest.TestCase):
    def test_dedupes_case_insensitively_keeping_the_first_spelling(self) -> None:
        merged = merge_skills(
            [
                ExtractedSkill(skill="Kubernetes", requirement_level="must_have"),
                ExtractedSkill(skill="kubernetes", requirement_level="must_have"),
            ]
        )
        self.assertEqual([m.skill for m in merged], ["Kubernetes"])

    def test_drops_names_that_normalise_to_nothing(self) -> None:
        merged = merge_skills([ExtractedSkill(skill=" - ", requirement_level="must_have")])
        self.assertEqual(merged, [])


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.test_skills_jd_extract -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'core.skills.jd_extract'`

- [ ] **Step 7: Write the extractor**

Create `packages/core/core/skills/jd_extract.py`:

```python
"""JD skill extraction (PLAN.md Step 14): job description -> skills, each
marked must-have or nice-to-have, via one local-LLM call per chunk.

Task `skill_extraction` is routed to Ollama only (DECISIONS.md §1: skill
extraction never migrates to a hosted provider). Descriptions longer than
`max_chunk_chars` are split on paragraph boundaries and the per-chunk
results merged — never silently truncated, because requirements are often
at the end of a posting. Known limitation: a "Nice to have" heading and
its bullets can land in different chunks, in which case the bullets read
as required; the golden set measures how often that costs accuracy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, field_validator

from core.llm.gateway import complete
from core.llm.json_response import parse_json_response
from core.llm.prompts import load_prompt
from core.llm.types import LLMAdapter
from core.skills.normalise import normalise_skill
from core.text import readable_description

_PROMPT_FAMILY = "local"
_PROMPT_VERSION_NUMBER = 1
CURRENT_PROMPT_VERSION = f"{_PROMPT_FAMILY}.v{_PROMPT_VERSION_NUMBER}"

DEFAULT_MAX_CHUNK_CHARS = 6000
"""~1.5k tokens of description per call — well inside llama3.1:8b's context,
leaving room for the prompt and the JSON answer."""

_NICE_TO_HAVE_VARIANTS = {"nice_to_have", "preferred", "optional", "bonus", "desirable"}


class ExtractedSkill(BaseModel):
    """One skill the model found in a job description.

    Attributes:
        skill: The skill name as the model wrote it.
        requirement_level: "must_have" or "nice_to_have". Any unrecognised
            value is coerced to "must_have" (the prompt's documented default).
    """

    skill: str
    requirement_level: Literal["must_have", "nice_to_have"] = "must_have"

    @field_validator("requirement_level", mode="before")
    @classmethod
    def _coerce_level(cls, value: object) -> str:
        """Map common model spellings onto the two allowed levels.

        Args:
            value: The raw value from the model's JSON.

        Returns:
            "nice_to_have" for a recognised hedging variant, else "must_have".
        """
        key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        return "nice_to_have" if key in _NICE_TO_HAVE_VARIANTS else "must_have"


class _Response(BaseModel):
    """The model's JSON answer for one chunk."""

    skills: list[ExtractedSkill] = []


@dataclass(frozen=True)
class JdExtraction:
    """The merged result of extracting one job description.

    Attributes:
        skills: De-duplicated skills across all chunks.
        prompt_version: The prompt version used, e.g. "local.v1".
        model: The model identifier reported by the last call ("" if no
            call was made because the description was empty).
    """

    skills: list[ExtractedSkill]
    prompt_version: str
    model: str


def split_into_chunks(text: str, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most `max_chars`, on paragraph boundaries.

    Args:
        text: The description text.
        max_chars: Maximum characters per chunk.

    Returns:
        The chunks in order. A single paragraph longer than `max_chars` is
        hard-split at character boundaries (rare for real postings).
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [
            paragraph[start : start + max_chars]
            for start in range(0, len(paragraph), max_chars)
        ]
        for piece in pieces:
            if current and len(current) + 2 + len(piece) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def merge_skills(skills: Iterable[ExtractedSkill]) -> list[ExtractedSkill]:
    """Collapse duplicate skills, keeping "must_have" if any mention is.

    Args:
        skills: Skills from one or more chunks.

    Returns:
        One entry per normalised name, in first-seen order and spelling.
        Names that normalise to nothing are dropped.
    """
    merged: dict[str, ExtractedSkill] = {}
    for item in skills:
        key = normalise_skill(item.skill)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
        elif item.requirement_level == "must_have":
            merged[key] = existing.model_copy(update={"requirement_level": "must_have"})
    return list(merged.values())


def extract_jd_skills(
    description: str,
    *,
    adapters: dict[str, LLMAdapter],
    provider: str | None = None,
    model: str | None = None,
    prompt_family: str | None = None,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
) -> JdExtraction:
    """Extract a job description's skills and requirement levels.

    Args:
        description: The job description (may be HTML-escaped HTML, as
            stored by ATS sources).
        adapters: Every available LLM adapter, keyed by provider.
        provider: Overrides the `skill_extraction` routing (eval harness).
        model: The model to use with `provider`.
        prompt_family: Which prompt family to load; defaults to "local".
        max_chunk_chars: Maximum description characters per LLM call.

    Returns:
        The merged `JdExtraction`.

    Raises:
        ValueError: If any chunk's response can't be parsed as the expected
            JSON shape.
    """
    family = prompt_family or _PROMPT_FAMILY
    template = load_prompt("skill_extraction", family, _PROMPT_VERSION_NUMBER)
    prompt_version = f"{family}.v{_PROMPT_VERSION_NUMBER}"

    collected: list[ExtractedSkill] = []
    model_id = ""
    for chunk in split_into_chunks(readable_description(description), max_chunk_chars):
        response = complete(
            task="skill_extraction",
            prompt=template.format(description=chunk),
            prompt_version=prompt_version,
            adapters=adapters,
            provider=provider,
            model=model,
        )
        model_id = response.model
        response_text = response.text.strip()
        try:
            parsed = parse_json_response(response_text)
            collected.extend(_Response.model_validate(parsed).skills)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"could not parse skill_extraction response: {exc} "
                f"(response started with: {response_text[:200]!r})"
            ) from exc
    return JdExtraction(merge_skills(collected), prompt_version, model_id)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.test_skills_jd_extract tests.test_task_config tests.test_llm_prompts -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
black packages/core && isort packages/core && ruff check packages/core
git add prompts/skill_extraction config/llm_tasks.yml packages/core/core/skills/jd_extract.py packages/core/tests/skills_fakes.py packages/core/tests/test_skills_jd_extract.py
git commit -m "feat(job_search): local-LLM JD skill extraction with must/nice-to-have levels (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Batch writer and `extract-job-skills` command

**Files:**
- Create: `packages/core/core/skills/write_job_skills.py`
- Modify: `apps/pipeline/app/cli.py`
- Test: `packages/core/tests/integration/test_write_job_skills.py`; extend `test_pipeline_cli_skills.py`

**Interfaces:**
- Consumes: `extract_jd_skills`, `CURRENT_PROMPT_VERSION`, `normalise_skill`, `silver.job_survivorship.winning_description`, `silver.job_skill_extraction`, `silver.job_skill_raw`, `FakeAdapter`.
- Produces:
  - `WriteSummary(extracted_jobs: int, skill_rows: int, failed_jobs: int)` (frozen dataclass)
  - `write_job_skills(engine: Engine, *, adapters: dict[str, LLMAdapter], job_group_ids: list[str] | None = None, limit: int | None = None) -> WriteSummary` — extracts every dedup survivor with a description and no extraction row at `CURRENT_PROMPT_VERSION`; one transaction per job; a job whose response cannot be parsed (or whose LLM call errors) is counted as failed, recorded nowhere, and retried next run.
  - CLI `pipeline extract-job-skills [--limit N]`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_write_job_skills.py`:

```python
"""Integration tests for core.skills.write_job_skills against live Postgres."""

from __future__ import annotations

import json
import unittest
import uuid

from sqlalchemy import text

from core.skills.write_job_skills import write_job_skills
from tests.integration.skills_fixtures import live_owner_engine, purge_fixtures
from tests.skills_fakes import FakeAdapter


def _reply(*skills: tuple[str, str]) -> str:
    return json.dumps(
        {"skills": [{"skill": s, "requirement_level": lvl} for s, lvl in skills]}
    )


class TestWriteJobSkills(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _add_survivor(self, job: str, description: str | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, apply_source_job_id, "
                    "apply_job_url, apply_title_for_display) VALUES (:g, :d, "
                    "'greenhouse', :s, 'https://example.test/x', 'Data Engineer')"
                ),
                {"g": job, "d": description, "s": f"src-{job}"},
            )

    def _write(self, adapter: FakeAdapter, jobs: list[str], **kwargs):
        return write_job_skills(
            self.engine, adapters={"ollama": adapter}, job_group_ids=jobs, **kwargs
        )

    def _raw(self, job: str) -> list[tuple[str, str]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT raw_norm, requirement_level FROM silver.job_skill_raw "
                    "WHERE job_group_id = :j ORDER BY raw_norm"
                ),
                {"j": job},
            ).all()
        return [(r.raw_norm, r.requirement_level) for r in rows]

    def _extractions(self, job: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT count(*) FROM silver.job_skill_extraction "
                    "WHERE job_group_id = :j"
                ),
                {"j": job},
            ).scalar_one()

    def test_extracts_and_stores_skills_with_levels(self) -> None:
        self._add_survivor(self.job, "Python required. dbt is a plus.")
        adapter = FakeAdapter(_reply(("Python", "must_have"), ("dbt", "nice_to_have")))
        summary = self._write(adapter, [self.job])
        self.assertEqual(
            (summary.extracted_jobs, summary.skill_rows, summary.failed_jobs), (1, 2, 0)
        )
        self.assertEqual(
            self._raw(self.job), [("dbt", "nice_to_have"), ("python", "must_have")]
        )
        with self.engine.connect() as conn:
            version = conn.execute(
                text(
                    "SELECT prompt_version FROM silver.job_skill_extraction "
                    "WHERE job_group_id = :j"
                ),
                {"j": self.job},
            ).scalar_one()
        self.assertEqual(version, "local.v1")

    def test_a_second_run_does_no_llm_work(self) -> None:
        self._add_survivor(self.job, "Python required.")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        self._write(adapter, [self.job])
        calls = len(adapter.calls)
        summary = self._write(adapter, [self.job])
        self.assertEqual(summary.extracted_jobs, 0)
        self.assertEqual(len(adapter.calls), calls)

    def test_a_job_with_no_skills_is_recorded_and_not_retried(self) -> None:
        self._add_survivor(self.job, "We are a friendly company.")
        adapter = FakeAdapter(_reply())
        self._write(adapter, [self.job])
        self.assertEqual(self._extractions(self.job), 1)
        self.assertEqual(self._raw(self.job), [])
        calls = len(adapter.calls)
        self._write(adapter, [self.job])
        self.assertEqual(len(adapter.calls), calls)

    def test_an_unparseable_response_counts_as_failed_and_is_retried(self) -> None:
        self._add_survivor(self.job, "Python required.")
        summary = self._write(FakeAdapter("no idea"), [self.job])
        self.assertEqual((summary.extracted_jobs, summary.failed_jobs), (0, 1))
        self.assertEqual(self._extractions(self.job), 0)
        retry = self._write(FakeAdapter(_reply(("Python", "must_have"))), [self.job])
        self.assertEqual(retry.extracted_jobs, 1)

    def test_jobs_without_a_description_are_skipped(self) -> None:
        self._add_survivor(self.job, None)
        adapter = FakeAdapter(_reply())
        self.assertEqual(self._write(adapter, [self.job]).extracted_jobs, 0)
        self.assertEqual(adapter.calls, [])

    def test_limit_caps_the_jobs_processed(self) -> None:
        other = f"fixture-job-{uuid.uuid4().hex[:8]}"
        self._add_survivor(self.job, "Python required.")
        self._add_survivor(other, "SQL required.")
        summary = self._write(FakeAdapter(_reply(("Python", "must_have"))), [self.job, other], limit=1)
        self.assertEqual(summary.extracted_jobs, 1)


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.integration.test_write_job_skills -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'core.skills.write_job_skills'`

- [ ] **Step 2: Write the writer**

Create `packages/core/core/skills/write_job_skills.py`:

```python
"""Batch write path for silver.job_skill_extraction / job_skill_raw (Step 14).

Extracts skills for every dedup survivor (silver.job_survivorship) that has
a description and no extraction at the current prompt version. Same
"only new work" pattern as `write_job_category`: a routine run never
re-calls the LLM for already-extracted jobs. Each job commits on its own —
local 8B generation on CPU is slow, so a long batch must keep its progress
if interrupted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine, text

from core.llm.types import LLMAdapter
from core.skills.jd_extract import CURRENT_PROMPT_VERSION, extract_jd_skills
from core.skills.normalise import normalise_skill

logger = logging.getLogger(__name__)

_SELECT_PENDING = text(
    "SELECT js.job_group_id, js.winning_description AS description "
    "FROM silver.job_survivorship AS js "
    "LEFT JOIN silver.job_skill_extraction AS e "
    "ON e.job_group_id = js.job_group_id AND e.prompt_version = :prompt_version "
    "WHERE e.job_group_id IS NULL AND js.winning_description IS NOT NULL "
    "AND (CAST(:job_group_ids AS text[]) IS NULL "
    "OR js.job_group_id = ANY(:job_group_ids)) "
    "ORDER BY js.job_group_id LIMIT CAST(:limit AS integer)"
)
_INSERT_EXTRACTION = text(
    "INSERT INTO silver.job_skill_extraction (job_group_id, prompt_version, model) "
    "VALUES (:job_group_id, :prompt_version, :model) "
    "ON CONFLICT (job_group_id, prompt_version) DO NOTHING"
)
_INSERT_RAW = text(
    "INSERT INTO silver.job_skill_raw (job_group_id, prompt_version, raw_skill, "
    "raw_norm, requirement_level) VALUES (:job_group_id, :prompt_version, "
    ":raw_skill, :raw_norm, :requirement_level) ON CONFLICT DO NOTHING"
)


@dataclass(frozen=True)
class WriteSummary:
    """Counts from one `write_job_skills` run.

    Attributes:
        extracted_jobs: Jobs successfully extracted and recorded.
        skill_rows: `job_skill_raw` rows written.
        failed_jobs: Jobs whose extraction failed (retried next run).
    """

    extracted_jobs: int
    skill_rows: int
    failed_jobs: int


def write_job_skills(
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    job_group_ids: list[str] | None = None,
    limit: int | None = None,
) -> WriteSummary:
    """Extract and store skills for every not-yet-extracted dedup survivor.

    Args:
        engine: The owner-role engine (shared-zone tables).
        adapters: Every available LLM adapter, keyed by provider.
        job_group_ids: Restrict to these jobs; `None` (what the CLI passes)
            covers every pending survivor. Exists so tests never extract
            unrelated rows in the shared dev DB.
        limit: Process at most this many jobs; `None` for all.

    Returns:
        The `WriteSummary`.
    """
    with engine.connect() as conn:
        pending = conn.execute(
            _SELECT_PENDING,
            {
                "prompt_version": CURRENT_PROMPT_VERSION,
                "job_group_ids": job_group_ids,
                "limit": limit,
            },
        ).all()

    extracted = skill_rows = failed = 0
    for row in pending:
        try:
            extraction = extract_jd_skills(row.description, adapters=adapters)
        except (ValueError, httpx.HTTPError) as exc:
            failed += 1
            logger.warning("skill extraction failed for %s: %s", row.job_group_id, exc)
            continue
        raw_rows = [
            {
                "job_group_id": row.job_group_id,
                "prompt_version": extraction.prompt_version,
                "raw_skill": skill.skill.strip(),
                "raw_norm": normalise_skill(skill.skill),
                "requirement_level": skill.requirement_level,
            }
            for skill in extraction.skills
        ]
        with engine.begin() as conn:
            conn.execute(
                _INSERT_EXTRACTION,
                {
                    "job_group_id": row.job_group_id,
                    "prompt_version": extraction.prompt_version,
                    "model": extraction.model or None,
                },
            )
            if raw_rows:
                conn.execute(_INSERT_RAW, raw_rows)
        extracted += 1
        skill_rows += len(raw_rows)
    return WriteSummary(extracted, skill_rows, failed)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.integration.test_write_job_skills -v`
Expected: PASS (6 tests)

- [ ] **Step 4: Add the `extract-job-skills` subcommand**

Extend `TestSkillSubcommandsAreRegistered` in `test_pipeline_cli_skills.py`:

```python
    def test_extract_job_skills_is_registered(self) -> None:
        self._help_exits_zero("extract-job-skills")
```

Run it to see it fail. In `apps/pipeline/app/cli.py` add `from core.skills.write_job_skills import write_job_skills` and above `_EVAL_TASKS`:

```python
def _cmd_extract_job_skills(args: argparse.Namespace) -> int:
    """Run the `extract-job-skills` subcommand.

    Args:
        args: Parsed CLI arguments — optional `limit`.

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    # A local 8B model generating on CPU routinely takes far longer than the
    # 30s used elsewhere here (see apps/api/app/dependencies.py's
    # get_ollama_http_client for the measured numbers).
    http_client = httpx.Client(timeout=2000.0)
    try:
        adapters = _build_llm_adapters(http_client)
        summary = write_job_skills(engine, adapters=adapters, limit=args.limit)
    finally:
        http_client.close()
    print(
        f"extract-job-skills complete: extracted_jobs={summary.extracted_jobs} "
        f"skill_rows={summary.skill_rows} failed_jobs={summary.failed_jobs}"
    )
    return 0
```

In `main`:

```python
    extract_parser = subparsers.add_parser(
        "extract-job-skills",
        help="Extract skills + must/nice-to-have levels for every dedup survivor",
    )
    extract_parser.add_argument(
        "--limit", type=int, default=None, help="Process at most this many jobs"
    )
```
```python
    if args.command == "extract-job-skills":
        return _cmd_extract_job_skills(args)
```

Run: `cd packages/core && python -m unittest tests.test_pipeline_cli_skills -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
black apps packages/core && isort apps packages/core && ruff check apps packages/core
git add packages/core/core/skills/write_job_skills.py packages/core/tests/integration/test_write_job_skills.py packages/core/tests/test_pipeline_cli_skills.py apps/pipeline/app/cli.py
git commit -m "feat(job_search): batch JD skill extraction writer and extract-job-skills command (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: CV skill mapping and `map-cv-skills` command

**Files:**
- Create: `packages/core/core/skills/cv_map.py`
- Modify: `apps/pipeline/app/cli.py`
- Test: `packages/core/tests/integration/test_skills_cv_map.py`; extend `test_pipeline_cli_skills.py`

**Interfaces:**
- Consumes: `map_strings`, `normalise_skill`, `core.cv.store.read_truth_base` / `write_truth_base`, `core.cv.schema.Skill`.
- Produces:
  - `CV_MAP_LABEL: str = "ESCO skill normalisation"`
  - `CvMapResult(new_version: int | None, mapped: int, unmapped: int)` (frozen dataclass; `new_version` is None when nothing changed)
  - `map_cv_skills(*, app_engine: Engine, owner_engine: Engine, user_id: uuid.UUID, embed: Callable[[str], list[float]], embedding_model: str, accept_threshold: float = EMBEDDING_ACCEPT_COSINE) -> CvMapResult` — fills each skill's `canonical_id` where it is `None` and a mapping exists; never overwrites an existing `canonical_id`; writes a new truth-base version only if something changed; raises `LookupError` if the user has no truth base.
  - CLI `pipeline map-cv-skills --user-id <uuid>`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/integration/test_skills_cv_map.py`:

```python
"""Integration tests for core.skills.cv_map against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.cv.schema import Bullet, CVTruthBase, Experience, Skill
from core.cv.store import read_truth_base, write_truth_base
from core.db.session import session_scope
from core.settings import get_settings
from core.skills.cv_map import CV_MAP_LABEL, map_cv_skills
from core.skills.esco_load import load_esco
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
    sparse_vector,
)

_MODEL = get_settings().embedding_model


def _far_embed(_text: str) -> list[float]:
    return sparse_vector({767: 1.0})


class TestMapCvSkills(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)
        load_esco(self.owner, FIXTURE_ESCO_DIR)
        self.user_id = uuid.uuid4()
        with session_scope(self.owner) as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'Test User')"
                ),
                {"id": self.user_id, "email": f"{self.user_id}@example.com"},
            )
        write_truth_base(
            self.app,
            self.user_id,
            "# markdown",
            CVTruthBase(
                identity="Jane Doe",
                headline="Engineer",
                skills=[
                    Skill(name="ZZFixture Cloud Platforms"),
                    Skill(name="zzfixture unheard of thing"),
                    Skill(name="Kept", canonical_id="manual:1"),
                ],
                experience=[
                    Experience(
                        company="Fixture Corp",
                        title="Engineer",
                        bullets=[Bullet(bullet_id="fixture01", text="Did things.")],
                    )
                ],
            ),
        )

    def tearDown(self) -> None:
        purge_fixtures(self.owner)
        with session_scope(self.owner) as conn:
            for table in ("cv_truth_base_history", "cv_truth_base"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE user_id = :id"), {"id": self.user_id}
                )
            conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id})

    def _run(self):
        return map_cv_skills(
            app_engine=self.app,
            owner_engine=self.owner,
            user_id=self.user_id,
            embed=_far_embed,
            embedding_model=_MODEL,
        )

    def test_fills_canonical_ids_and_writes_a_labelled_new_version(self) -> None:
        result = self._run()
        self.assertEqual((result.new_version, result.mapped, result.unmapped), (2, 2, 1))
        stored = read_truth_base(self.app, self.user_id)
        ids = {s.name: s.canonical_id for s in stored.truth_base.skills}
        self.assertEqual(
            ids,
            {
                "ZZFixture Cloud Platforms": "fixture-cloud",
                "zzfixture unheard of thing": None,
                "Kept": "manual:1",
            },
        )
        self.assertEqual(stored.label, CV_MAP_LABEL)

    def test_bullet_ids_are_untouched(self) -> None:
        self._run()
        stored = read_truth_base(self.app, self.user_id)
        self.assertEqual(
            stored.truth_base.experience[0].bullets[0].bullet_id, "fixture01"
        )

    def test_a_second_run_writes_no_new_version(self) -> None:
        self._run()
        second = self._run()
        self.assertIsNone(second.new_version)
        self.assertEqual(read_truth_base(self.app, self.user_id).version, 2)

    def test_flags_the_mapping_rows_as_seen_in_a_cv_and_queues_unmapped_ones(self) -> None:
        self._run()
        with self.owner.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT raw_norm, seen_in_cv, review_status "
                    "FROM silver.skill_mapping WHERE raw_norm LIKE 'zzfixture%' "
                    "ORDER BY raw_norm"
                )
            ).all()
        self.assertEqual(
            [(r.raw_norm, r.seen_in_cv, r.review_status) for r in rows],
            [
                ("zzfixture cloud platforms", True, None),
                ("zzfixture unheard of thing", True, "open"),
            ],
        )

    def test_a_user_without_a_cv_raises_lookup_error(self) -> None:
        with self.assertRaises(LookupError):
            map_cv_skills(
                app_engine=self.app,
                owner_engine=self.owner,
                user_id=uuid.uuid4(),
                embed=_far_embed,
                embedding_model=_MODEL,
            )


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.integration.test_skills_cv_map -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'core.skills.cv_map'`

- [ ] **Step 2: Write the implementation**

Create `packages/core/core/skills/cv_map.py`:

```python
"""Map a user's CV skills to ESCO / custom skill ids (PLAN.md Step 14).

Fills `Skill.canonical_id` in the CV truth base where it is still None and
the skill string has a mapping. The write goes through
`core.cv.store.write_truth_base` with a label, so it is a new, traceable,
reversible version exactly like any other edit; bullet IDs are untouched.
Shared mapping rows (`silver.skill_mapping`) are written with the owner
engine; the per-user truth base is read and written with the RLS-subject
app engine.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine

from core.cv.store import read_truth_base, write_truth_base
from core.skills.mapper import EMBEDDING_ACCEPT_COSINE, map_strings
from core.skills.normalise import normalise_skill

CV_MAP_LABEL = "ESCO skill normalisation"


@dataclass(frozen=True)
class CvMapResult:
    """The outcome of mapping one user's CV skills.

    Attributes:
        new_version: The truth-base version written, or None if no skill
            changed (so no version was written).
        mapped: Skills that now have a `canonical_id`.
        unmapped: Skills still without one (they are in the review list).
    """

    new_version: int | None
    mapped: int
    unmapped: int


def map_cv_skills(
    *,
    app_engine: Engine,
    owner_engine: Engine,
    user_id: uuid.UUID,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    accept_threshold: float = EMBEDDING_ACCEPT_COSINE,
) -> CvMapResult:
    """Fill `canonical_id` on a user's CV skills.

    Args:
        app_engine: The app-role (RLS) engine, for the truth base.
        owner_engine: The owner-role engine, for the shared mapping table.
        user_id: Whose CV to map.
        embed: Maps a string to its embedding.
        embedding_model: The embedding model in use.
        accept_threshold: Minimum cosine similarity to accept an embedding
            match (see `core.skills.mapper.map_skill`).

    Returns:
        The `CvMapResult`. An existing `canonical_id` (e.g. hand-corrected
        in the CV Editor) is never overwritten.

    Raises:
        LookupError: If the user has no CV truth base.
        core.skills.mapper.EmbeddingModelMismatch: If the stored ESCO
            embeddings are from a different model.
    """
    stored = read_truth_base(app_engine, user_id)
    if stored is None:
        raise LookupError(f"user {user_id} has no CV truth base")
    truth_base = stored.truth_base

    resolved = map_strings(
        owner_engine,
        [skill.name for skill in truth_base.skills],
        embed=embed,
        embedding_model=embedding_model,
        seen_in_cv=True,
        accept_threshold=accept_threshold,
    )
    changed = 0
    skills = []
    for skill in truth_base.skills:
        skill_id = resolved.get(normalise_skill(skill.name))
        if skill.canonical_id is None and skill_id is not None:
            skills.append(skill.model_copy(update={"canonical_id": skill_id}))
            changed += 1
        else:
            skills.append(skill)

    new_version = None
    if changed:
        new_version = write_truth_base(
            app_engine,
            user_id,
            stored.extracted_markdown,
            truth_base.model_copy(update={"skills": skills}),
            label=CV_MAP_LABEL,
        )
    unmapped = sum(1 for skill in skills if skill.canonical_id is None)
    return CvMapResult(new_version, mapped=len(skills) - unmapped, unmapped=unmapped)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.integration.test_skills_cv_map -v`
Expected: PASS (5 tests)

- [ ] **Step 4: Add the `map-cv-skills` subcommand**

Extend `TestSkillSubcommandsAreRegistered` in `test_pipeline_cli_skills.py`:

```python
    def test_map_cv_skills_is_registered(self) -> None:
        self._help_exits_zero("map-cv-skills")

    def test_map_cv_skills_rejects_a_malformed_user_id(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["map-cv-skills", "--user-id", "not-a-uuid"])
        self.assertEqual(ctx.exception.code, 2)
```

Run to see them fail. In `apps/pipeline/app/cli.py` add `import uuid`, `from core.skills.cv_map import map_cv_skills`, and above `_EVAL_TASKS`:

```python
def _cmd_map_cv_skills(args: argparse.Namespace) -> int:
    """Run the `map-cv-skills` subcommand.

    Args:
        args: Parsed CLI arguments — `user_id`.

    Returns:
        0 on success, 1 if the user has no CV or the ESCO embeddings are
        from a different model than the configured one.
    """
    settings = get_settings()
    owner_engine = build_engine(settings.database_url)
    app_engine = build_engine(settings.app_database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        result = map_cv_skills(
            app_engine=app_engine,
            owner_engine=owner_engine,
            user_id=args.user_id,
            embed=_build_embedder(http_client, settings),
            embedding_model=settings.embedding_model,
        )
    except (LookupError, EmbeddingModelMismatch) as exc:
        print(f"map-cv-skills: {exc}")
        return 1
    finally:
        http_client.close()
    version = result.new_version if result.new_version is not None else "unchanged"
    print(
        f"map-cv-skills complete: mapped={result.mapped} "
        f"unmapped={result.unmapped} truth_base_version={version}"
    )
    return 0
```

In `main`:

```python
    map_cv_parser = subparsers.add_parser(
        "map-cv-skills",
        help="Fill canonical_id on a user's CV skills (writes a new CV version)",
    )
    map_cv_parser.add_argument("--user-id", required=True, type=uuid.UUID)
```
```python
    if args.command == "map-cv-skills":
        return _cmd_map_cv_skills(args)
```

Run: `cd packages/core && python -m unittest tests.test_pipeline_cli_skills -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
black apps packages/core && isort apps packages/core && ruff check apps packages/core
git add packages/core/core/skills/cv_map.py packages/core/tests/integration/test_skills_cv_map.py packages/core/tests/test_pipeline_cli_skills.py apps/pipeline/app/cli.py
git commit -m "feat(job_search): map CV skills to canonical ids as a new truth-base version (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: dbt models `silver__skill` and `silver__bridge_job_skill`

**Files:**
- Create: `dbt/models/silver/silver__skill.sql`
- Create: `dbt/models/silver/silver__bridge_job_skill.sql`
- Create: `dbt/models/silver/_silver_skills.yml` (sources + model docs + tests; a new file, so no existing YAML is edited)
- Test: `packages/core/tests/integration/test_dbt_skill_bridge.py`

**Interfaces:**
- Consumes: `esco.skill`, `silver.custom_skill`, `silver.skill_mapping`, `silver.job_skill_extraction`, `silver.job_skill_raw`, `silver.job_survivorship` (the last already declared in `_gold.yml`'s `silver_ingest` source); fixtures/helpers from Tasks 2–4.
- Produces:
  - `silver.silver__skill(skill_id, canonical_label, source)` — grain `skill_id`; `source` is `esco` or `custom`.
  - `silver.silver__bridge_job_skill(job_group_id, skill_id, requirement_level, mention_count)` — grain `(job_group_id, skill_id)`; built from each job's latest extraction and mapped strings only; `requirement_level` is `must_have` if any mapped mention is. Step 15 reads this.

- [ ] **Step 1: Write the failing integration test (runs dbt)**

Create `packages/core/tests/integration/test_dbt_skill_bridge.py`:

```python
"""Builds the Step 14 dbt models against fixture data and checks the bridge."""

from __future__ import annotations

import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

from core.skills.esco_load import load_esco
from tests.integration.skills_fixtures import (
    FIXTURE_ESCO_DIR,
    insert_job_skills,
    insert_mapping,
    live_owner_engine,
    purge_fixtures,
)

_DBT_DIR = Path(__file__).resolve().parents[4] / "dbt"
_SELECT = ["--select", "silver__skill", "silver__bridge_job_skill"]


def _dbt_run() -> None:
    subprocess.run(
        ["dbt", "run", "--project-dir", str(_DBT_DIR), "--profiles-dir", str(_DBT_DIR), *_SELECT],
        check=True,
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(shutil.which("dbt"), "dbt is not installed")
@unittest.skipUnless((_DBT_DIR / "dbt_packages").is_dir(), "run `dbt deps` first")
class TestSkillBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        load_esco(self.engine, FIXTURE_ESCO_DIR)
        sfx = uuid.uuid4().hex[:8]
        self.job_a = f"fixture-job-a-{sfx}"
        self.job_b = f"fixture-job-b-{sfx}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
                    "VALUES ('custom:fixture-c', 'Fixture Custom')"
                )
            )
            for norm, skill_id, method in (
                ("zzfixture cloud platforms", "fixture-cloud", "label"),
                ("zzfixture cloud computing", "fixture-cloud", "label"),
                ("zzfixture sql", "fixture-sql", "label"),
                ("zzfixture python", "fixture-python", "label"),
                ("zzfixture custom", "custom:fixture-c", "alias"),
            ):
                insert_mapping(
                    conn, norm, skill_id=skill_id, method=method, review_status=None
                )
            insert_mapping(conn, "zzfixture unmapped")  # open: must be excluded
            insert_job_skills(
                conn,
                self.job_a,
                "local.v1",
                [
                    ("ZZFixture Cloud Platforms", "zzfixture cloud platforms", "nice_to_have"),
                    ("zzfixture cloud computing", "zzfixture cloud computing", "must_have"),
                    ("zzfixture unmapped", "zzfixture unmapped", "must_have"),
                    ("zzfixture custom", "zzfixture custom", "nice_to_have"),
                ],
            )
            insert_job_skills(
                conn, self.job_b, "local.v0", [("zzfixture python", "zzfixture python", "must_have")]
            )
            conn.execute(
                text(
                    "UPDATE silver.job_skill_extraction "
                    "SET extracted_at = now() - interval '1 day' "
                    "WHERE job_group_id = :j"
                ),
                {"j": self.job_b},
            )
            insert_job_skills(
                conn, self.job_b, "local.v1", [("zzfixture sql", "zzfixture sql", "nice_to_have")]
            )
        _dbt_run()

    def tearDown(self) -> None:
        purge_fixtures(self.engine)
        _dbt_run()  # rebuild without the fixtures so later `dbt test` runs stay clean

    def _bridge(self) -> dict[tuple[str, str], tuple[str, int]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT job_group_id, skill_id, requirement_level, mention_count "
                    "FROM silver.silver__bridge_job_skill "
                    "WHERE job_group_id IN (:a, :b)"
                ),
                {"a": self.job_a, "b": self.job_b},
            ).all()
        return {
            (r.job_group_id, r.skill_id): (r.requirement_level, r.mention_count)
            for r in rows
        }

    def test_collapses_to_job_and_skill_with_must_have_winning(self) -> None:
        self.assertEqual(
            self._bridge()[(self.job_a, "fixture-cloud")], ("must_have", 2)
        )

    def test_excludes_unmapped_strings(self) -> None:
        skills = {skill for (_job, skill) in self._bridge()}
        self.assertNotIn(None, skills)
        self.assertEqual(
            {s for (j, s) in self._bridge() if j == self.job_a},
            {"fixture-cloud", "custom:fixture-c"},
        )

    def test_uses_only_each_jobs_latest_extraction(self) -> None:
        self.assertEqual(
            {s for (j, s) in self._bridge() if j == self.job_b}, {"fixture-sql"}
        )

    def test_skill_vocabulary_unions_esco_and_custom(self) -> None:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT skill_id, source FROM silver.silver__skill "
                    "WHERE skill_id IN ('fixture-cloud', 'custom:fixture-c')"
                )
            ).all()
        self.assertEqual(
            {(r.skill_id, r.source) for r in rows},
            {("fixture-cloud", "esco"), ("custom:fixture-c", "custom")},
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Prepare dbt packages once: `cd dbt && dbt deps` (or copy the main checkout's `dbt/dbt_packages` directory in — it is gitignored).
Run: `cd packages/core && python -m unittest tests.integration.test_dbt_skill_bridge -v`
Expected: ERROR — `dbt run` fails: the selected models do not exist (`Nothing to do` / `CalledProcessError`).

- [ ] **Step 3: Write the models**

Create `dbt/models/silver/silver__skill.sql`:

```sql
-- silver__skill: the unified skill vocabulary — every ESCO skill plus every
-- custom skill (tools ESCO lacks, from the seed file or the review page).
-- Grain: skill_id (unique).

SELECT
    skill_id,
    preferred_label AS canonical_label,
    'esco' AS source
FROM {{ source('esco_ingest', 'skill') }}

UNION ALL

SELECT
    skill_id,
    canonical_label,
    'custom' AS source
FROM {{ source('silver_ingest', 'custom_skill') }}
```

Create `dbt/models/silver/silver__bridge_job_skill.sql`:

```sql
-- silver__bridge_job_skill: which normalised skills each deduplicated job asks
-- for, and whether each is a must-have or a nice-to-have (PLAN.md Step 14).
-- Grain: (job_group_id, skill_id) (unique).
--
-- Unmapped strings are excluded on purpose: they live in the review list
-- (silver.skill_mapping.review_status), not in this mart.

WITH latest_extraction AS (

    -- Each job's most recent completed extraction run, so a re-extraction
    -- under a new prompt version replaces the old one instead of mixing in.
    SELECT DISTINCT ON (job_group_id)
        job_group_id,
        prompt_version
    FROM {{ source('silver_ingest', 'job_skill_extraction') }}
    ORDER BY job_group_id ASC, extracted_at DESC

),

job_skills AS (

    -- The raw skill strings from that latest run.
    SELECT
        r.job_group_id,
        r.raw_norm,
        r.requirement_level
    FROM {{ source('silver_ingest', 'job_skill_raw') }} AS r
    INNER JOIN latest_extraction AS le
        ON r.job_group_id = le.job_group_id
        AND r.prompt_version = le.prompt_version

),

mapped AS (

    -- Only strings that resolved to a skill.
    SELECT
        js.job_group_id,
        m.skill_id,
        js.requirement_level
    FROM job_skills AS js
    INNER JOIN {{ source('silver_ingest', 'skill_mapping') }} AS m
        ON js.raw_norm = m.raw_norm
    WHERE m.skill_id IS NOT NULL

)

SELECT
    job_group_id,
    skill_id,
    -- A skill named as required anywhere in the job is required.
    CASE
        WHEN BOOL_OR(requirement_level = 'must_have') THEN 'must_have'
        ELSE 'nice_to_have'
    END AS requirement_level,
    COUNT(*) AS mention_count
FROM mapped
GROUP BY job_group_id, skill_id
```

Create `dbt/models/silver/_silver_skills.yml`:

```yaml
version: 2

sources:
  - name: esco_ingest
    schema: esco
    tables:
      - name: skill
        description: >
          ESCO skills, loaded by the load-esco pipeline CLI subcommand
          (core.skills.esco_load), not dbt.
        columns:
          - name: skill_id
            description: "Trailing UUID of the ESCO concept URI."
  - name: silver_ingest
    schema: silver
    tables:
      - name: custom_skill
        description: >
          Skills ESCO lacks (`custom:<slug>`), written by the seed-alias sync
          and the Skill Review page (Step 14).
      - name: skill_mapping
        description: >
          One row per distinct normalised skill string, written by the
          map-skills / map-cv-skills subcommands (core.skills.mapper) and the
          Skill Review page. skill_id is NULL while unmapped.
      - name: job_skill_extraction
        description: >
          One row per (job_group_id, prompt_version) extraction run, written
          by extract-job-skills (core.skills.write_job_skills).
      - name: job_skill_raw
        description: >
          The LLM's raw skill strings per extraction run, with their
          must_have / nice_to_have level.

models:
  - name: silver__skill
    description: >
      The unified skill vocabulary: every ESCO skill plus every custom skill
      (PLAN.md Step 14). The target of silver__bridge_job_skill.skill_id.
    columns:
      - name: skill_id
        description: "ESCO skill UUID, or `custom:<slug>`."
        data_type: text
        tests:
          - unique
          - not_null
      - name: canonical_label
        description: "The skill's display name."
        data_type: text
        tests: [not_null]
      - name: source
        description: "'esco' or 'custom'."
        data_type: text
        tests:
          - not_null
          - accepted_values:
              values: ['esco', 'custom']

  - name: silver__bridge_job_skill
    description: >
      Which normalised skills each deduplicated job asks for, and whether each
      is a must-have or nice-to-have (PLAN.md Step 14). Built from each job's
      latest extraction run and mapped strings only; a skill is must_have if
      any mapped mention is. Feeds Step 15's coverage score and Step 21's gap
      ranking.
    tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [job_group_id, skill_id]
    columns:
      - name: job_group_id
        description: "The deduplicated job (silver.job_survivorship)."
        data_type: text
        tests:
          - not_null
          - relationships:
              to: source('silver_ingest', 'job_survivorship')
              field: job_group_id
      - name: skill_id
        description: "The mapped skill (silver__skill)."
        data_type: text
        tests:
          - not_null
          - relationships:
              to: ref('silver__skill')
              field: skill_id
      - name: requirement_level
        description: "'must_have' if any mapped mention is, else 'nice_to_have'."
        data_type: text
        tests:
          - not_null
          - accepted_values:
              values: ['must_have', 'nice_to_have']
      - name: mention_count
        description: "How many extracted strings for this job mapped to this skill."
        data_type: bigint
        tests: [not_null]
```

- [ ] **Step 4: Run the test and the dbt tests**

Run: `cd packages/core && python -m unittest tests.integration.test_dbt_skill_bridge -v`
Expected: PASS (4 tests)

Run: `cd dbt && dbt test --select silver__skill silver__bridge_job_skill`
Expected: all tests PASS. (A `relationships` failure on `skill_id` here means a mapping points at a skill that is in neither `esco.skill` nor `silver.custom_skill` — for example ESCO is not loaded yet but an alias targets an ESCO id. That is the test doing its job; fix the data, not the test.)

- [ ] **Step 5: Commit**

```bash
black packages/core && isort packages/core && ruff check packages/core
git add dbt/models/silver/silver__skill.sql dbt/models/silver/silver__bridge_job_skill.sql dbt/models/silver/_silver_skills.yml packages/core/tests/integration/test_dbt_skill_bridge.py
git commit -m "feat(job_search): silver__skill and silver__bridge_job_skill dbt models (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: Review logic and API router

**Files:**
- Create: `packages/core/core/skills/review.py`
- Create: `apps/api/app/routers/skills.py`
- Modify: `apps/api/app/main.py` (register the router)
- Modify: `packages/core/tests/integration/skills_fixtures.py` (purge `custom:zzfixture-%` too)
- Test: `packages/core/tests/integration/test_skills_router.py`

**Interfaces:**
- Consumes: `normalise_skill`, tables from Tasks 2/4, `insert_mapping`, `insert_job_skills`, `load_esco`.
- Produces in `core.skills.review` (all take an open `Connection`; callers wrap in `engine.begin()`):
  - `ReviewError(ValueError)`, `ReviewNotFound(ReviewError)`
  - `ReviewItem(raw_norm, raw_example, seen_in_cv, jd_job_count, sample_job_group_ids: list[str], candidate_skill_id, candidate_label, candidate_score)`; `MatchItem(raw_norm, raw_example, skill_id, skill_label, score, jd_job_count)`; `SkillOption(skill_id, label, source)` (frozen dataclasses)
  - `list_unmapped(conn, *, limit: int = 50) -> list[ReviewItem]` (status `open` or `rejected`); `list_embedding_matches(conn, *, limit: int = 50) -> list[MatchItem]` (lowest score first); `search_skills(conn, query: str, *, limit: int = 20) -> list[SkillOption]`
  - `resolve_to_skill(conn, raw_norm: str, skill_id: str) -> None`; `resolve_to_custom(conn, raw_norm: str, canonical_label: str) -> None`; `dismiss(conn, raw_norm: str) -> None`; `reject_embedding_match(conn, raw_norm: str) -> None`
- Produces HTTP (all JSON): `GET /skills/review?limit=`, `GET /skills/review/embedding-matches?limit=`, `GET /skills/search?q=&limit=`, `POST /skills/review/resolve` (`{raw_norm, skill_id}` xor `{raw_norm, custom_label}`), `POST /skills/review/dismiss` and `POST /skills/review/reject` (`{raw_norm}`); success is `{"status": "ok"}`, unknown string → 404, invalid state/skill → 422.

- [ ] **Step 1: Widen the fixture purge for custom skills created via the API**

In `packages/core/tests/integration/skills_fixtures.py`, in `purge_fixtures`, replace the custom-skill delete with:

```python
        conn.execute(text("DELETE FROM silver.custom_skill "
                          "WHERE skill_id LIKE 'custom:fixture-%' "
                          "OR skill_id LIKE 'custom:zzfixture-%'"))
```

- [ ] **Step 2: Write the failing API tests**

Create `packages/core/tests/integration/test_skills_router.py`:

```python
"""Integration tests for the skill-review API (no mocking the database)."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.skills.esco_load import load_esco  # noqa: E402
from tests.integration.skills_fixtures import (  # noqa: E402
    FIXTURE_ESCO_DIR,
    insert_job_skills,
    insert_mapping,
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)

_UNMAPPED = "zzfixture unmapped one"
_MATCHED = "zzfixture matched"


class TestSkillReviewApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)
        load_esco(self.owner, FIXTURE_ESCO_DIR)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"
        with self.owner.begin() as conn:
            insert_mapping(
                conn,
                _UNMAPPED,
                candidate_skill_id="fixture-cloud",
                candidate_score=0.6,
                seen_in_cv=True,
            )
            insert_mapping(
                conn,
                _MATCHED,
                skill_id="fixture-python",
                method="embedding",
                score=0.86,
                review_status=None,
            )
            insert_job_skills(
                conn, self.job, "local.v1", [(_UNMAPPED, _UNMAPPED, "must_have")]
            )
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_app_db_engine, None)
        purge_fixtures(self.owner)

    def _mapping(self, raw_norm: str):
        with self.owner.connect() as conn:
            return conn.execute(
                text("SELECT * FROM silver.skill_mapping WHERE raw_norm = :n"),
                {"n": raw_norm},
            ).one()

    def _review_norms(self) -> set[str]:
        body = self.client.get("/skills/review", params={"limit": 500}).json()
        return {item["raw_norm"] for item in body}

    def test_review_list_shows_the_unmapped_string_with_context_and_suggestion(self) -> None:
        body = self.client.get("/skills/review", params={"limit": 500}).json()
        item = next(i for i in body if i["raw_norm"] == _UNMAPPED)
        self.assertEqual(item["jd_job_count"], 1)
        self.assertEqual(item["sample_job_group_ids"], [self.job])
        self.assertTrue(item["seen_in_cv"])
        self.assertEqual(item["candidate_skill_id"], "fixture-cloud")
        self.assertEqual(item["candidate_label"], "zzfixture cloud technologies")
        self.assertAlmostEqual(item["candidate_score"], 0.6)
        self.assertNotIn(_MATCHED, {i["raw_norm"] for i in body})

    def test_embedding_matches_list_shows_the_auto_mapped_string(self) -> None:
        body = self.client.get(
            "/skills/review/embedding-matches", params={"limit": 500}
        ).json()
        item = next(i for i in body if i["raw_norm"] == _MATCHED)
        self.assertEqual(item["skill_id"], "fixture-python")
        self.assertEqual(item["skill_label"], "zzfixture python")
        self.assertAlmostEqual(item["score"], 0.86)

    def test_search_finds_skills_by_any_label(self) -> None:
        body = self.client.get("/skills/search", params={"q": "zzfixture cloud"}).json()
        found = {o["skill_id"]: o["source"] for o in body}
        self.assertEqual(found.get("fixture-cloud"), "esco")

    def test_search_treats_wildcards_literally(self) -> None:
        body = self.client.get("/skills/search", params={"q": "%"}).json()
        self.assertNotIn("fixture-cloud", {o["skill_id"] for o in body})

    def test_resolving_to_an_esco_skill_maps_it_and_creates_a_review_alias(self) -> None:
        response = self.client.post(
            "/skills/review/resolve", json={"raw_norm": _UNMAPPED, "skill_id": "fixture-cloud"}
        )
        self.assertEqual(response.status_code, 200)
        row = self._mapping(_UNMAPPED)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status),
            ("fixture-cloud", "alias", "resolved"),
        )
        with self.owner.connect() as conn:
            alias = conn.execute(
                text(
                    "SELECT skill_id, source FROM silver.skill_alias "
                    "WHERE alias_norm = :n"
                ),
                {"n": _UNMAPPED},
            ).one()
        self.assertEqual((alias.skill_id, alias.source), ("fixture-cloud", "review"))
        self.assertNotIn(_UNMAPPED, self._review_norms())

    def test_resolving_as_a_custom_skill_creates_it(self) -> None:
        response = self.client.post(
            "/skills/review/resolve",
            json={"raw_norm": _UNMAPPED, "custom_label": "ZZFixture My Tool"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._mapping(_UNMAPPED).skill_id, "custom:zzfixture-my-tool")
        with self.owner.connect() as conn:
            label = conn.execute(
                text(
                    "SELECT canonical_label FROM silver.custom_skill "
                    "WHERE skill_id = 'custom:zzfixture-my-tool'"
                )
            ).scalar_one()
        self.assertEqual(label, "ZZFixture My Tool")

    def test_resolving_to_an_unknown_skill_is_rejected(self) -> None:
        response = self.client.post(
            "/skills/review/resolve", json={"raw_norm": _UNMAPPED, "skill_id": "nope"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self._mapping(_UNMAPPED).review_status, "open")

    def test_resolve_needs_exactly_one_target(self) -> None:
        for payload in (
            {"raw_norm": _UNMAPPED},
            {"raw_norm": _UNMAPPED, "skill_id": "fixture-cloud", "custom_label": "X"},
        ):
            self.assertEqual(
                self.client.post("/skills/review/resolve", json=payload).status_code, 422
            )

    def test_an_unknown_string_is_a_404(self) -> None:
        response = self.client.post(
            "/skills/review/dismiss", json={"raw_norm": "zzfixture does not exist"}
        )
        self.assertEqual(response.status_code, 404)

    def test_dismissing_removes_it_from_the_list_and_only_applies_to_unmapped(self) -> None:
        ok = self.client.post("/skills/review/dismiss", json={"raw_norm": _UNMAPPED})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self._mapping(_UNMAPPED).review_status, "dismissed")
        self.assertNotIn(_UNMAPPED, self._review_norms())
        bad = self.client.post("/skills/review/dismiss", json={"raw_norm": _MATCHED})
        self.assertEqual(bad.status_code, 422)

    def test_rejecting_an_embedding_match_returns_it_to_the_unmapped_list(self) -> None:
        response = self.client.post("/skills/review/reject", json={"raw_norm": _MATCHED})
        self.assertEqual(response.status_code, 200)
        row = self._mapping(_MATCHED)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status, row.candidate_skill_id),
            (None, "none", "rejected", "fixture-python"),
        )
        self.assertIn(_MATCHED, self._review_norms())
        again = self.client.post("/skills/review/reject", json={"raw_norm": _MATCHED})
        self.assertEqual(again.status_code, 422)


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.integration.test_skills_router -v`
Expected: ERROR — every request 404s / import error for the missing router.

- [ ] **Step 3: Write the review logic**

Create `packages/core/core/skills/review.py`:

```python
"""Review-list logic for unmapped and auto-mapped skills (PLAN.md Step 14).

Everything here takes an open `Connection`; the API router wraps each call in
`engine.begin()` so a resolve is one transaction. A resolution writes a
`silver.skill_alias` row (source 'review'), so the fix is made once and applies
to every future CV and JD string that normalises the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import Connection, text

from core.skills.normalise import normalise_skill


class ReviewError(ValueError):
    """Raised when a review action is not valid for the row's current state."""


class ReviewNotFound(ReviewError):
    """Raised when the named skill string has no `skill_mapping` row."""


@dataclass(frozen=True)
class ReviewItem:
    """An unmapped skill string awaiting a decision.

    Attributes:
        raw_norm: The normalised string (the review key).
        raw_example: One original spelling, for display.
        seen_in_cv: Whether a CV contained it.
        jd_job_count: How many jobs' extractions contain it.
        sample_job_group_ids: Up to three such jobs.
        candidate_skill_id: The nearest ESCO skill below the threshold, if any.
        candidate_label: That skill's display label.
        candidate_score: Its cosine similarity.
    """

    raw_norm: str
    raw_example: str
    seen_in_cv: bool
    jd_job_count: int
    sample_job_group_ids: list[str]
    candidate_skill_id: str | None
    candidate_label: str | None
    candidate_score: float | None


@dataclass(frozen=True)
class MatchItem:
    """A string the embedding stage auto-mapped, listed for verification.

    Attributes:
        raw_norm: The normalised string.
        raw_example: One original spelling.
        skill_id: The skill it was mapped to.
        skill_label: That skill's display label.
        score: The cosine similarity that cleared the threshold.
        jd_job_count: How many jobs' extractions contain it.
    """

    raw_norm: str
    raw_example: str
    skill_id: str
    skill_label: str | None
    score: float
    jd_job_count: int


@dataclass(frozen=True)
class SkillOption:
    """One search result the reviewer can map a string to.

    Attributes:
        skill_id: The ESCO id or `custom:<slug>`.
        label: Display label.
        source: "esco" or "custom".
    """

    skill_id: str
    label: str
    source: str


_JD_COUNT = (
    "(SELECT count(DISTINCT r.job_group_id) FROM silver.job_skill_raw AS r "
    "WHERE r.raw_norm = m.raw_norm)"
)


def list_unmapped(conn: Connection, *, limit: int = 50) -> list[ReviewItem]:
    """List unmapped strings needing review, most-requested first.

    Args:
        conn: An open connection.
        limit: Maximum items.

    Returns:
        Items with status `open` or `rejected`, ordered by how many jobs
        mention them, then CV presence.
    """
    rows = conn.execute(
        text(
            f"SELECT m.raw_norm, m.raw_example, m.seen_in_cv, "
            f"m.candidate_skill_id, m.candidate_score, "
            f"COALESCE(es.preferred_label, cs.canonical_label) AS candidate_label, "
            f"{_JD_COUNT} AS jd_job_count, "
            f"ARRAY(SELECT DISTINCT r.job_group_id FROM silver.job_skill_raw AS r "
            f"WHERE r.raw_norm = m.raw_norm ORDER BY r.job_group_id LIMIT 3) "
            f"AS sample_job_group_ids "
            f"FROM silver.skill_mapping AS m "
            f"LEFT JOIN esco.skill AS es ON es.skill_id = m.candidate_skill_id "
            f"LEFT JOIN silver.custom_skill AS cs ON cs.skill_id = m.candidate_skill_id "
            f"WHERE m.review_status IN ('open', 'rejected') "
            f"ORDER BY jd_job_count DESC, m.seen_in_cv DESC, m.raw_norm "
            f"LIMIT :limit"
        ),
        {"limit": limit},
    ).all()
    return [
        ReviewItem(
            raw_norm=r.raw_norm,
            raw_example=r.raw_example,
            seen_in_cv=r.seen_in_cv,
            jd_job_count=r.jd_job_count,
            sample_job_group_ids=list(r.sample_job_group_ids),
            candidate_skill_id=r.candidate_skill_id,
            candidate_label=r.candidate_label,
            candidate_score=(
                float(r.candidate_score) if r.candidate_score is not None else None
            ),
        )
        for r in rows
    ]


def list_embedding_matches(conn: Connection, *, limit: int = 50) -> list[MatchItem]:
    """List auto-mapped embedding matches, least confident first.

    Args:
        conn: An open connection.
        limit: Maximum items.

    Returns:
        Rows whose method is `embedding` (a confirm or reject moves them out),
        lowest cosine score first.
    """
    rows = conn.execute(
        text(
            f"SELECT m.raw_norm, m.raw_example, m.skill_id, m.score, "
            f"COALESCE(es.preferred_label, cs.canonical_label) AS skill_label, "
            f"{_JD_COUNT} AS jd_job_count "
            f"FROM silver.skill_mapping AS m "
            f"LEFT JOIN esco.skill AS es ON es.skill_id = m.skill_id "
            f"LEFT JOIN silver.custom_skill AS cs ON cs.skill_id = m.skill_id "
            f"WHERE m.method = 'embedding' "
            f"ORDER BY m.score ASC, m.raw_norm LIMIT :limit"
        ),
        {"limit": limit},
    ).all()
    return [
        MatchItem(
            raw_norm=r.raw_norm,
            raw_example=r.raw_example,
            skill_id=r.skill_id,
            skill_label=r.skill_label,
            score=float(r.score),
            jd_job_count=r.jd_job_count,
        )
        for r in rows
    ]


def _like_pattern(query: str) -> str:
    """Build a literal-substring LIKE pattern from a search query.

    Args:
        query: The reviewer's search text.

    Returns:
        ``%<normalised query>%`` with LIKE wildcards escaped.
    """
    escaped = (
        normalise_skill(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def search_skills(conn: Connection, query: str, *, limit: int = 20) -> list[SkillOption]:
    """Find skills whose label contains the query.

    Args:
        conn: An open connection.
        query: The search text (normalised before matching).
        limit: Maximum results.

    Returns:
        Custom skills first, then ESCO skills matching on any label
        (preferred, alt or hidden). Empty if the query normalises to nothing.
    """
    if not normalise_skill(query):
        return []
    pattern = _like_pattern(query)
    custom = conn.execute(
        text(
            "SELECT skill_id, canonical_label AS label FROM silver.custom_skill "
            "WHERE lower(canonical_label) LIKE :p ORDER BY canonical_label LIMIT :limit"
        ),
        {"p": pattern, "limit": limit},
    ).all()
    esco = conn.execute(
        text(
            "SELECT s.skill_id, s.preferred_label AS label FROM esco.skill AS s "
            "WHERE EXISTS (SELECT 1 FROM esco.skill_label AS l "
            "WHERE l.skill_id = s.skill_id AND l.label_norm LIKE :p) "
            "ORDER BY s.preferred_label LIMIT :limit"
        ),
        {"p": pattern, "limit": limit},
    ).all()
    options = [SkillOption(r.skill_id, r.label, "custom") for r in custom]
    options += [SkillOption(r.skill_id, r.label, "esco") for r in esco]
    return options[:limit]


def _lock_mapping(conn: Connection, raw_norm: str):
    """Fetch and row-lock one mapping.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.

    Returns:
        The locked `skill_mapping` row.

    Raises:
        ReviewNotFound: If there is no such row.
    """
    row = conn.execute(
        text("SELECT * FROM silver.skill_mapping WHERE raw_norm = :n FOR UPDATE"),
        {"n": raw_norm},
    ).one_or_none()
    if row is None:
        raise ReviewNotFound(f"unknown skill string {raw_norm!r}")
    return row


def resolve_to_skill(conn: Connection, raw_norm: str, skill_id: str) -> None:
    """Map a string to a skill and remember it as a review alias.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.
        skill_id: An ESCO skill id or an existing `custom:<slug>` id.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If `skill_id` is in neither `esco.skill` nor
            `silver.custom_skill`.
    """
    _lock_mapping(conn, raw_norm)
    exists = conn.execute(
        text(
            "SELECT 1 FROM esco.skill WHERE skill_id = :i "
            "UNION ALL SELECT 1 FROM silver.custom_skill WHERE skill_id = :i LIMIT 1"
        ),
        {"i": skill_id},
    ).first()
    if exists is None:
        raise ReviewError(f"unknown skill_id {skill_id!r}")
    conn.execute(
        text(
            "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
            "VALUES (:n, :s, 'review') ON CONFLICT (alias_norm) DO UPDATE "
            "SET skill_id = EXCLUDED.skill_id, source = 'review'"
        ),
        {"n": raw_norm, "s": skill_id},
    )
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET skill_id = :s, method = 'alias', "
            "score = NULL, candidate_skill_id = NULL, candidate_score = NULL, "
            "review_status = 'resolved', mapped_at = now() WHERE raw_norm = :n"
        ),
        {"n": raw_norm, "s": skill_id},
    )


def resolve_to_custom(conn: Connection, raw_norm: str, canonical_label: str) -> None:
    """Create (or reuse) a custom skill and map the string to it.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.
        canonical_label: The custom skill's display name; its slug becomes
            the `custom:<slug>` id.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If the label yields an empty slug.
    """
    _lock_mapping(conn, raw_norm)
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        normalise_skill(canonical_label).replace("+", "plus").replace("#", "sharp"),
    ).strip("-")
    if not slug:
        raise ReviewError("custom skill label must contain letters or digits")
    skill_id = f"custom:{slug}"
    conn.execute(
        text(
            "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
            "VALUES (:i, :l) ON CONFLICT (skill_id) DO NOTHING"
        ),
        {"i": skill_id, "l": canonical_label.strip()},
    )
    resolve_to_skill(conn, raw_norm, skill_id)


def dismiss(conn: Connection, raw_norm: str) -> None:
    """Dismiss an unmapped string so it is never re-queued.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If it is already mapped (only unmapped strings can be
            dismissed).
    """
    row = _lock_mapping(conn, raw_norm)
    if row.skill_id is not None:
        raise ReviewError(f"{raw_norm!r} is mapped; only unmapped strings can be dismissed")
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET review_status = 'dismissed' "
            "WHERE raw_norm = :n"
        ),
        {"n": raw_norm},
    )


def reject_embedding_match(conn: Connection, raw_norm: str) -> None:
    """Reject a wrong auto-match, returning the string to the review list.

    The rejected skill is kept as the row's candidate (a suggestion, not a
    mapping), and status `rejected` protects the row from re-mapping.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string.

    Raises:
        ReviewNotFound: If the string has no mapping row.
        ReviewError: If the row was not mapped by the embedding stage.
    """
    row = _lock_mapping(conn, raw_norm)
    if row.method != "embedding":
        raise ReviewError(f"{raw_norm!r} was not auto-mapped by embedding")
    conn.execute(
        text(
            "UPDATE silver.skill_mapping SET candidate_skill_id = skill_id, "
            "candidate_score = score, skill_id = NULL, score = NULL, "
            "method = 'none', review_status = 'rejected' WHERE raw_norm = :n"
        ),
        {"n": raw_norm},
    )
```

- [ ] **Step 4: Write the router and register it**

Create `apps/api/app/routers/skills.py`:

```python
"""Skill review endpoints (PLAN.md Step 14): the unmapped-skills list, the
embedding-match verify list, ESCO search, and the resolve / dismiss / reject
actions. All data is SHARED-zone taxonomy — a resolution applies to every
user (docs/tenancy.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import Connection, Engine

from core.skills import review

router = APIRouter()


class ReviewItemModel(BaseModel):
    """An unmapped skill string awaiting review (see `core.skills.review.ReviewItem`)."""

    raw_norm: str
    raw_example: str
    seen_in_cv: bool
    jd_job_count: int
    sample_job_group_ids: list[str]
    candidate_skill_id: str | None
    candidate_label: str | None
    candidate_score: float | None


class MatchItemModel(BaseModel):
    """An auto-mapped embedding match to verify (see `core.skills.review.MatchItem`)."""

    raw_norm: str
    raw_example: str
    skill_id: str
    skill_label: str | None
    score: float
    jd_job_count: int


class SkillOptionModel(BaseModel):
    """One skill search result (see `core.skills.review.SkillOption`)."""

    skill_id: str
    label: str
    source: str


class ResolveRequest(BaseModel):
    """Map a string to an existing skill xor a new custom skill.

    Attributes:
        raw_norm: The normalised string being resolved.
        skill_id: An existing ESCO / custom skill id.
        custom_label: A label for a new custom skill.
    """

    raw_norm: str
    skill_id: str | None = None
    custom_label: str | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> ResolveRequest:
        """Require exactly one of `skill_id` / `custom_label`.

        Returns:
            This instance, if valid.

        Raises:
            ValueError: If neither or both are given.
        """
        if (self.skill_id is None) == (self.custom_label is None):
            raise ValueError("provide exactly one of skill_id or custom_label")
        return self


class RawNormRequest(BaseModel):
    """A request naming one normalised skill string.

    Attributes:
        raw_norm: The normalised string.
    """

    raw_norm: str


def _act(engine: Engine, action: Callable[[Connection], None]) -> dict[str, str]:
    """Run a review action in one transaction, mapping errors to HTTP codes.

    Args:
        engine: The app-role engine.
        action: The review call to run on the transaction's connection.

    Returns:
        ``{"status": "ok"}``.

    Raises:
        fastapi.HTTPException: 404 for an unknown string, 422 for an
            invalid state or skill.
    """
    try:
        with engine.begin() as conn:
            action(conn)
    except review.ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except review.ReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok"}


@router.get("/skills/review", response_model=list[ReviewItemModel])
def get_review_list(
    limit: int = Query(default=50, ge=1, le=500),
    engine: Engine = Depends(get_app_db_engine),
) -> list[ReviewItemModel]:
    """List unmapped skill strings needing a decision.

    Args:
        limit: Maximum items.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Open and rejected items, most-requested first.
    """
    with engine.connect() as conn:
        items = review.list_unmapped(conn, limit=limit)
    return [ReviewItemModel(**asdict(item)) for item in items]


@router.get("/skills/review/embedding-matches", response_model=list[MatchItemModel])
def get_embedding_matches(
    limit: int = Query(default=50, ge=1, le=500),
    engine: Engine = Depends(get_app_db_engine),
) -> list[MatchItemModel]:
    """List embedding auto-matches for verification, least confident first.

    Args:
        limit: Maximum items.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Auto-mapped items with their scores.
    """
    with engine.connect() as conn:
        items = review.list_embedding_matches(conn, limit=limit)
    return [MatchItemModel(**asdict(item)) for item in items]


@router.get("/skills/search", response_model=list[SkillOptionModel])
def search_skills(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    engine: Engine = Depends(get_app_db_engine),
) -> list[SkillOptionModel]:
    """Search ESCO and custom skills by label.

    Args:
        q: The search text.
        limit: Maximum results.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Matching skills.
    """
    with engine.connect() as conn:
        options = review.search_skills(conn, q, limit=limit)
    return [SkillOptionModel(**asdict(option)) for option in options]


@router.post("/skills/review/resolve")
def post_resolve(
    request: ResolveRequest, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, str]:
    """Map a string to a skill, or to a new custom skill.

    Args:
        request: The string and its target.
        engine: Injected via `get_app_db_engine`.

    Returns:
        ``{"status": "ok"}``.
    """
    if request.skill_id is not None:
        skill_id = request.skill_id
        return _act(
            engine, lambda conn: review.resolve_to_skill(conn, request.raw_norm, skill_id)
        )
    label = request.custom_label or ""
    return _act(
        engine, lambda conn: review.resolve_to_custom(conn, request.raw_norm, label)
    )


@router.post("/skills/review/dismiss")
def post_dismiss(
    request: RawNormRequest, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, str]:
    """Dismiss an unmapped string so it is never re-queued.

    Args:
        request: The string.
        engine: Injected via `get_app_db_engine`.

    Returns:
        ``{"status": "ok"}``.
    """
    return _act(engine, lambda conn: review.dismiss(conn, request.raw_norm))


@router.post("/skills/review/reject")
def post_reject(
    request: RawNormRequest, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, str]:
    """Reject a wrong embedding auto-match.

    Args:
        request: The string.
        engine: Injected via `get_app_db_engine`.

    Returns:
        ``{"status": "ok"}``.
    """
    return _act(engine, lambda conn: review.reject_embedding_match(conn, request.raw_norm))
```

In `apps/api/app/main.py` change the import to `from app.routers import classification, cv, dedup, ingest, skills` and add `app.include_router(skills.router)` after `app.include_router(cv.router)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.integration.test_skills_router tests.test_api_health -v`
Expected: PASS (11 router tests + health)

- [ ] **Step 6: Commit**

```bash
black apps packages/core && isort apps packages/core && ruff check apps packages/core
git add packages/core/core/skills/review.py apps/api/app/routers/skills.py apps/api/app/main.py packages/core/tests/integration/skills_fixtures.py packages/core/tests/integration/test_skills_router.py
git commit -m "feat(job_search): skill review API - resolve, dismiss, reject, search (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 13: Skill Review Streamlit page

**Files:**
- Create: `apps/ui/app/pages/6_Skill_Review.py`
- Test: `packages/core/tests/test_ui_skill_review.py` (Streamlit `AppTest`, `httpx.get` faked)

**Interfaces:**
- Consumes: the Task 12 HTTP endpoints; `core.settings.get_settings().api_base_url`.
- Produces: the page (two tabs — "Unmapped", "Embedding matches — verify"); no code interface.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_ui_skill_review.py`:

```python
"""Headless render test for the Skill Review page (Streamlit AppTest)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import httpx
from streamlit.testing.v1 import AppTest

_PAGE = Path(__file__).resolve().parents[3] / "apps" / "ui" / "app" / "pages" / "6_Skill_Review.py"

_UNMAPPED = {
    "raw_norm": "zzfixture thing",
    "raw_example": "ZZFixture Thing",
    "seen_in_cv": True,
    "jd_job_count": 3,
    "sample_job_group_ids": ["j1"],
    "candidate_skill_id": "fixture-cloud",
    "candidate_label": "cloud technologies",
    "candidate_score": 0.62,
}
_MATCH = {
    "raw_norm": "zzfixture matched",
    "raw_example": "ZZFixture Matched",
    "skill_id": "fixture-python",
    "skill_label": "python",
    "score": 0.86,
    "jd_job_count": 1,
}


def _fake_get(url: str, **_kwargs) -> httpx.Response:
    request = httpx.Request("GET", url)
    if url.endswith("/skills/review/embedding-matches"):
        return httpx.Response(200, json=[_MATCH], request=request)
    if url.endswith("/skills/review"):
        return httpx.Response(200, json=[_UNMAPPED], request=request)
    return httpx.Response(200, json=[], request=request)


class TestSkillReviewPage(unittest.TestCase):
    def test_renders_both_lists_with_their_actions(self) -> None:
        with mock.patch("httpx.get", side_effect=_fake_get):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        shown = " ".join(m.value for m in app.markdown)
        self.assertIn("ZZFixture Thing", shown)
        self.assertIn("ZZFixture Matched", shown)
        labels = [b.label for b in app.button]
        self.assertIn("Accept suggestion: cloud technologies (0.62)", labels)
        self.assertIn("Dismiss", labels)
        self.assertIn("Confirm", labels)
        self.assertIn("Reject", labels)

    def test_shows_an_error_instead_of_crashing_when_the_api_is_down(self) -> None:
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertGreaterEqual(len(app.error), 1)


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.test_ui_skill_review -v`
Expected: FAIL — the page file does not exist.

- [ ] **Step 2: Write the page**

Create `apps/ui/app/pages/6_Skill_Review.py`:

```python
"""Skill review (PLAN.md Step 14) — resolve skill strings that did not map to
ESCO, and verify the ones the embedding stage mapped automatically.

A resolution is remembered as an alias, so each string is fixed once and the
fix applies to every future CV and job description. Aliases are shared across
users (docs/tenancy.md: taxonomy is a shared zone).
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Skill Review", layout="wide")
st.title("Skill Review")
st.write(
    "Skills from job descriptions and your CV that could not be matched to the "
    "ESCO vocabulary, plus the matches the system made by similarity — check "
    "those, since a wrong match is otherwise invisible."
)

_API = get_settings().api_base_url


def _get(path: str, params: dict | None = None) -> list[dict]:
    """GET a list endpoint on the API.

    Args:
        path: The endpoint path.
        params: Query parameters.

    Returns:
        The parsed JSON list.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    response = httpx.get(f"{_API}{path}", params=params, timeout=10.0)
    response.raise_for_status()
    return response.json()


def _post(path: str, payload: dict) -> bool:
    """POST an action to the API, showing any error on the page.

    Args:
        path: The endpoint path.
        payload: The JSON body.

    Returns:
        True if the action succeeded.
    """
    try:
        response = httpx.post(f"{_API}{path}", json=payload, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        st.error(exc.response.json().get("detail", str(exc)))
        return False
    except httpx.HTTPError as exc:
        st.error(f"Request failed: {exc}")
        return False
    return True


unmapped_tab, verify_tab = st.tabs(["Unmapped", "Embedding matches — verify"])

with unmapped_tab:
    try:
        unmapped = _get("/skills/review", {"limit": 25})
    except httpx.HTTPError as exc:
        st.error(f"Failed to load the review list: {exc}")
        unmapped = []
    st.caption(f"{len(unmapped)} shown, most-requested first")
    for item in unmapped:
        key = item["raw_norm"]
        with st.container(border=True):
            st.markdown(f"**{item['raw_example']}**")
            st.caption(
                f"In {item['jd_job_count']} job(s)"
                + (" · on your CV" if item["seen_in_cv"] else "")
            )
            if item["candidate_skill_id"]:
                suggestion = item["candidate_label"] or item["candidate_skill_id"]
                if st.button(
                    f"Accept suggestion: {suggestion} ({item['candidate_score']:.2f})",
                    key=f"accept-{key}",
                ):
                    if _post(
                        "/skills/review/resolve",
                        {"raw_norm": key, "skill_id": item["candidate_skill_id"]},
                    ):
                        st.rerun()
            query = st.text_input("Search ESCO / custom skills", key=f"q-{key}")
            if query:
                try:
                    options = _get("/skills/search", {"q": query, "limit": 10})
                except httpx.HTTPError as exc:
                    st.error(f"Search failed: {exc}")
                    options = []
                choices = {f"{o['label']} ({o['source']})": o["skill_id"] for o in options}
                if choices:
                    picked = st.selectbox("Match", list(choices), key=f"sel-{key}")
                    if st.button("Map to selected", key=f"map-{key}"):
                        if _post(
                            "/skills/review/resolve",
                            {"raw_norm": key, "skill_id": choices[picked]},
                        ):
                            st.rerun()
                else:
                    st.caption("No matches.")
            custom_label = st.text_input(
                "Or create a custom skill", value=item["raw_example"], key=f"c-{key}"
            )
            left, right = st.columns(2)
            if left.button("Mark as custom skill", key=f"custom-{key}"):
                if _post(
                    "/skills/review/resolve",
                    {"raw_norm": key, "custom_label": custom_label},
                ):
                    st.rerun()
            if right.button("Dismiss", key=f"dismiss-{key}"):
                if _post("/skills/review/dismiss", {"raw_norm": key}):
                    st.rerun()

with verify_tab:
    try:
        matches = _get("/skills/review/embedding-matches", {"limit": 25})
    except httpx.HTTPError as exc:
        st.error(f"Failed to load embedding matches: {exc}")
        matches = []
    st.caption(f"{len(matches)} shown, least confident first")
    for match in matches:
        key = match["raw_norm"]
        with st.container(border=True):
            st.markdown(f"**{match['raw_example']}**")
            st.write(
                f"→ {match['skill_label'] or match['skill_id']} "
                f"(similarity {match['score']:.2f}, in {match['jd_job_count']} job(s))"
            )
            left, right = st.columns(2)
            if left.button("Confirm", key=f"confirm-{key}"):
                if _post(
                    "/skills/review/resolve",
                    {"raw_norm": key, "skill_id": match["skill_id"]},
                ):
                    st.rerun()
            if right.button("Reject", key=f"reject-{key}"):
                if _post("/skills/review/reject", {"raw_norm": key}):
                    st.rerun()
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.test_ui_skill_review -v`
Expected: PASS (2 tests)

- [ ] **Step 4: Smoke-test the page against a live API (manual)**

From `job_search/`, with the venv and `.env` variables active (do not use docker compose from a worktree):

```bash
PYTHONPATH=packages/core:apps/api uvicorn app.main:app --port 8010 &
API_BASE_URL=http://localhost:8010 PYTHONPATH=packages/core streamlit run apps/ui/app/pages/6_Skill_Review.py --server.port 8511 --server.headless true &
curl -s localhost:8010/skills/review?limit=3
```

Open `http://localhost:8511`, confirm both tabs load without an error banner, then stop both processes.

- [ ] **Step 5: Commit**

```bash
black apps packages/core && isort apps packages/core && ruff check apps packages/core
git add apps/ui/app/pages/6_Skill_Review.py packages/core/tests/test_ui_skill_review.py
git commit -m "feat(job_search): Skill Review Streamlit page (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 14: Wire `skill_extraction` into the eval harness

**Files:**
- Create: `evals/golden/skill_extraction.yml`
- Modify: `packages/core/core/evals/runner.py` (predictor + registry)
- Modify: `apps/pipeline/app/cli.py` (`_EVAL_TASKS`)
- Test: `packages/core/tests/test_evals_runner_skill_extraction.py`

**Interfaces:**
- Consumes: `extract_jd_skills`, `normalise_skill`, `FakeAdapter`, the `skill_extraction` entry in `config/llm_tasks.yml` (Task 8), `field_f1`.
- Produces: `_predict_skill_extraction` registered as `_PREDICTORS["skill_extraction"]`; predicted/expected dicts are flat `{"skill:<normalised name>": "<must_have|nice_to_have>"}` so `field_f1` scores precision and recall over (skill, level) pairs with no new metric; `run-evals --task skill_extraction` works.

- [ ] **Step 1: Write the golden set (20 synthetic cases)**

Create `evals/golden/skill_extraction.yml`. Every case is invented; none is copied from a real posting. Keys are the *normalised* skill name (lowercase, per `normalise_skill`):

```yaml
cases:
  - case_id: skill_001
    input:
      description: |
        We need a Data Engineer with strong Python and SQL. You will build
        pipelines on Airflow. Nice to have: dbt, Terraform.
    expected:
      "skill:python": must_have
      "skill:sql": must_have
      "skill:airflow": must_have
      "skill:dbt": nice_to_have
      "skill:terraform": nice_to_have
  - case_id: skill_002
    input:
      description: |
        Strong Go experience is essential. Kubernetes and PostgreSQL in
        production. Bonus: Kafka.
    expected:
      "skill:go": must_have
      "skill:kubernetes": must_have
      "skill:postgresql": must_have
      "skill:kafka": nice_to_have
  - case_id: skill_003
    input:
      description: |
        Join our friendly team in London. We offer 25 days holiday and a
        great culture.
    expected: {}
  - case_id: skill_004
    input:
      description: |
        You will build interfaces with React and TypeScript. Experience with
        GraphQL is a plus.
    expected:
      "skill:react": must_have
      "skill:typescript": must_have
      "skill:graphql": nice_to_have
  - case_id: skill_005
    input:
      description: |
        Required: Python, PyTorch, and Docker for deploying models.
        Preferred: Spark.
    expected:
      "skill:python": must_have
      "skill:pytorch": must_have
      "skill:docker": must_have
      "skill:spark": nice_to_have
  - case_id: skill_006
    input:
      description: |
        Own our CI/CD pipelines. Terraform and AWS are essential. Ideally you
        know Ansible.
    expected:
      "skill:ci/cd": must_have
      "skill:terraform": must_have
      "skill:aws": must_have
      "skill:ansible": nice_to_have
  - case_id: skill_007
    input:
      description: |
        Write SQL daily and build dashboards in Tableau. Knowledge of R would
        be an asset.
    expected:
      "skill:sql": must_have
      "skill:tableau": must_have
      "skill:r": nice_to_have
  - case_id: skill_008
    input:
      description: |
        Build our mobile app in Swift. Familiarity with Kotlin is desirable.
    expected:
      "skill:swift": must_have
      "skill:kotlin": nice_to_have
  - case_id: skill_009
    input:
      description: |
        Day to day you will write Java services, tune PostgreSQL queries and
        run everything on Kubernetes.
    expected:
      "skill:java": must_have
      "skill:postgresql": must_have
      "skill:kubernetes": must_have
  - case_id: skill_010
    input:
      description: |
        Requirements:
        - Python
        - Git

        Nice to have:
        - Rust
    expected:
      "skill:python": must_have
      "skill:git": must_have
      "skill:rust": nice_to_have
  - case_id: skill_011
    input:
      description: |
        Must know C++ and .NET. Node.js is a plus.
    expected:
      "skill:c++": must_have
      "skill:.net": must_have
      "skill:node.js": nice_to_have
  - case_id: skill_012
    input:
      description: |
        Salary 60k. Hybrid, two days in the office. You must be a great
        communicator. Experience with Snowflake is required.
    expected:
      "skill:snowflake": must_have
  - case_id: skill_013
    input:
      description: |
        Required: SQL and Python. Bonus points for knowledge of dbt and Looker.
    expected:
      "skill:sql": must_have
      "skill:python": must_have
      "skill:dbt": nice_to_have
      "skill:looker": nice_to_have
  - case_id: skill_014
    input:
      description: |
        Statistics and Python are core to this role. Experience with A/B
        testing is preferred.
    expected:
      "skill:statistics": must_have
      "skill:python": must_have
      "skill:a/b testing": nice_to_have
  - case_id: skill_015
    input:
      description: |
        Experience with penetration testing and Burp Suite is required. A CISSP
        certification is desirable.
    expected:
      "skill:penetration testing": must_have
      "skill:burp suite": must_have
      "skill:cissp": nice_to_have
  - case_id: skill_016
    input:
      description: |
        We run on Google Cloud Platform using BigQuery. Terraform experience
        would be an asset.
    expected:
      "skill:google cloud platform": must_have
      "skill:bigquery": must_have
      "skill:terraform": nice_to_have
  - case_id: skill_017
    input:
      description: |
        Write automated tests in Cypress and Jest. Knowledge of Playwright is
        a plus.
    expected:
      "skill:cypress": must_have
      "skill:jest": must_have
      "skill:playwright": nice_to_have
  - case_id: skill_018
    input:
      description: |
        Looking for an experienced Excel power user.
    expected:
      "skill:excel": must_have
  - case_id: skill_019
    input:
      description: |
        Must have: Scala and Spark. Nice to have: Databricks and Delta Lake.
    expected:
      "skill:scala": must_have
      "skill:spark": must_have
      "skill:databricks": nice_to_have
      "skill:delta lake": nice_to_have
  - case_id: skill_020
    input:
      description: |
        The role reports to the Head of Operations. Location: Manchester.
        Full-time.
    expected: {}
```

- [ ] **Step 2: Write the failing test**

Create `packages/core/tests/test_evals_runner_skill_extraction.py`:

```python
"""skill_extraction's golden set loads and the predictor round-trips it.

Unit-level, fake adapter only (mirrors test_evals_runner_cv_extraction.py).
"""

from __future__ import annotations

import json
import unittest

from core.evals.golden import load_golden_set
from core.evals.metrics import field_f1
from core.evals.runner import _PREDICTORS, MINIMUM_GOLDEN_SET_SIZE
from core.skills.normalise import normalise_skill
from tests.skills_fakes import FakeAdapter


def _reply(*skills: tuple[str, str]) -> str:
    return json.dumps(
        {"skills": [{"skill": s, "requirement_level": lvl} for s, lvl in skills]}
    )


def _predict(case, adapter):
    return _PREDICTORS["skill_extraction"](
        case,
        provider="ollama",
        model="test-model",
        prompt_family="local",
        adapters={"ollama": adapter},
    )


class TestSkillExtractionGoldenSet(unittest.TestCase):
    def test_has_at_least_the_minimum_case_count(self) -> None:
        self.assertGreaterEqual(
            len(load_golden_set("skill_extraction")), MINIMUM_GOLDEN_SET_SIZE
        )

    def test_expected_keys_are_normalised_and_levels_are_valid(self) -> None:
        for case in load_golden_set("skill_extraction"):
            for key, level in case.expected.items():
                self.assertTrue(key.startswith("skill:"), (case.case_id, key))
                name = key[len("skill:") :]
                self.assertEqual(name, normalise_skill(name), (case.case_id, key))
                self.assertIn(level, {"must_have", "nice_to_have"}, case.case_id)

    def test_includes_cases_that_name_no_skills(self) -> None:
        empty = [c for c in load_golden_set("skill_extraction") if not c.expected]
        self.assertGreaterEqual(len(empty), 2)

    def test_case_ids_are_unique(self) -> None:
        ids = [c.case_id for c in load_golden_set("skill_extraction")]
        self.assertEqual(len(ids), len(set(ids)))


class TestSkillExtractionPredictor(unittest.TestCase):
    def test_is_registered(self) -> None:
        self.assertIn("skill_extraction", _PREDICTORS)

    def test_flattens_extraction_to_skill_level_pairs_and_reports_the_version(self) -> None:
        case = load_golden_set("skill_extraction")[0]
        adapter = FakeAdapter(_reply(("Python", "must_have"), ("dbt", "nice_to_have")))
        predicted, prompt_version = _predict(case, adapter)
        self.assertEqual(
            predicted, {"skill:python": "must_have", "skill:dbt": "nice_to_have"}
        )
        self.assertEqual(prompt_version, "local.v1")

    def test_a_perfect_prediction_scores_one_under_field_f1(self) -> None:
        case = next(c for c in load_golden_set("skill_extraction") if c.case_id == "skill_001")
        adapter = FakeAdapter(
            _reply(
                ("Python", "must_have"),
                ("SQL", "must_have"),
                ("Airflow", "must_have"),
                ("dbt", "nice_to_have"),
                ("Terraform", "nice_to_have"),
            )
        )
        predicted, _version = _predict(case, adapter)
        self.assertEqual(field_f1(predicted, case.expected), 1.0)

    def test_a_malformed_response_falls_back_to_empty(self) -> None:
        case = load_golden_set("skill_extraction")[0]
        self.assertEqual(_predict(case, FakeAdapter("not json")), ({}, None))


if __name__ == "__main__":
    unittest.main()
```

Run: `cd packages/core && python -m unittest tests.test_evals_runner_skill_extraction -v`
Expected: FAIL — `KeyError: 'skill_extraction'` (predictor not registered).

- [ ] **Step 3: Add the predictor and register it**

In `packages/core/core/evals/runner.py`, add after `_predict_cv_extraction`:

```python
def _predict_skill_extraction(
    case: GoldenCase,
    *,
    provider: str,
    model: str,
    prompt_family: str,
    adapters: dict[str, LLMAdapter],
) -> tuple[dict[str, object], str | None]:
    """Predict a `skill_extraction` case's skills and levels via the LLM.

    Args:
        case: The golden case to predict — `case.input["description"]` is
            the job description to extract from.
        provider: The provider to force `extract_jd_skills` to use.
        model: The model to use with `provider`.
        prompt_family: The prompt variant to load for `provider`.
        adapters: Every available LLM adapter, keyed by provider name.

    Returns:
        A tuple of (`{"skill:<normalised name>": "<requirement level>"}` —
        one flat key per skill, so `field_f1` scores (skill, level) pairs —
        and the `prompt_version` `extract_jd_skills` used). Falls back to
        `({}, None)` if extraction raises `ValueError`, so one malformed
        response does not crash a whole eval run.
    """
    from core.skills.jd_extract import extract_jd_skills
    from core.skills.normalise import normalise_skill

    try:
        extraction = extract_jd_skills(
            str(case.input["description"]),
            adapters=adapters,
            provider=provider,
            model=model,
            prompt_family=prompt_family,
        )
    except ValueError:
        return {}, None
    predicted: dict[str, object] = {
        f"skill:{normalise_skill(skill.skill)}": skill.requirement_level
        for skill in extraction.skills
    }
    return predicted, extraction.prompt_version
```

and extend the registry:

```python
_PREDICTORS: dict[str, _Predictor] = {
    "job_categorisation": _predict_job_categorisation,
    "cv_extraction": _predict_cv_extraction,
    "skill_extraction": _predict_skill_extraction,
}
```

In `apps/pipeline/app/cli.py` change `_EVAL_TASKS = ["job_categorisation", "cv_extraction"]` to `_EVAL_TASKS = ["job_categorisation", "cv_extraction", "skill_extraction"]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/core && python -m unittest tests.test_evals_runner_skill_extraction tests.test_pipeline_cli_run_evals tests.test_evals_runner_cv_extraction tests.test_evals_golden -v`
Expected: PASS

- [ ] **Step 5: Run the real eval against the local model**

Ollama must be up with `llama3.1:8b`. From `job_search/`: `python -m apps.pipeline.app.cli run-evals --task skill_extraction --provider local`
Expected: a line like `skill_extraction (ollama): score=0.xxx (n=20)` — a real score, not `insufficient_data`. Record the score in the commit message. A low first score is information (tune `prompts/skill_extraction/local.v1.md` and bump to `v2`, never edit `v1` in place once it has produced stored rows); it does not block this task.

- [ ] **Step 6: Commit**

```bash
black apps packages/core && isort apps packages/core && ruff check apps packages/core
git add evals/golden/skill_extraction.yml packages/core/core/evals/runner.py packages/core/tests/test_evals_runner_skill_extraction.py apps/pipeline/app/cli.py
git commit -m "feat(job_search): wire skill_extraction into the eval harness with a 20-case golden set (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 15: Docs, gitignore, compose mount, and end-to-end verification

**Files:**
- Modify: `.gitignore`, `docker-compose.yml`
- Create: `data/esco/.gitkeep`, `docs/esco.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the operator workflow documented in `docs/esco.md`; a verified branch.

- [ ] **Step 1: Keep ESCO data out of git**

Append to `.gitignore`:

```
# ESCO release CSVs (PLAN.md Step 14) — downloaded by hand from the ESCO
# portal, never committed. The directory is kept via .gitkeep.
data/esco/*
!data/esco/.gitkeep
```

Create the empty file `data/esco/.gitkeep`.

- [ ] **Step 2: Mount the ESCO directory into the pipeline container**

In `docker-compose.yml`, in the `pipeline` service, change

```yaml
    profiles: [cli]
    volumes:
      - ./apps/pipeline/app:/app/apps/pipeline/app
```

to

```yaml
    profiles: [cli]
    volumes:
      - ./apps/pipeline/app:/app/apps/pipeline/app
      - ./data/esco:/data/esco:ro
```

- [ ] **Step 3: Write the operator guide**

Create `docs/esco.md`:

```markdown
# ESCO skill normalisation (Step 14)

CV skills and job-description skills are mapped onto one vocabulary: the
[ESCO](https://esco.ec.europa.eu) skills taxonomy, plus a small set of
`custom:` skills for tools ESCO lacks. Design:
`docs/superpowers/specs/2026-09-19-step14-esco-skill-normalisation-design.md`.

ESCO is published by the European Commission. Check its current reuse terms
and attribution requirements on the ESCO portal before redistributing
anything derived from it. The release files are never committed here
(`data/esco/*` is gitignored).

## One-off setup

1. Download the ESCO **English CSV** release from the ESCO portal and unzip
   it. Copy `skills_en.csv`, `occupations_en.csv` and
   `occupationSkillRelations_en.csv` into `data/esco/`.
2. `alembic -c db/alembic.ini upgrade head` (migrations 0022, 0023).
3. Load, then embed (about 14k Ollama calls, resumable — re-run if interrupted):

   ```bash
   python -m apps.pipeline.app.cli load-esco data/esco
   python -m apps.pipeline.app.cli embed-esco
   ```

   In Docker: `docker compose run --rm pipeline load-esco /data/esco`.

If a future release renames a CSV column, `load-esco` stops and names the
missing column rather than loading garbage.

## Routine run (after new jobs land and are deduplicated)

```bash
python -m apps.pipeline.app.cli extract-job-skills   # local LLM; slow on CPU
python -m apps.pipeline.app.cli map-skills           # seed aliases, then map new strings
(cd dbt && dbt run --select silver__skill silver__bridge_job_skill)
python -m apps.pipeline.app.cli map-cv-skills --user-id <uuid>   # after a CV upload/edit
```

`map-cv-skills` writes a new CV truth-base version labelled "ESCO skill
normalisation" (only if something changed), so it is traceable and reversible
in the CV Editor's history.

## Reviewing what did not map

Open the **Skill Review** page. Two tabs:

- **Unmapped** — strings that matched nothing. Accept the suggestion, search
  ESCO for the right skill, mark it as a custom skill, or dismiss it.
- **Embedding matches — verify** — strings matched by similarity. Confirm or
  reject each; a wrong match is otherwise invisible.

A resolution becomes an alias, so it applies to every future CV and JD
string that normalises the same way. Aliases are shared across users.

## Tuning the similarity threshold

`core.skills.mapper.EMBEDDING_ACCEPT_COSINE` (0.85) is a starting value, not
an empirically tuned one. After the first real run, hand-check about 100
entries on the verify tab; if too many are wrong raise it, if the Unmapped
tab is swamped with obvious matches lower it, then run
`map-skills --remap-unresolved`. Human decisions (confirmed, rejected,
resolved, dismissed) are never re-mapped.

## Seed aliases

`config/skill_aliases.yml` is the committed starting set (GCP, AWS, Kubernetes,
PostgreSQL). Once real ESCO data is loaded, check whether ESCO already has an
equivalent skill for each `custom:` entry and, if so, re-point the entry at the
ESCO skill id so the same skill does not exist under two ids.

## Changing the embedding model

`esco.skill_embedding` records the model per row and `map-skills` refuses to
run if it differs from `EMBEDDING_MODEL`. A different model (or dimension, which
needs a migration) means re-running `embed-esco`; no source data is lost.
```

- [ ] **Step 4: Run the full new-feature test set and the whole unit suite**

From `packages/core`:

```bash
python -m unittest discover -s tests -t . 2>&1 | tail -15
```

Expected: `OK` (some tests report as skipped when Ollama/Anthropic keys are absent — that is normal; note the skipped count). No failures or errors. If a pre-existing test fails, run it on `main` to tell a regression from an existing failure before changing anything.

- [ ] **Step 5: Lint, format and type-check**

From `job_search/`:

```bash
ruff check . && isort --check-only . && black --check .
cd packages/core && mypy --config-file ../../pyproject.toml -p core.skills
```

Expected: all clean (`Success: no issues found` for mypy). Fix any finding in the file that caused it (`black .` / `isort .` auto-fix formatting).

- [ ] **Step 6: Verify the migrations are reversible**

Only if the new tables hold no real data yet (`SELECT count(*) FROM esco.skill;` returns 0 and `silver.skill_mapping` is empty apart from fixtures):

```bash
alembic -c db/alembic.ini downgrade 0021 && alembic -c db/alembic.ini upgrade head
```

Expected: both directions succeed, ending at `0023 (head)`. Re-run `cd packages/core && python -m unittest tests.integration.test_esco_schema tests.integration.test_skills_silver_schema -v` afterwards.

- [ ] **Step 7: End-to-end smoke with the real ESCO release (needs the CSVs from Step 1's setup)**

Skip and say so in the PR if the release is not downloaded yet. Otherwise:

1. `load-esco data/esco` — confirm the counts look like the release (roughly 14k skills, 3k occupations) and there are few `skipped_relations`.
2. `embed-esco` — let it finish (resumable).
3. Seed check: for each `custom:` entry in `config/skill_aliases.yml`, look for an existing ESCO skill (`curl 'localhost:8000/skills/search?q=kubernetes'` against a running API, or SQL on `esco.skill_label`). Re-point the entry at the ESCO id if one exists.
4. `extract-job-skills --limit 5`, then `map-skills`, then `dbt run --select silver__skill silver__bridge_job_skill` and `dbt test --select silver__skill silver__bridge_job_skill`.
5. Open Skill Review; hand-check the verify tab and set `EMBEDDING_ACCEPT_COSINE` per `docs/esco.md`.
6. Confirm the acceptance criterion on real data: `GCP`, `Google Cloud` and `Google Cloud Platform` all appear in `silver.skill_mapping` with the same `skill_id`.

- [ ] **Step 8: Commit**

```bash
git add .gitignore docker-compose.yml data/esco/.gitkeep docs/esco.md
git commit -m "docs(job_search): ESCO operator guide, gitignore and compose mount (JOB-215)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

After this task, follow superpowers:finishing-a-development-branch (the repo's `pr` / `commit-push-pr` skills open the PR; the PR merge event fires `jira_sync_kit complete-story`).

---

## Spec coverage

| Spec section | Task |
|---|---|
| Decisions 1 (ESCO bulk CSV) | 3, 15 |
| Decision 2 (alias → label → embedding → review, no LLM in mapping) | 6, 7 |
| Decision 3 (survivors only) | 9 |
| Decision 4 (table + Streamlit resolve page; alias persistence) | 4, 12, 13 |
| Decision 5 (persisted embeddings, no ANN index, model recorded) | 2, 5, 7 (model check) |
| Data model — `esco` schema | 2 |
| Data model — `silver` tables + invariants + grants | 4 |
| String normalisation | 1 |
| Mapper + threshold + `--remap-unresolved` | 7 |
| Seed aliases | 6 |
| JD extraction (task, prompt, level rule, chunking, merge, CLI) | 8, 9 |
| CV side | 10 |
| `silver__bridge_job_skill` + dbt tests | 11 |
| Review list (API, resolve/dismiss/reject, UI, verify tab) | 12, 13 |
| Eval harness wiring | 8 (config), 14 |
| Loader (`load-esco`, `embed-esco`, gitignore, attribution note) | 3, 5, 15 |
| Testing incl. the GCP acceptance test | 7 (`TestGcpAcceptance`), and each task |
| Done when | 7, 11, 14, 15 (Step 7) |
