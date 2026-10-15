# Step 19a — Persisted company intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Research a company once (via Crawl4AI), keep the result versioned and fresh-dated, show it with its top-5 competitors and a per-job competitive angle, and let Steps 19 and 20a reuse it.

**Architecture:** A fixed pipeline in `packages/core/core/company_intel/` — resolve the end client (LLM + manual override) → crawl a bounded, cached page set (Crawl4AI as a Docker Compose service) → extract cited facts (LLM + a fabrication guard) → append a version to `intel.company_intel`. A second LLM task writes a per-job competitive angle. One service module exposes `get_or_refresh_intel` and `get_or_create_angle`; a CLI command, three API endpoints and a Streamlit page call them.

**Tech Stack:** Python 3.11, SQLAlchemy 2 + Alembic (Postgres), pydantic v2, httpx, FastAPI, Streamlit, `unittest`, Crawl4AI `unclecode/crawl4ai:0.9.4` over HTTP, Claude via the existing LLM gateway.

**Spec:** [docs/superpowers/specs/2026-10-14-step19a-company-intel-design.md](../specs/2026-10-14-step19a-company-intel-design.md)

## Global Constraints

- Schema `intel` (shared across users, **no RLS**); tables `intel.company_intel` (append-only), `intel.crawl_cache`, `intel.job_company`, `intel.job_competitive_angle` (append-only). The app role `job_search_app` gets only the grants each table needs.
- `company_key` = md5 hex of `normalise_company(name)`, identical to `gold.dim_company.company_key` (`dbt_utils.generate_surrogate_key` over the normalised name). No foreign key to `dim_company`.
- Config `config/company_intel.yml`: `refresh_after_days: 90`, `crawl_cache_days: 7`, `max_pages: 8`.
- At most **5** competitors. `basis` is `site` (needs a crawled `source_url`) or `model_knowledge` (no URL; always shown as unverified). Facts citing a URL that was not crawled are dropped.
- The competitive angle is **analysis, not fact**: shown with the label `analysis — not verified`; model-knowledge competitors with `model knowledge — unverified`.
- A manual company resolution is never overwritten by the LLM.
- If every page fails, nothing is written and the previous version stays current. Pages that load but yield no facts are stored as `no_content`.
- Crawler: same registered domain only, `robots.txt` respected (`check_robots_txt`), at most `max_pages` pages, a cached page (fetched within `crawl_cache_days`) is never re-crawled.
- Crawl4AI runs as the `crawl4ai` Compose service (image `unclecode/crawl4ai:0.9.4`, port 11235, `shm_size: 1gb`, profile `research` so it is not started by default); the app reads `CRAWL4AI_URL` (default `http://crawl4ai:11235`). The `api` and `pipeline` images get no browser.
- LLM tasks `company_resolution`, `company_intel_extraction`, `competitive_angle`: Claude (`claude-sonnet-5`, prompt family `claude`), prompts at `prompts/<task>/claude.v1.md`; literal braces in prompt files are doubled (`{{ }}`); page text and other data are passed as format *values*, never spliced into the template.
- Code rules (`.claude/rules/python-style.md`): Python 3.11, black/isort/ruff (88 cols), `from __future__ import annotations`, type hints, Google-style docstrings with `Args`/`Returns`/`Raises` on every function and class (nested, private and test helpers too).
- Tests (`.claude/rules/python-testing.md`): `unittest`, behaviour-named methods, one test file per module under `packages/core/tests/`. DB tests are integration tests on the real dev Postgres (no DB mocking), use `zzfixture`-prefixed ids and remove their rows.
- Run **unit** tests on the host from `packages/core`: `../../venv/bin/python -m unittest tests.<module> -v`. Run **DB / CLI / UI** tests in the container (the host cannot resolve the `postgres` host name): `cd job_search && docker compose up -d postgres && docker compose run --rm --entrypoint python pipeline -m unittest tests.<module> -v` (use service `ui` for the Streamlit page test).
- Format only the files you touched, from `packages/core`: `../../../.superpowers/fmt.sh <paths>` (black, isort, ruff). Never `isort .`.
- Apply migrations from `job_search`: `set -a; . ./.env; set +a; DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}" venv/bin/alembic -c db/alembic.ini upgrade head`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Work on branch `feat/step-19a-company-intel`.

## Review Focus

1. A company name with a legal suffix or odd casing (`"Stripe, Inc."`, `"ACME LTD"`) must give the same key as `gold.dim_company` (Task 2).
2. Crawl4AI answering with `markdown` as a string **or** as `{raw_markdown, fit_markdown}`, links as dicts **or** strings, an empty result list, or a non-200: always a `Page`, never an exception (Task 4).
3. An agency posting that names no end client: no crawl, no extraction call, outcome `end_client_unknown` (Task 8).
4. A manual end client/website must survive a later automatic resolution of the same job (Tasks 3 and 6).
5. Competitor rules: more than 5 → 5; `model_knowledge` with a URL → URL cleared; `site` citing an uncrawled URL → dropped (Task 7). Page text containing `{}` must not break prompt formatting (Task 7).

---

### Task 1: Migration — the `intel` schema

**Files:**
- Create: `db/migrations/versions/0037_create_intel_schema.py`
- Create: `packages/core/tests/integration/test_intel_schema.py`
- Modify: `docs/superpowers/specs/2026-10-14-step19a-company-intel-design.md` (two small corrections, Step 5)

**Interfaces:**
- Produces: tables `intel.company_intel(id, company_key, version, display_name, status, researched_at, content, pages, resolved_from_job, model, prompt_version, usage)` unique `(company_key, version)`; `intel.crawl_cache(url, fetched_on, status, markdown, links, error)` pk `(url, fetched_on)`; `intel.job_company(job_group_id pk, posted_company, poster_kind, end_client, website_url, method, resolved_at)`; `intel.job_competitive_angle(id, job_group_id, company_key, intel_version, content, model, prompt_version, usage, created_at)`.

- [ ] **Step 1: Write the failing schema test**

```python
"""Schema tests for the intel tables (migration 0037)."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

_KEY = "zzfixture-intel-key"


class TestIntelSchema(unittest.TestCase):
    """The intel tables exist with the constraints and grants the spec needs."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove every fixture row."""
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM intel.company_intel WHERE company_key = :k"),
                {"k": _KEY},
            )
            conn.execute(
                text("DELETE FROM intel.crawl_cache WHERE url LIKE 'https://zzfixture%'")
            )
            conn.execute(
                text("DELETE FROM intel.job_company WHERE job_group_id LIKE 'zzfixture%'")
            )
            conn.execute(
                text(
                    "DELETE FROM intel.job_competitive_angle "
                    "WHERE job_group_id LIKE 'zzfixture%'"
                )
            )

    def _insert_intel(self, conn, version: int) -> None:
        """Insert one company_intel row as the app role.

        Args:
            conn: An open connection.
            version: The version number to insert.
        """
        conn.execute(
            text(
                "INSERT INTO intel.company_intel "
                "(company_key, version, display_name, status, content, pages, usage) "
                "VALUES (:k, :v, 'Zz Co', 'ok', '{}'::jsonb, '[]'::jsonb, '{}'::jsonb)"
            ),
            {"k": _KEY, "v": version},
        )

    def test_a_version_is_unique_per_company(self) -> None:
        with self.app_engine.begin() as conn:
            self._insert_intel(conn, 1)
        with self.assertRaises(IntegrityError):
            with self.app_engine.begin() as conn:
                self._insert_intel(conn, 1)

    def test_intel_is_append_only_for_the_app_role(self) -> None:
        with self.app_engine.begin() as conn:
            self._insert_intel(conn, 1)
        with self.assertRaises(ProgrammingError):
            with self.app_engine.begin() as conn:
                conn.execute(
                    text("UPDATE intel.company_intel SET display_name = 'x'")
                )

    def test_status_must_be_a_known_value(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.app_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO intel.company_intel "
                        "(company_key, version, display_name, status, content, pages, "
                        "usage) VALUES (:k, 1, 'Zz', 'bogus', '{}'::jsonb, "
                        "'[]'::jsonb, '{}'::jsonb)"
                    ),
                    {"k": _KEY},
                )

    def test_the_cache_keeps_one_row_per_url_and_day(self) -> None:
        insert = text(
            "INSERT INTO intel.crawl_cache (url, fetched_on, status, markdown) "
            "VALUES ('https://zzfixture.example/', current_date, 'ok', 'x')"
        )
        with self.app_engine.begin() as conn:
            conn.execute(insert)
        with self.assertRaises(IntegrityError):
            with self.app_engine.begin() as conn:
                conn.execute(insert)

    def test_the_resolution_method_must_be_known_and_is_updatable(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.app_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO intel.job_company (job_group_id, poster_kind, "
                        "method) VALUES ('zzfixture-j', 'employer', 'bogus')"
                    )
                )
        with self.app_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO intel.job_company (job_group_id, poster_kind, method) "
                    "VALUES ('zzfixture-j', 'employer', 'employer')"
                )
            )
            conn.execute(
                text(
                    "UPDATE intel.job_company SET method = 'manual' "
                    "WHERE job_group_id = 'zzfixture-j'"
                )
            )

    def test_angles_can_be_appended_but_not_changed(self) -> None:
        with self.app_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO intel.job_competitive_angle (job_group_id, "
                    "company_key, intel_version, content, usage) VALUES "
                    "('zzfixture-j', :k, 1, '{}'::jsonb, '{}'::jsonb)"
                ),
                {"k": _KEY},
            )
        with self.assertRaises(ProgrammingError):
            with self.app_engine.begin() as conn:
                conn.execute(text("UPDATE intel.job_competitive_angle SET model = 'x'"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `job_search`): `docker compose up -d postgres && docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_intel_schema -v`
Expected: errors such as `relation "intel.company_intel" does not exist` (the `intel` schema is not created yet).

- [ ] **Step 3: Write the migration**

```python
"""create intel schema (PLAN.md Step 19a)

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-14

Backs docs/superpowers/specs/2026-10-14-step19a-company-intel-design.md.
Company intelligence is shared data (no RLS), like gold.dim_job:

- intel.company_intel: append-only, one row per company per version.
- intel.crawl_cache: crawled page markdown, one row per URL per day.
- intel.job_company: which company a job is really for (employer / agency
  end client), automatic or a manual override.
- intel.job_competitive_angle: append-only per-job analysis, pinned to the
  intel version it was written from.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def _now() -> sa.TextClause:
    """Return the `now()` server default.

    Returns:
        A SQL text clause.
    """
    return sa.text("now()")


def upgrade() -> None:
    """Create the intel schema, its four tables and the app-role grants."""
    op.execute("CREATE SCHEMA IF NOT EXISTS intel")
    op.execute("GRANT USAGE ON SCHEMA intel TO job_search_app")

    op.create_table(
        "company_intel",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "researched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_now(),
        ),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("pages", JSONB(), nullable=False),
        sa.Column("resolved_from_job", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("usage", JSONB(), nullable=False),
        sa.UniqueConstraint("company_key", "version", name="uq_company_intel_version"),
        sa.CheckConstraint(
            "status IN ('ok', 'no_content', 'failed')", name="ck_company_intel_status"
        ),
        schema="intel",
    )
    op.execute("GRANT SELECT, INSERT ON intel.company_intel TO job_search_app")

    op.create_table(
        "crawl_cache",
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("fetched_on", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("links", JSONB(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("url", "fetched_on", name="pk_crawl_cache"),
        schema="intel",
    )
    op.execute("GRANT SELECT, INSERT ON intel.crawl_cache TO job_search_app")

    op.create_table(
        "job_company",
        sa.Column("job_group_id", sa.Text(), primary_key=True),
        sa.Column("posted_company", sa.Text(), nullable=True),
        sa.Column("poster_kind", sa.Text(), nullable=False),
        sa.Column("end_client", sa.Text(), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_now(),
        ),
        sa.CheckConstraint(
            "poster_kind IN ('employer', 'agency', 'unknown')",
            name="ck_job_company_poster_kind",
        ),
        sa.CheckConstraint(
            "method IN ('employer', 'llm', 'manual')", name="ck_job_company_method"
        ),
        schema="intel",
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON intel.job_company TO job_search_app")

    op.create_table(
        "job_competitive_angle",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("intel_version", sa.Integer(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("usage", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_now(),
        ),
        schema="intel",
    )
    op.create_index(
        "ix_job_competitive_angle_job",
        "job_competitive_angle",
        ["job_group_id", "created_at"],
        schema="intel",
    )
    op.execute("GRANT SELECT, INSERT ON intel.job_competitive_angle TO job_search_app")


def downgrade() -> None:
    """Drop the four tables and the schema."""
    op.drop_index(
        "ix_job_competitive_angle_job",
        table_name="job_competitive_angle",
        schema="intel",
    )
    op.drop_table("job_competitive_angle", schema="intel")
    op.drop_table("job_company", schema="intel")
    op.drop_table("crawl_cache", schema="intel")
    op.drop_table("company_intel", schema="intel")
    op.execute("DROP SCHEMA IF EXISTS intel")
```

- [ ] **Step 4: Apply it and run the test**

Run (from `job_search`):
```bash
set -a; . ./.env; set +a
DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}" venv/bin/alembic -c db/alembic.ini upgrade head
docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_intel_schema -v
```
Expected: `Running upgrade 0036 -> 0037`, then `OK` (6 tests).

- [ ] **Step 5: Correct the spec (it now has `links`/`error` on the cache and no `--user-id`)**

In the spec file, change the `intel.crawl_cache` bullet to `url text`, `fetched_on date`, `status text`, `markdown text`, `links jsonb` (the page's internal links, needed to pick pages from a cached homepage), `error text`; and in the CLI bullet change `research-company --user-id … --job-group-id …` to `research-company --job-group-id …` (intel is shared, so no user).

- [ ] **Step 6: Commit**

```bash
git add db/migrations/versions/0037_create_intel_schema.py packages/core/tests/integration/test_intel_schema.py docs/superpowers/specs/2026-10-14-step19a-company-intel-design.md
git commit -m "feat(job_search): intel schema for persisted company intelligence

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Keys, models, config and the LLM task runner

**Files:**
- Create: `packages/core/core/company_intel/__init__.py` (empty docstring module)
- Create: `packages/core/core/company_intel/keys.py`
- Create: `packages/core/core/company_intel/schema.py`
- Create: `packages/core/core/company_intel/config.py`
- Create: `packages/core/core/company_intel/llm.py`
- Create: `config/company_intel.yml`
- Modify: `config/llm_tasks.yml` (three tasks)
- Create: `packages/core/tests/company_intel_fixtures.py`
- Test: `packages/core/tests/test_company_intel_keys.py`, `packages/core/tests/test_company_intel_config.py`, `packages/core/tests/test_company_intel_llm.py`, `packages/core/tests/integration/test_company_intel_key_matches_dim_company.py`

**Interfaces:**
- Produces:
  - `keys.company_key(name: str | None) -> str | None`
  - `schema`: `IntelFact(text, source_url)`, `Competitor(name, why, basis, source_url)`, `IntelContent` (lists `business_model`, `size_signals`, `tech_stack`, `recent_announcements`, `notes`, `competitors`; method `is_empty() -> bool`), `Resolution` (frozen dataclass: `job_group_id, posted_company, poster_kind, end_client, website_url, method`), `StoredIntel` (frozen dataclass: `company_key, version, display_name, status, researched_at, content, pages, resolved_from_job, model, prompt_version, usage`), `AngleAdvantage(competitor, point)`, `AngleContent(transformation, advantages, caveats)`, `StoredAngle` (frozen dataclass: `job_group_id, company_key, intel_version, content, model, prompt_version, usage, created_at`).
  - `config.CompanyIntelConfig(refresh_after_days=90, crawl_cache_days=7, max_pages=8)`, `config.load_config(path: Path | None = None) -> CompanyIntelConfig`
  - `llm.TaskRun(text, model, prompt_version, record)`, `llm.LlmTaskError`, `llm.run_task(task, version, variables, adapters, *, config_path=None, max_tokens=4096) -> TaskRun`, `llm.total_usage(calls: list[dict]) -> dict`
  - test helpers in `tests/company_intel_fixtures.py`: `FakeAdapter(replies)`, `FakeFetcher(pages)`, `make_job(...)`, `make_source(...)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_company_intel_keys.py`:
```python
"""Unit tests for company_key."""

from __future__ import annotations

import hashlib
import unittest

from core.company_intel.keys import company_key


class TestCompanyKey(unittest.TestCase):
    """company_key follows normalise_company then md5."""

    def test_it_is_the_md5_of_the_normalised_name(self) -> None:
        self.assertEqual(
            company_key("Stripe, Inc."), hashlib.md5(b"Stripe").hexdigest()
        )

    def test_legal_suffix_and_casing_do_not_change_the_key(self) -> None:
        self.assertEqual(company_key("ACME LTD"), company_key("Acme"))
        self.assertEqual(company_key("Acme Limited"), company_key("Acme"))

    def test_known_aliases_share_a_key(self) -> None:
        self.assertEqual(company_key("Facebook"), company_key("Meta"))

    def test_no_name_gives_no_key(self) -> None:
        self.assertIsNone(company_key(None))
        self.assertIsNone(company_key("   "))


if __name__ == "__main__":
    unittest.main()
```

`tests/integration/test_company_intel_key_matches_dim_company.py`:
```python
"""company_key must equal gold.dim_company.company_key."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine

from core.company_intel.keys import company_key


class TestKeyMatchesDimCompany(unittest.TestCase):
    """The Python key joins to the dbt key."""

    def test_the_first_hundred_companies_match(self) -> None:
        engine = live_app_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT company_key, normalised_company FROM gold.dim_company "
                    "ORDER BY company_key LIMIT 100"
                )
            ).all()
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertEqual(company_key(row.normalised_company), row.company_key)


if __name__ == "__main__":
    unittest.main()
```

`tests/test_company_intel_config.py`:
```python
"""Unit tests for the company-intel config."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.company_intel.config import CompanyIntelConfig, load_config


class TestLoadConfig(unittest.TestCase):
    """load_config reads the YAML, with defaults for missing keys."""

    def test_the_shipped_file_has_the_spec_values(self) -> None:
        self.assertEqual(load_config(), CompanyIntelConfig(90, 7, 8))

    def test_missing_keys_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yml"
            path.write_text("refresh_after_days: 30\n")
            self.assertEqual(load_config(path), CompanyIntelConfig(30, 7, 8))

    def test_a_non_positive_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yml"
            path.write_text("max_pages: 0\n")
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
```

`tests/company_intel_fixtures.py`:
```python
"""Shared fakes and builders for the company-intel tests."""

from __future__ import annotations

from collections.abc import Sequence

from core.company_intel.fetch import Page
from core.llm.types import LLMResponse
from core.tailoring.schema import JobContext, JobSkill, JobSource


class FakeAdapter:
    """An LLM adapter that returns scripted replies and records prompts.

    Attributes:
        prompts: Every prompt received, in order.
    """

    def __init__(self, replies: Sequence[str]) -> None:
        """Script the replies.

        Args:
            replies: The reply texts, returned in order (the last repeats).
        """
        self._replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, *, model: str, prompt: str, **_kwargs: object) -> LLMResponse:
        """Return the next scripted reply.

        Args:
            model: The requested model (echoed back).
            prompt: The prompt (recorded).
            **_kwargs: Ignored sampling options.

        Returns:
            A response with fixed token counts.
        """
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self._replies) - 1)
        return LLMResponse(
            text=self._replies[index],
            provider="anthropic",
            model=model,
            input_tokens=100,
            output_tokens=50,
        )


class FakeFetcher:
    """A fetcher serving canned pages; unknown URLs fail.

    Attributes:
        requested: Every URL asked for, in order.
    """

    def __init__(self, pages: dict[str, Page]) -> None:
        """Hold the canned pages.

        Args:
            pages: URL to page.
        """
        self._pages = pages
        self.requested: list[str] = []

    def fetch(self, url: str) -> Page:
        """Return the canned page or a failed one.

        Args:
            url: The URL asked for.

        Returns:
            The canned page, or a `failed` page for an unknown URL.
        """
        self.requested.append(url)
        return self._pages.get(
            url, Page(url=url, status="failed", error="not in the fake")
        )


def make_job(**overrides: object) -> JobContext:
    """Build an invented job.

    Args:
        **overrides: Fields to replace.

    Returns:
        A `JobContext` for a fixture employer.
    """
    base: dict[str, object] = {
        "job_group_id": "zzfixture-intel-job",
        "title_for_display": "Data Engineer",
        "company": "Zzfixture Intel Ltd",
        "description": "Build dbt and Snowflake pipelines to replace our batch ETL.",
        "skills": [JobSkill("s1", "dbt", "must_have")],
    }
    return JobContext(**{**base, **overrides})  # type: ignore[arg-type]


def make_source(**overrides: object) -> JobSource:
    """Build an invented job source.

    Args:
        **overrides: Fields to replace.

    Returns:
        A `JobSource` with one URL.
    """
    base: dict[str, object] = {
        "apply_url": "https://jobs.example/zz/1",
        "source_urls": ("https://jobs.example/zz/1",),
        "source_names": ("fixture",),
    }
    return JobSource(**{**base, **overrides})  # type: ignore[arg-type]
```
(`Page` is created in Task 4; the fixtures file is imported by tests from Task 4 on. In this task, create the file but do not import it yet.)

`tests/test_company_intel_llm.py`:
```python
"""Unit tests for the company-intel LLM task runner."""

from __future__ import annotations

import unittest

from core.company_intel.llm import LlmTaskError, run_task, total_usage
from core.llm.types import LLMResponse


class _Echo:
    """An adapter echoing a fixed reply."""

    def __init__(self, text: str, truncated: bool = False) -> None:
        """Hold the reply.

        Args:
            text: The reply text.
            truncated: Whether to flag it as cut off.
        """
        self.text, self.truncated, self.prompt = text, truncated, ""

    def complete(self, *, model: str, prompt: str, **_kwargs: object) -> LLMResponse:
        """Return the fixed reply.

        Args:
            model: The model asked for.
            prompt: The prompt (kept).
            **_kwargs: Ignored.

        Returns:
            The response.
        """
        self.prompt = prompt
        return LLMResponse(
            self.text, "anthropic", model, 1000, 500, truncated=self.truncated
        )


class TestRunTask(unittest.TestCase):
    """run_task loads the prompt, calls the task's provider and records usage."""

    def test_it_returns_text_prompt_version_and_a_priced_record(self) -> None:
        adapter = _Echo(" {} ")
        result = run_task(
            "company_resolution",
            1,
            {
                "title": "T",
                "company": "C",
                "location": "L",
                "urls": "u",
                "description": "D {with} braces",
            },
            {"anthropic": adapter},
        )
        self.assertEqual(result.text, "{}")
        self.assertEqual(result.prompt_version, "claude.v1")
        self.assertIn("D {with} braces", adapter.prompt)
        self.assertEqual(result.record["task"], "company_resolution")
        self.assertEqual(result.record["input_tokens"], 1000)
        self.assertAlmostEqual(result.record["cost_usd"], 0.007)

    def test_a_cut_off_reply_is_an_error(self) -> None:
        with self.assertRaises(LlmTaskError):
            run_task(
                "company_resolution",
                1,
                {
                    "title": "T",
                    "company": "C",
                    "location": "L",
                    "urls": "u",
                    "description": "D",
                },
                {"anthropic": _Echo("{", truncated=True)},
            )


class TestTotalUsage(unittest.TestCase):
    """total_usage sums calls and keeps an unpriced total honest."""

    def test_it_sums_tokens_and_cost(self) -> None:
        calls = [
            {"task": "a", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.5},
            {"task": "b", "input_tokens": 20, "output_tokens": 1, "cost_usd": 0.25},
        ]
        total = total_usage(calls)
        self.assertEqual(total["input_tokens"], 30)
        self.assertEqual(total["output_tokens"], 6)
        self.assertAlmostEqual(total["cost_usd"], 0.75)
        self.assertEqual(total["calls"], calls)

    def test_an_unpriced_call_makes_the_total_cost_unknown(self) -> None:
        total = total_usage(
            [{"task": "a", "input_tokens": 1, "output_tokens": 1, "cost_usd": None}]
        )
        self.assertIsNone(total["cost_usd"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run (from `packages/core`): `../../venv/bin/python -m unittest tests.test_company_intel_keys tests.test_company_intel_config tests.test_company_intel_llm -v`
Expected: `ModuleNotFoundError: No module named 'core.company_intel'`.

- [ ] **Step 3: Implement**

`core/company_intel/__init__.py`:
```python
"""Persisted company intelligence (PLAN.md Step 19a)."""
```

`core/company_intel/keys.py`:
```python
"""Company keys that match `gold.dim_company` (PLAN.md Step 19a)."""

from __future__ import annotations

import hashlib

from core.normalisation.company import normalise_company


def company_key(name: str | None) -> str | None:
    """Build the key intel is stored under.

    Matches `gold.dim_company.company_key`, which is
    `dbt_utils.generate_surrogate_key` over the normalised name: the md5 hex
    digest of that string.

    Args:
        name: A company name as written on a posting or typed by the user.

    Returns:
        The md5 hex key, or None when the name is missing or blank after
        normalisation.
    """
    normalised = normalise_company(name)
    if not normalised:
        return None
    return hashlib.md5(  # noqa: S324 - a stable key, not a security hash
        normalised.encode("utf-8")
    ).hexdigest()
```

`core/company_intel/schema.py`:
```python
"""Models for stored company intelligence and the per-job competitive angle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MAX_COMPETITORS = 5
"""The most competitors kept per company."""

Basis = Literal["site", "model_knowledge"]
PosterKind = Literal["employer", "agency", "unknown"]
Method = Literal["employer", "llm", "manual"]
IntelStatus = Literal["ok", "no_content", "failed"]


class IntelFact(BaseModel):
    """One cited fact.

    Attributes:
        text: The fact, one or two sentences.
        source_url: The crawled page it comes from.
    """

    text: str
    source_url: str


class Competitor(BaseModel):
    """One competitor.

    Attributes:
        name: The competitor's name.
        why: Why it competes, one sentence.
        basis: `site` (the company's pages name or imply it) or
            `model_knowledge` (the model's general knowledge: unverified).
        source_url: The crawled page for a `site` competitor, else None.
    """

    name: str
    why: str = ""
    basis: Basis
    source_url: str | None = None


class IntelContent(BaseModel):
    """The structured profile of a company.

    Attributes:
        business_model: Cited facts about what the company sells and to whom.
        size_signals: Cited headcount, offices and growth wording.
        tech_stack: Cited technologies in use.
        recent_announcements: Cited recent news from the company's pages.
        notes: Anything else useful for a cover letter, still cited.
        competitors: Up to `MAX_COMPETITORS` competitors.
    """

    business_model: list[IntelFact] = Field(default_factory=list)
    size_signals: list[IntelFact] = Field(default_factory=list)
    tech_stack: list[IntelFact] = Field(default_factory=list)
    recent_announcements: list[IntelFact] = Field(default_factory=list)
    notes: list[IntelFact] = Field(default_factory=list)
    competitors: list[Competitor] = Field(default_factory=list)

    def is_empty(self) -> bool:
        """Say whether the profile holds nothing at all.

        Returns:
            True when every list is empty.
        """
        return not any(getattr(self, name) for name in type(self).model_fields)


@dataclass(frozen=True)
class Resolution:
    """Which company a job is really for.

    Attributes:
        job_group_id: The job.
        posted_company: The company on the posting.
        poster_kind: `employer`, `agency` or `unknown`.
        end_client: The company to research, or None when unknown.
        website_url: A candidate official website, or None.
        method: `employer`, `llm` or `manual`.
    """

    job_group_id: str
    posted_company: str | None
    poster_kind: PosterKind
    end_client: str | None
    website_url: str | None
    method: Method


@dataclass(frozen=True)
class StoredIntel:
    """One stored version of a company's intel.

    Attributes:
        company_key: The company's key.
        version: The version number (1 is the first).
        display_name: The name the intel was researched under.
        status: `ok`, `no_content` or `failed`.
        researched_at: When this version was written.
        content: The structured profile.
        pages: Each crawled page: `{url, status, error}`.
        resolved_from_job: The job that triggered the research.
        model: The extraction model.
        prompt_version: The extraction prompt version.
        usage: Token use and cost of the research.
    """

    company_key: str
    version: int
    display_name: str
    status: IntelStatus
    researched_at: datetime
    content: IntelContent
    pages: list[dict]
    resolved_from_job: str | None
    model: str | None
    prompt_version: str | None
    usage: dict


class AngleAdvantage(BaseModel):
    """One way the transformation could put the company ahead.

    Attributes:
        competitor: A listed competitor's exact name, or "" for the field.
        point: One grounded sentence.
    """

    competitor: str = ""
    point: str


class AngleContent(BaseModel):
    """The per-job competitive analysis (analysis, not fact).

    Attributes:
        transformation: What change the job implies for the company.
        advantages: How that change could put the company ahead.
        caveats: What is uncertain.
    """

    transformation: str = ""
    advantages: list[AngleAdvantage] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class StoredAngle:
    """A stored competitive angle.

    Attributes:
        job_group_id: The job it is for.
        company_key: The company it concerns.
        intel_version: The intel version it was written from.
        content: The analysis.
        model: The model that wrote it.
        prompt_version: The prompt version.
        usage: Token use and cost.
        created_at: When it was written.
    """

    job_group_id: str
    company_key: str
    intel_version: int
    content: AngleContent
    model: str | None
    prompt_version: str | None
    usage: dict
    created_at: datetime
```

`core/company_intel/config.py`:
```python
"""Company-intel settings from `config/company_intel.yml`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "company_intel.yml"


@dataclass(frozen=True)
class CompanyIntelConfig:
    """Research limits.

    Attributes:
        refresh_after_days: Intel older than this is stale.
        crawl_cache_days: A cached page younger than this is reused.
        max_pages: The most pages crawled for one company.
    """

    refresh_after_days: int = 90
    crawl_cache_days: int = 7
    max_pages: int = 8


def load_config(path: Path | None = None) -> CompanyIntelConfig:
    """Read the config file.

    Args:
        path: Override for tests; defaults to `config/company_intel.yml`.

    Returns:
        The config; missing keys keep their defaults.

    Raises:
        ValueError: If a value is not a positive integer.
    """
    raw = yaml.safe_load((path or _DEFAULT_PATH).read_text()) or {}
    defaults = CompanyIntelConfig()
    values: dict[str, int] = {}
    for name in ("refresh_after_days", "crawl_cache_days", "max_pages"):
        value = raw.get(name, getattr(defaults, name))
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer, got {value!r}")
        values[name] = value
    return CompanyIntelConfig(**values)
```

`config/company_intel.yml`:
```yaml
# Step 19a company-intel limits. See docs/superpowers/specs/2026-10-14-step19a-company-intel-design.md
refresh_after_days: 90   # intel older than this is re-researched
crawl_cache_days: 7      # a page crawled within this many days is not crawled again
max_pages: 8             # most pages crawled for one company
```

`config/llm_tasks.yml` — add under `tasks:`:
```yaml
  company_resolution:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
  company_intel_extraction:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
  competitive_angle:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
```

`prompts/company_resolution/claude.v1.md`:
```markdown
You read one job posting and decide which company the work is really for.

Posting:
- Title: {title}
- Posted by (company field): {company}
- Location: {location}
- Seen at: {urls}

Description:
{description}

Decide:
- "poster_kind": "employer" if the company that posted it is the one that would employ the person; "agency" if it is a recruitment agency, staffing firm or consultancy placing someone with a client; "unknown" if you cannot tell.
- "end_client": for an agency, the client's name ONLY if the description names it explicitly (never guess from phrases like "a leading bank"); otherwise null. For an employer, null.
- "website_url": the official website (https://...) of the company the work is for - the employer, or the end client - only if you are confident of it; otherwise null. Never invent a URL.

Respond with ONLY a JSON object, no other text:
{{"poster_kind": "employer|agency|unknown", "end_client": "<name or null>", "website_url": "<https URL or null>"}}
```
(The other two prompt files are created in Tasks 7 and 9; `run_task` is tested here with `company_resolution` only.)

`core/company_intel/llm.py`:
```python
"""Run one company-intel LLM task through the gateway and record its cost."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.llm.gateway import complete
from core.llm.pricing import estimate_cost_usd
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter


class LlmTaskError(Exception):
    """An LLM task could not produce a usable reply."""


@dataclass(frozen=True)
class TaskRun:
    """One finished task call.

    Attributes:
        text: The reply, stripped.
        model: The model that answered.
        prompt_version: e.g. `claude.v1`.
        record: `{task, model, input_tokens, output_tokens, cost_usd}`.
    """

    text: str
    model: str
    prompt_version: str
    record: dict


def run_task(
    task: str,
    version: int,
    variables: dict[str, str],
    adapters: dict[str, LLMAdapter],
    *,
    config_path: Path | None = None,
    max_tokens: int = 4096,
) -> TaskRun:
    """Format a task's prompt, call its provider and record the usage.

    Args:
        task: A task name in `config/llm_tasks.yml`.
        version: The prompt version number.
        variables: Values for the template's `{placeholders}`. They are
            format values, so braces inside them are safe.
        adapters: Provider name to adapter.
        config_path: Task-config override (tests).
        max_tokens: Cap on the reply length.

    Returns:
        The reply and its usage record.

    Raises:
        LlmTaskError: If the reply was cut off by the token cap.
        KeyError: If the task's provider has no adapter.
    """
    config = load_task_config(task, config_path=config_path)
    template = load_prompt(task, config.prompt_family, version)
    prompt = template.format(**variables)
    prompt_version = f"{config.prompt_family}.v{version}"
    response = complete(
        task,
        prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        config_path=config_path,
        max_tokens=max_tokens,
    )
    if response.truncated:
        raise LlmTaskError(f"the {task} reply was cut off at {max_tokens} tokens")
    cost = estimate_cost_usd(
        response.provider, response.model, response.input_tokens, response.output_tokens
    )
    record = {
        "task": task,
        "model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": cost,
    }
    return TaskRun(response.text.strip(), response.model, prompt_version, record)


def total_usage(calls: list[dict]) -> dict:
    """Sum a research's LLM calls.

    Args:
        calls: Records from `run_task`.

    Returns:
        `{calls, input_tokens, output_tokens, cost_usd}`; `cost_usd` is None
        when any call is unpriced.
    """
    costs = [call["cost_usd"] for call in calls]
    return {
        "calls": calls,
        "input_tokens": sum(call["input_tokens"] for call in calls),
        "output_tokens": sum(call["output_tokens"] for call in calls),
        "cost_usd": None if any(c is None for c in costs) else sum(costs),
    }
```

- [ ] **Step 4: Run the tests**

Run (from `packages/core`): `../../venv/bin/python -m unittest tests.test_company_intel_keys tests.test_company_intel_config tests.test_company_intel_llm -v`
Expected: `OK`. If the shipped `llm_prices.yml` price for `claude-sonnet-5` differs from `{input: 2.0, output: 10.0}`, change the expected `0.007` in `test_it_returns_text_prompt_version_and_a_priced_record` to `(1000*input + 500*output)/1e6`.
Then the DB test (from `job_search`): `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_key_matches_dim_company -v` → `OK`.

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/company_intel tests/company_intel_fixtures.py tests/test_company_intel_keys.py tests/test_company_intel_config.py tests/test_company_intel_llm.py tests/integration/test_company_intel_key_matches_dim_company.py && cd ../..
git add packages/core/core/company_intel packages/core/tests config/company_intel.yml config/llm_tasks.yml prompts/company_resolution
git commit -m "feat(job_search): company-intel keys, models, config and LLM task runner

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The store (all `intel` reads and writes)

**Files:**
- Create: `packages/core/core/company_intel/store.py`
- Test: `packages/core/tests/integration/test_company_intel_store.py`

**Interfaces:**
- Consumes: `schema.*` (Task 2). `fetch.Page` is defined in Task 4 — to keep this task independent, `store` defines the page-cache functions over plain values (`CachedPage` dataclass below), and Task 4's `CachedFetcher` converts.
- Produces (all take an app-role `Engine`):
  - `CachedPage(url, markdown, links: tuple[str, ...], fetched_on: date)`
  - `read_cached_page(engine, url, *, max_age_days: int, today: date) -> CachedPage | None`
  - `write_cached_page(engine, url, *, fetched_on: date, markdown: str, links: Sequence[str]) -> None`
  - `read_latest_intel(engine, key) -> StoredIntel | None`, `read_intel_version(engine, key, version) -> StoredIntel | None`, `list_intel_versions(engine, key) -> list[tuple[int, datetime, str]]`
  - `append_intel(engine, *, company_key, display_name, status, content, pages, resolved_from_job, model, prompt_version, usage) -> int` (the new version)
  - `read_resolution(engine, job_group_id) -> Resolution | None`, `save_resolution(engine, resolution) -> Resolution` (a stored `manual` row is returned unchanged unless the new one is also `manual`)
  - `read_angle(engine, job_group_id, key, intel_version) -> StoredAngle | None`, `read_latest_angle(engine, job_group_id) -> StoredAngle | None`, `append_angle(engine, *, job_group_id, company_key, intel_version, content, model, prompt_version, usage) -> StoredAngle`

- [ ] **Step 1: Write the failing integration tests**

```python
"""Integration tests for the intel store (real Postgres)."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.company_intel import store
from core.company_intel.schema import (
    AngleAdvantage,
    AngleContent,
    Competitor,
    IntelContent,
    IntelFact,
    Resolution,
)

_KEY = "zzfixture-store-key"
_JOB = "zzfixture-store-job"
_URL = "https://zzfixture-store.example/about"


def _content() -> IntelContent:
    """Build a small profile.

    Returns:
        Intel content with one fact and one competitor.
    """
    return IntelContent(
        business_model=[IntelFact(text="Sells widgets.", source_url=_URL)],
        competitors=[Competitor(name="Rival", basis="model_knowledge")],
    )


class TestIntelStore(unittest.TestCase):
    """Reads and writes against the intel tables."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove every fixture row."""
        with self.owner.begin() as conn:
            for table, column in (
                ("company_intel", "company_key"),
                ("job_competitive_angle", "company_key"),
            ):
                conn.execute(
                    text(f"DELETE FROM intel.{table} WHERE {column} = :k"), {"k": _KEY}
                )
            conn.execute(
                text("DELETE FROM intel.crawl_cache WHERE url LIKE 'https://zzfixture-store%'")
            )
            conn.execute(
                text("DELETE FROM intel.job_company WHERE job_group_id = :j"), {"j": _JOB}
            )

    def _append(self, status: str = "ok") -> int:
        """Append one intel version.

        Args:
            status: The status to store.

        Returns:
            The new version number.
        """
        return store.append_intel(
            self.engine,
            company_key=_KEY,
            display_name="Zz Store Co",
            status=status,  # type: ignore[arg-type]
            content=_content(),
            pages=[{"url": _URL, "status": "ok", "error": None}],
            resolved_from_job=_JOB,
            model="m",
            prompt_version="claude.v1",
            usage={"calls": [], "input_tokens": 1, "output_tokens": 2, "cost_usd": 0.1},
        )

    def test_versions_count_up_and_the_latest_wins(self) -> None:
        self.assertEqual(self._append(), 1)
        self.assertEqual(self._append("no_content"), 2)
        latest = store.read_latest_intel(self.engine, _KEY)
        assert latest is not None
        self.assertEqual((latest.version, latest.status), (2, "no_content"))
        self.assertEqual(latest.content.competitors[0].name, "Rival")
        self.assertEqual(latest.pages[0]["url"], _URL)
        self.assertEqual(latest.usage["output_tokens"], 2)
        first = store.read_intel_version(self.engine, _KEY, 1)
        assert first is not None
        self.assertEqual(first.status, "ok")
        self.assertEqual([v[0] for v in store.list_intel_versions(self.engine, _KEY)], [2, 1])

    def test_an_unknown_company_has_no_intel(self) -> None:
        self.assertIsNone(store.read_latest_intel(self.engine, _KEY))
        self.assertIsNone(store.read_intel_version(self.engine, _KEY, 1))
        self.assertEqual(store.list_intel_versions(self.engine, _KEY), [])

    def test_a_cached_page_is_returned_only_while_fresh(self) -> None:
        today = date(2026, 10, 14)
        store.write_cached_page(
            self.engine,
            "https://zzfixture-store.example/",
            fetched_on=today - timedelta(days=3),
            markdown="# Hi",
            links=["https://zzfixture-store.example/about"],
        )
        fresh = store.read_cached_page(
            self.engine, "https://zzfixture-store.example/", max_age_days=7, today=today
        )
        assert fresh is not None
        self.assertEqual(fresh.markdown, "# Hi")
        self.assertEqual(fresh.links, ("https://zzfixture-store.example/about",))
        self.assertIsNone(
            store.read_cached_page(
                self.engine,
                "https://zzfixture-store.example/",
                max_age_days=2,
                today=today,
            )
        )

    def test_writing_the_same_page_twice_on_a_day_is_harmless(self) -> None:
        for _ in range(2):
            store.write_cached_page(
                self.engine,
                "https://zzfixture-store.example/x",
                fetched_on=date(2026, 10, 14),
                markdown="x",
                links=[],
            )

    def test_a_manual_resolution_survives_an_automatic_one(self) -> None:
        manual = Resolution(_JOB, "Agency", "agency", "Real Bank", "https://bank.example", "manual")
        auto = Resolution(_JOB, "Agency", "agency", "Guess Ltd", None, "llm")
        store.save_resolution(self.engine, manual)
        kept = store.save_resolution(self.engine, auto)
        self.assertEqual(kept, manual)
        self.assertEqual(store.read_resolution(self.engine, _JOB), manual)

    def test_an_automatic_resolution_is_replaced_by_a_manual_one(self) -> None:
        auto = Resolution(_JOB, "Agency", "agency", None, None, "llm")
        manual = Resolution(_JOB, "Agency", "agency", "Real Bank", None, "manual")
        store.save_resolution(self.engine, auto)
        self.assertEqual(store.save_resolution(self.engine, manual), manual)
        self.assertIsNone(store.read_resolution(self.engine, "zzfixture-none"))

    def test_angles_are_stored_per_intel_version_and_the_newest_is_read(self) -> None:
        content = AngleContent(
            transformation="Moves to a modern stack.",
            advantages=[AngleAdvantage(competitor="Rival", point="Faster data.")],
            caveats=["Unverified."],
        )
        store.append_angle(
            self.engine,
            job_group_id=_JOB,
            company_key=_KEY,
            intel_version=1,
            content=content,
            model="m",
            prompt_version="claude.v1",
            usage={"calls": []},
        )
        self.assertIsNone(store.read_angle(self.engine, _JOB, _KEY, 2))
        found = store.read_angle(self.engine, _JOB, _KEY, 1)
        assert found is not None
        self.assertEqual(found.content.advantages[0].competitor, "Rival")
        latest = store.read_latest_angle(self.engine, _JOB)
        assert latest is not None
        self.assertEqual(latest.intel_version, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_store -v`
Expected: `ImportError: cannot import name 'store' from 'core.company_intel'`.

- [ ] **Step 3: Implement the store**

```python
"""Reads and writes for the `intel` tables (PLAN.md Step 19a).

Every function takes the app-role engine. The tables are shared data (no
RLS), so no user id is involved. `company_intel` and `job_competitive_angle`
are append-only: a new version or angle is a new row.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Engine, text

from core.company_intel.schema import (
    AngleContent,
    IntelContent,
    IntelStatus,
    Resolution,
    StoredAngle,
    StoredIntel,
)


@dataclass(frozen=True)
class CachedPage:
    """A crawled page read back from the cache.

    Attributes:
        url: The page's URL.
        markdown: Its text as Markdown.
        links: Its internal links.
        fetched_on: The day it was crawled.
    """

    url: str
    markdown: str
    links: tuple[str, ...]
    fetched_on: date


_INTEL_COLUMNS = (
    "company_key, version, display_name, status, researched_at, content, pages, "
    "resolved_from_job, model, prompt_version, usage"
)


def _intel_from_row(row) -> StoredIntel:  # type: ignore[no-untyped-def]
    """Build a `StoredIntel` from a result row.

    Args:
        row: A row selecting `_INTEL_COLUMNS`.

    Returns:
        The stored intel.
    """
    return StoredIntel(
        company_key=row.company_key,
        version=row.version,
        display_name=row.display_name,
        status=row.status,
        researched_at=row.researched_at,
        content=IntelContent.model_validate(row.content),
        pages=list(row.pages),
        resolved_from_job=row.resolved_from_job,
        model=row.model,
        prompt_version=row.prompt_version,
        usage=dict(row.usage),
    )


def read_latest_intel(engine: Engine, key: str) -> StoredIntel | None:
    """Read a company's newest intel version.

    Args:
        engine: The app-role engine.
        key: The company key.

    Returns:
        The highest version, or None when the company has none.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {_INTEL_COLUMNS} FROM intel.company_intel "
                "WHERE company_key = :k ORDER BY version DESC LIMIT 1"
            ),
            {"k": key},
        ).one_or_none()
    return _intel_from_row(row) if row else None


def read_intel_version(engine: Engine, key: str, version: int) -> StoredIntel | None:
    """Read one specific intel version.

    Args:
        engine: The app-role engine.
        key: The company key.
        version: The version to read.

    Returns:
        That version, or None when it does not exist.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {_INTEL_COLUMNS} FROM intel.company_intel "
                "WHERE company_key = :k AND version = :v"
            ),
            {"k": key, "v": version},
        ).one_or_none()
    return _intel_from_row(row) if row else None


def list_intel_versions(engine: Engine, key: str) -> list[tuple[int, datetime, str]]:
    """List a company's versions, newest first.

    Args:
        engine: The app-role engine.
        key: The company key.

    Returns:
        `(version, researched_at, status)` tuples.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT version, researched_at, status FROM intel.company_intel "
                "WHERE company_key = :k ORDER BY version DESC"
            ),
            {"k": key},
        ).all()
    return [(r.version, r.researched_at, r.status) for r in rows]


def append_intel(
    engine: Engine,
    *,
    company_key: str,
    display_name: str,
    status: IntelStatus,
    content: IntelContent,
    pages: list[dict],
    resolved_from_job: str | None,
    model: str | None,
    prompt_version: str | None,
    usage: dict,
) -> int:
    """Append a new intel version for a company.

    Args:
        engine: The app-role engine.
        company_key: The company key.
        display_name: The name it was researched under.
        status: `ok`, `no_content` or `failed`.
        content: The structured profile.
        pages: Each crawled page: `{url, status, error}`.
        resolved_from_job: The job that triggered the research.
        model: The extraction model.
        prompt_version: The extraction prompt version.
        usage: Token use and cost.

    Returns:
        The new version number (1 for a company's first).
    """
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO intel.company_intel (company_key, version, display_name, "
                "status, content, pages, resolved_from_job, model, prompt_version, "
                "usage) SELECT :k, COALESCE(MAX(version), 0) + 1, :name, :status, "
                "CAST(:content AS jsonb), CAST(:pages AS jsonb), :job, :model, :pv, "
                "CAST(:usage AS jsonb) FROM intel.company_intel WHERE company_key = :k "
                "RETURNING version"
            ),
            {
                "k": company_key,
                "name": display_name,
                "status": status,
                "content": content.model_dump_json(),
                "pages": json.dumps(pages),
                "job": resolved_from_job,
                "model": model,
                "pv": prompt_version,
                "usage": json.dumps(usage),
            },
        ).scalar_one()


def read_cached_page(
    engine: Engine, url: str, *, max_age_days: int, today: date
) -> CachedPage | None:
    """Read the newest cached copy of a page if it is still fresh.

    Args:
        engine: The app-role engine.
        url: The page URL.
        max_age_days: How old a copy may be, in days.
        today: The current day.

    Returns:
        The cached page, or None when there is no copy within the window.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT url, markdown, links, fetched_on FROM intel.crawl_cache "
                "WHERE url = :u AND status = 'ok' AND fetched_on >= :oldest "
                "ORDER BY fetched_on DESC LIMIT 1"
            ),
            {"u": url, "oldest": today - timedelta(days=max_age_days)},
        ).one_or_none()
    if row is None:
        return None
    return CachedPage(row.url, row.markdown, tuple(row.links), row.fetched_on)


def write_cached_page(
    engine: Engine,
    url: str,
    *,
    fetched_on: date,
    markdown: str,
    links: Sequence[str],
) -> None:
    """Cache a crawled page; a second write for the same day is ignored.

    Args:
        engine: The app-role engine.
        url: The page URL.
        fetched_on: The day it was crawled.
        markdown: Its text as Markdown.
        links: Its internal links.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO intel.crawl_cache (url, fetched_on, status, markdown, "
                "links) VALUES (:u, :d, 'ok', :md, CAST(:links AS jsonb)) "
                "ON CONFLICT (url, fetched_on) DO NOTHING"
            ),
            {"u": url, "d": fetched_on, "md": markdown, "links": json.dumps(list(links))},
        )


def _resolution_from_row(row) -> Resolution:  # type: ignore[no-untyped-def]
    """Build a `Resolution` from a result row.

    Args:
        row: A row of `intel.job_company`.

    Returns:
        The resolution.
    """
    return Resolution(
        job_group_id=row.job_group_id,
        posted_company=row.posted_company,
        poster_kind=row.poster_kind,
        end_client=row.end_client,
        website_url=row.website_url,
        method=row.method,
    )


def read_resolution(engine: Engine, job_group_id: str) -> Resolution | None:
    """Read how a job's company was resolved.

    Args:
        engine: The app-role engine.
        job_group_id: The job.

    Returns:
        The stored resolution, or None when the job has none.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT job_group_id, posted_company, poster_kind, end_client, "
                "website_url, method FROM intel.job_company WHERE job_group_id = :j"
            ),
            {"j": job_group_id},
        ).one_or_none()
    return _resolution_from_row(row) if row else None


def save_resolution(engine: Engine, resolution: Resolution) -> Resolution:
    """Save a job's resolution without overwriting a manual one.

    Args:
        engine: The app-role engine.
        resolution: The resolution to store.

    Returns:
        What is stored afterwards: `resolution`, or the existing `manual`
        row when `resolution` is automatic.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO intel.job_company (job_group_id, posted_company, "
                "poster_kind, end_client, website_url, method) VALUES (:j, :posted, "
                ":kind, :end, :site, :method) ON CONFLICT (job_group_id) DO UPDATE SET "
                "posted_company = EXCLUDED.posted_company, "
                "poster_kind = EXCLUDED.poster_kind, end_client = EXCLUDED.end_client, "
                "website_url = EXCLUDED.website_url, method = EXCLUDED.method, "
                "resolved_at = now() WHERE intel.job_company.method <> 'manual' "
                "OR EXCLUDED.method = 'manual'"
            ),
            {
                "j": resolution.job_group_id,
                "posted": resolution.posted_company,
                "kind": resolution.poster_kind,
                "end": resolution.end_client,
                "site": resolution.website_url,
                "method": resolution.method,
            },
        )
    stored = read_resolution(engine, resolution.job_group_id)
    assert stored is not None
    return stored


def _angle_from_row(row) -> StoredAngle:  # type: ignore[no-untyped-def]
    """Build a `StoredAngle` from a result row.

    Args:
        row: A row of `intel.job_competitive_angle`.

    Returns:
        The stored angle.
    """
    return StoredAngle(
        job_group_id=row.job_group_id,
        company_key=row.company_key,
        intel_version=row.intel_version,
        content=AngleContent.model_validate(row.content),
        model=row.model,
        prompt_version=row.prompt_version,
        usage=dict(row.usage),
        created_at=row.created_at,
    )


_ANGLE_COLUMNS = (
    "job_group_id, company_key, intel_version, content, model, prompt_version, "
    "usage, created_at"
)


def read_angle(
    engine: Engine, job_group_id: str, key: str, intel_version: int
) -> StoredAngle | None:
    """Read the newest angle written from one intel version.

    Args:
        engine: The app-role engine.
        job_group_id: The job.
        key: The company key.
        intel_version: The intel version the angle must be built from.

    Returns:
        The newest matching angle, or None.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {_ANGLE_COLUMNS} FROM intel.job_competitive_angle "
                "WHERE job_group_id = :j AND company_key = :k AND intel_version = :v "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"j": job_group_id, "k": key, "v": intel_version},
        ).one_or_none()
    return _angle_from_row(row) if row else None


def read_latest_angle(engine: Engine, job_group_id: str) -> StoredAngle | None:
    """Read a job's newest angle, whatever intel version it used.

    Args:
        engine: The app-role engine.
        job_group_id: The job.

    Returns:
        The newest angle, or None.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {_ANGLE_COLUMNS} FROM intel.job_competitive_angle "
                "WHERE job_group_id = :j ORDER BY created_at DESC LIMIT 1"
            ),
            {"j": job_group_id},
        ).one_or_none()
    return _angle_from_row(row) if row else None


def append_angle(
    engine: Engine,
    *,
    job_group_id: str,
    company_key: str,
    intel_version: int,
    content: AngleContent,
    model: str | None,
    prompt_version: str | None,
    usage: dict,
) -> StoredAngle:
    """Append a competitive angle for a job.

    Args:
        engine: The app-role engine.
        job_group_id: The job.
        company_key: The company key.
        intel_version: The intel version it was written from.
        content: The analysis.
        model: The model that wrote it.
        prompt_version: The prompt version.
        usage: Token use and cost.

    Returns:
        The stored angle.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO intel.job_competitive_angle (job_group_id, company_key, "
                "intel_version, content, model, prompt_version, usage) VALUES (:j, "
                ":k, :v, CAST(:content AS jsonb), :model, :pv, CAST(:usage AS jsonb)) "
                f"RETURNING {_ANGLE_COLUMNS}"
            ),
            {
                "j": job_group_id,
                "k": company_key,
                "v": intel_version,
                "content": content.model_dump_json(),
                "model": model,
                "pv": prompt_version,
                "usage": json.dumps(usage),
            },
        ).one()
    return _angle_from_row(row)
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_store -v`
Expected: `OK` (7 tests).

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/company_intel/store.py tests/integration/test_company_intel_store.py && cd ../..
git add packages/core/core/company_intel/store.py packages/core/tests/integration/test_company_intel_store.py
git commit -m "feat(job_search): company-intel store (versions, cache, resolution, angle)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The fetcher — Crawl4AI over HTTP, plus the cache

**Files:**
- Create: `packages/core/core/company_intel/fetch.py`
- Modify: `packages/core/core/settings.py` (add `crawl4ai_url`)
- Test: `packages/core/tests/test_company_intel_fetch.py`, `packages/core/tests/integration/test_company_intel_cached_fetcher.py`

**Interfaces:**
- Consumes: `store.read_cached_page`, `store.write_cached_page` (Task 3).
- Produces:
  - `Page(url: str, status: Literal["ok", "failed"], markdown: str = "", links: tuple[str, ...] = (), error: str | None = None)` frozen dataclass
  - `Fetcher` Protocol: `fetch(self, url: str) -> Page`
  - `parse_crawl_payload(url: str, payload: object) -> Page`
  - `Crawl4AIFetcher(base_url: str, *, client: httpx.Client | None = None, timeout: float = 90.0)`
  - `CachedFetcher(inner: Fetcher, engine: Engine, *, cache_days: int, today: Callable[[], date] = date.today)`
  - `Settings.crawl4ai_url: str = "http://crawl4ai:11235"`

- [ ] **Step 1: Write the failing unit tests**

```python
"""Unit tests for the Crawl4AI fetcher (no network)."""

from __future__ import annotations

import json
import unittest

import httpx

from core.company_intel.fetch import Crawl4AIFetcher, Page, parse_crawl_payload

_URL = "https://acme.example/"


class TestParseCrawlPayload(unittest.TestCase):
    """parse_crawl_payload turns any Crawl4AI answer into a Page."""

    def test_string_markdown_and_dict_links(self) -> None:
        page = parse_crawl_payload(
            _URL,
            {
                "success": True,
                "results": [
                    {
                        "success": True,
                        "markdown": "# Acme\nWe make widgets.",
                        "links": {"internal": [{"href": "/about"}, {"href": "https://acme.example/careers"}]},
                    }
                ],
            },
        )
        self.assertEqual(page.status, "ok")
        self.assertIn("widgets", page.markdown)
        self.assertEqual(
            page.links, ("https://acme.example/about", "https://acme.example/careers")
        )

    def test_dict_markdown_prefers_raw_markdown_and_string_links(self) -> None:
        page = parse_crawl_payload(
            _URL,
            {
                "results": [
                    {
                        "success": True,
                        "markdown": {"raw_markdown": "Raw text", "fit_markdown": "Fit"},
                        "links": {"internal": ["https://acme.example/blog"]},
                    }
                ]
            },
        )
        self.assertEqual((page.markdown, page.links), ("Raw text", ("https://acme.example/blog",)))

    def test_fit_markdown_is_used_when_raw_is_empty(self) -> None:
        page = parse_crawl_payload(
            _URL,
            {"results": [{"success": True, "markdown": {"raw_markdown": "", "fit_markdown": "Fit"}}]},
        )
        self.assertEqual(page.markdown, "Fit")

    def test_a_failed_result_carries_its_error(self) -> None:
        page = parse_crawl_payload(
            _URL,
            {"results": [{"success": False, "error_message": "blocked by robots.txt"}]},
        )
        self.assertEqual(page.status, "failed")
        self.assertIn("robots", page.error or "")

    def test_odd_payloads_never_raise(self) -> None:
        for payload in (None, [], {}, {"results": []}, {"results": ["x"]}, "oops"):
            page = parse_crawl_payload(_URL, payload)
            self.assertEqual(page.status, "failed", payload)
            self.assertTrue(page.error)

    def test_a_page_with_no_text_is_a_failure(self) -> None:
        page = parse_crawl_payload(_URL, {"results": [{"success": True, "markdown": "  "}]})
        self.assertEqual(page.status, "failed")


class TestCrawl4AIFetcher(unittest.TestCase):
    """Crawl4AIFetcher posts to /crawl and tolerates transport errors."""

    def _fetcher(self, handler) -> Crawl4AIFetcher:  # type: ignore[no-untyped-def]
        """Build a fetcher over a mock transport.

        Args:
            handler: A request handler for `httpx.MockTransport`.

        Returns:
            The fetcher.
        """
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return Crawl4AIFetcher("http://crawl4ai:11235/", client=client)

    def test_it_posts_the_url_with_robots_checking_on(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"results": [{"success": True, "markdown": "Hello"}]}
            )

        page = self._fetcher(handler).fetch(_URL)
        self.assertEqual(page.markdown, "Hello")
        self.assertEqual(seen["url"], "http://crawl4ai:11235/crawl")
        body = seen["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["urls"], [_URL])
        self.assertTrue(body["crawler_config"]["params"]["check_robots_txt"])

    def test_http_errors_and_bad_json_become_failed_pages(self) -> None:
        for response in (
            httpx.Response(500, text="boom"),
            httpx.Response(200, text="not json"),
        ):
            page = self._fetcher(lambda _r, resp=response: resp).fetch(_URL)
            self.assertEqual(page.status, "failed")
            self.assertTrue(page.error)

    def test_a_connection_error_becomes_a_failed_page(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        page = self._fetcher(handler).fetch(_URL)
        self.assertEqual(page, Page(url=_URL, status="failed", error=page.error))
        self.assertIn("down", page.error or "")


if __name__ == "__main__":
    unittest.main()
```

And the integration test for the cache:
```python
"""Integration test for the caching fetcher (real Postgres)."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from sqlalchemy import text
from tests.company_intel_fixtures import FakeFetcher
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.company_intel.fetch import CachedFetcher, Page

_URL = "https://zzfixture-fetch.example/about"


class TestCachedFetcher(unittest.TestCase):
    """The cache stops a second crawl within the window."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove the fixture cache rows."""
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM intel.crawl_cache WHERE url LIKE 'https://zzfixture-fetch%'")
            )

    def test_a_second_fetch_the_same_week_does_not_crawl_again(self) -> None:
        inner = FakeFetcher(
            {_URL: Page(_URL, "ok", "About us", ("https://zzfixture-fetch.example/jobs",))}
        )
        today = date(2026, 10, 14)
        fetcher = CachedFetcher(inner, self.engine, cache_days=7, today=lambda: today)
        first = fetcher.fetch(_URL)
        second = CachedFetcher(
            inner, self.engine, cache_days=7, today=lambda: today + timedelta(days=3)
        ).fetch(_URL)
        self.assertEqual(inner.requested, [_URL])
        self.assertEqual(second.markdown, first.markdown)
        self.assertEqual(second.links, first.links)

    def test_an_old_copy_is_crawled_again(self) -> None:
        inner = FakeFetcher({_URL: Page(_URL, "ok", "About us")})
        today = date(2026, 10, 14)
        CachedFetcher(inner, self.engine, cache_days=7, today=lambda: today).fetch(_URL)
        CachedFetcher(
            inner, self.engine, cache_days=7, today=lambda: today + timedelta(days=30)
        ).fetch(_URL)
        self.assertEqual(inner.requested, [_URL, _URL])

    def test_failed_pages_are_not_cached(self) -> None:
        inner = FakeFetcher({})
        today = date(2026, 10, 14)
        fetcher = CachedFetcher(inner, self.engine, cache_days=7, today=lambda: today)
        self.assertEqual(fetcher.fetch(_URL).status, "failed")
        fetcher.fetch(_URL)
        self.assertEqual(inner.requested, [_URL, _URL])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail**

Run (from `packages/core`): `../../venv/bin/python -m unittest tests.test_company_intel_fetch -v`
Expected: `ModuleNotFoundError: No module named 'core.company_intel.fetch'`.

- [ ] **Step 3: Implement**

`settings.py` — add to the class docstring `crawl4ai_url: Base URL of the Crawl4AI service used by company research.` and the field `crawl4ai_url: str = "http://crawl4ai:11235"` next to `ollama_base_url`. Add `CRAWL4AI_URL=http://crawl4ai:11235` to `.env.example` under the Ollama line.

`core/company_intel/fetch.py`:
```python
"""Fetch web pages as Markdown through Crawl4AI, with a day-level cache."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol
from urllib.parse import urljoin

import httpx
from sqlalchemy import Engine

from core.company_intel import store


@dataclass(frozen=True)
class Page:
    """One fetched page.

    Attributes:
        url: The URL asked for.
        status: `ok` or `failed`.
        markdown: The page text as Markdown (empty when failed).
        links: The page's internal links, absolute.
        error: Why it failed, if it did.
    """

    url: str
    status: Literal["ok", "failed"]
    markdown: str = ""
    links: tuple[str, ...] = ()
    error: str | None = None


class Fetcher(Protocol):
    """Anything that can fetch a page."""

    def fetch(self, url: str) -> Page:
        """Fetch one page.

        Args:
            url: The page URL.

        Returns:
            The page; a failure is a `failed` page, never an exception.
        """
        ...


def _failed(url: str, error: str) -> Page:
    """Build a failed page.

    Args:
        url: The URL asked for.
        error: Why it failed.

    Returns:
        A `failed` page.
    """
    return Page(url=url, status="failed", error=error)


def parse_crawl_payload(url: str, payload: object) -> Page:
    """Turn a Crawl4AI `/crawl` answer into a page.

    Tolerates `markdown` as a string or as `{raw_markdown, fit_markdown}`
    and links as `{href}` dicts or plain strings.

    Args:
        url: The URL that was asked for.
        payload: The decoded JSON answer.

    Returns:
        The page; anything unusable is a `failed` page with a reason.
    """
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        return _failed(url, "the crawler returned no results")
    result = results[0]
    if not isinstance(result, dict):
        return _failed(url, "the crawler returned an unreadable result")
    if result.get("success") is False:
        return _failed(url, str(result.get("error_message") or "the crawl failed"))
    markdown = result.get("markdown")
    if isinstance(markdown, dict):
        markdown = markdown.get("raw_markdown") or markdown.get("fit_markdown") or ""
    if not isinstance(markdown, str) or not markdown.strip():
        return _failed(url, "the page had no text")
    links_block = result.get("links")
    internal = links_block.get("internal") if isinstance(links_block, dict) else None
    links: list[str] = []
    for item in internal or []:
        href = item.get("href") if isinstance(item, dict) else item
        if isinstance(href, str) and href.strip():
            absolute = urljoin(url, href.strip())
            if absolute not in links:
                links.append(absolute)
    return Page(url=url, status="ok", markdown=markdown, links=tuple(links))


class Crawl4AIFetcher:
    """Fetch pages from a running Crawl4AI service (`POST /crawl`)."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 90.0,
    ) -> None:
        """Point at the service.

        Args:
            base_url: The service URL, e.g. `http://crawl4ai:11235`.
            client: An HTTP client (tests pass one with a mock transport).
            timeout: Seconds to wait for one crawl.
        """
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def fetch(self, url: str) -> Page:
        """Crawl one page, respecting robots.txt.

        Args:
            url: The page URL.

        Returns:
            The page; transport and parsing failures become `failed` pages.
        """
        body = {
            "urls": [url],
            "browser_config": {"type": "BrowserConfig", "params": {"headless": True}},
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {"cache_mode": "bypass", "check_robots_txt": True},
            },
        }
        try:
            response = self._client.post(f"{self._base}/crawl", json=body)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return _failed(url, f"the crawler request failed: {exc}")
        return parse_crawl_payload(url, payload)


class CachedFetcher:
    """Wrap a fetcher with the `intel.crawl_cache` day-level cache."""

    def __init__(
        self,
        inner: Fetcher,
        engine: Engine,
        *,
        cache_days: int,
        today: Callable[[], date] = date.today,
    ) -> None:
        """Wrap a fetcher.

        Args:
            inner: The fetcher used on a cache miss.
            engine: The app-role engine.
            cache_days: A cached copy younger than this is reused.
            today: Clock for tests.
        """
        self._inner = inner
        self._engine = engine
        self._days = cache_days
        self._today = today

    def fetch(self, url: str) -> Page:
        """Return a fresh cached copy or crawl and cache the page.

        Args:
            url: The page URL.

        Returns:
            The page. Only successful pages are cached.
        """
        today = self._today()
        cached = store.read_cached_page(
            self._engine, url, max_age_days=self._days, today=today
        )
        if cached is not None:
            return Page(url=url, status="ok", markdown=cached.markdown, links=cached.links)
        page = self._inner.fetch(url)
        if page.status == "ok":
            store.write_cached_page(
                self._engine, url, fetched_on=today, markdown=page.markdown, links=page.links
            )
        return page
```

- [ ] **Step 4: Run the tests**

Run (from `packages/core`): `../../venv/bin/python -m unittest tests.test_company_intel_fetch -v` → `OK`.
Run (from `job_search`): `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_cached_fetcher -v` → `OK`.

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/company_intel/fetch.py core/settings.py tests/test_company_intel_fetch.py tests/integration/test_company_intel_cached_fetcher.py && cd ../..
git add packages/core/core/company_intel/fetch.py packages/core/core/settings.py packages/core/tests .env.example
git commit -m "feat(job_search): Crawl4AI fetcher with a URL+day cache

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Page discovery

**Files:**
- Create: `packages/core/core/company_intel/discover.py`
- Test: `packages/core/tests/test_company_intel_discover.py`

**Interfaces:**
- Produces: `registered_domain(url: str) -> str`, `pick_pages(home_url: str, links: Iterable[str], max_pages: int) -> list[str]` (the home URL first, then at most `max_pages - 1` same-domain pages, best first, no duplicates).

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for choosing which pages to crawl."""

from __future__ import annotations

import unittest

from core.company_intel.discover import pick_pages, registered_domain

_HOME = "https://www.acme.com/"


class TestRegisteredDomain(unittest.TestCase):
    """registered_domain strips subdomains, including two-part UK suffixes."""

    def test_plain_and_subdomain_hosts(self) -> None:
        self.assertEqual(registered_domain("https://www.acme.com/x"), "acme.com")
        self.assertEqual(registered_domain("https://blog.eng.acme.com"), "acme.com")

    def test_two_part_suffixes(self) -> None:
        self.assertEqual(registered_domain("https://www.acme.co.uk/"), "acme.co.uk")

    def test_garbage_gives_an_empty_domain(self) -> None:
        self.assertEqual(registered_domain("not a url"), "")


class TestPickPages(unittest.TestCase):
    """pick_pages keeps relevant same-site pages, best first."""

    def test_home_comes_first_then_relevant_pages(self) -> None:
        links = [
            "https://www.acme.com/pricing",
            "https://www.acme.com/about",
            "https://www.acme.com/careers",
            "https://www.acme.com/blog/how-we-use-dbt",
            "https://www.acme.com/engineering",
        ]
        picked = pick_pages(_HOME, links, 8)
        self.assertEqual(picked[0], _HOME)
        self.assertEqual(
            set(picked[1:]),
            {
                "https://www.acme.com/about",
                "https://www.acme.com/careers",
                "https://www.acme.com/blog/how-we-use-dbt",
                "https://www.acme.com/engineering",
            },
        )

    def test_other_sites_files_and_duplicates_are_dropped(self) -> None:
        links = [
            "https://twitter.com/acme/about",
            "https://www.acme.com/about#team",
            "https://www.acme.com/about?utm=1",
            "https://www.acme.com/about",
            "https://www.acme.com/about/report.pdf",
            "mailto:hi@acme.com",
            "https://www.acme.com/logo.png",
        ]
        self.assertEqual(pick_pages(_HOME, links, 8), [_HOME, "https://www.acme.com/about"])

    def test_the_cap_includes_the_home_page(self) -> None:
        links = [f"https://www.acme.com/blog/post-{i}" for i in range(20)]
        self.assertEqual(len(pick_pages(_HOME, links, 4)), 4)
        self.assertEqual(pick_pages(_HOME, links, 1), [_HOME])

    def test_the_order_is_stable(self) -> None:
        links = ["https://www.acme.com/team", "https://www.acme.com/about"]
        self.assertEqual(pick_pages(_HOME, links, 8), pick_pages(_HOME, reversed(links), 8))

    def test_subdomains_of_the_same_site_are_allowed(self) -> None:
        picked = pick_pages(_HOME, ["https://engineering.acme.com/blog"], 8)
        self.assertEqual(picked, [_HOME, "https://engineering.acme.com/blog"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run (from `packages/core`): `../../venv/bin/python -m unittest tests.test_company_intel_discover -v` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Choose which of a company's pages to crawl (PLAN.md Step 19a)."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

# Second-level suffixes where the registered domain has three labels. A
# short list: a miss only means a subdomain is treated as its own site.
_TWO_PART_SUFFIXES = frozenset(
    {"co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "com.au", "co.nz", "co.in"}
)
# Path words that signal a page worth reading, best first.
_KEYWORDS = (
    "about",
    "company",
    "careers",
    "jobs",
    "engineering",
    "technology",
    "tech",
    "blog",
    "team",
    "culture",
    "product",
    "platform",
    "news",
)
_SKIP_SUFFIXES = (
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".zip", ".mp4", ".css", ".js",
)


def registered_domain(url: str) -> str:
    """Return a URL's registered domain (the site, without subdomains).

    Args:
        url: Any URL.

    Returns:
        e.g. `acme.com` or `acme.co.uk`; an empty string when the URL has
        no usable host.
    """
    host = (urlsplit(url).hostname or "").lower().strip(".")
    labels = host.split(".")
    if len(labels) < 2 or not all(labels):
        return ""
    if ".".join(labels[-2:]) in _TWO_PART_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _clean(url: str) -> str | None:
    """Drop the query and fragment and reject non-page URLs.

    Args:
        url: A candidate link.

    Returns:
        The cleaned http(s) URL, or None when it is not a crawlable page.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.path.lower().endswith(_SKIP_SUFFIXES):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))


def _rank(url: str) -> tuple[int, int, str]:
    """Score a page: lower is better.

    Args:
        url: A cleaned URL.

    Returns:
        `(keyword position, path depth, url)`; a page with no keyword
        sorts after every page with one.
    """
    path = urlsplit(url).path.lower()
    words = [w for w in path.replace("_", "-").replace("/", "-").split("-") if w]
    positions = [_KEYWORDS.index(w) for w in words if w in _KEYWORDS]
    return (
        min(positions) if positions else len(_KEYWORDS),
        path.count("/"),
        url,
    )


def pick_pages(home_url: str, links: Iterable[str], max_pages: int) -> list[str]:
    """Pick the pages to crawl for one company.

    Args:
        home_url: The company's homepage (always included, first).
        links: Links found on the homepage.
        max_pages: The most pages in total, the homepage included.

    Returns:
        The homepage, then up to `max_pages - 1` same-site pages whose path
        contains a useful word, best first and in a stable order.
    """
    site = registered_domain(home_url)
    chosen: dict[str, tuple[int, int, str]] = {}
    for link in links:
        cleaned = _clean(link)
        if cleaned is None or cleaned == _clean(home_url):
            continue
        if not site or registered_domain(cleaned) != site:
            continue
        rank = _rank(cleaned)
        if rank[0] == len(_KEYWORDS):
            continue
        chosen[cleaned] = rank
    ordered = sorted(chosen, key=lambda u: chosen[u])
    return [home_url, *ordered[: max(0, max_pages - 1)]]
```

- [ ] **Step 4: Run to verify it passes**

Run (from `packages/core`): `../../venv/bin/python -m unittest tests.test_company_intel_discover -v` → `OK`.

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/company_intel/discover.py tests/test_company_intel_discover.py && cd ../..
git add packages/core/core/company_intel/discover.py packages/core/tests/test_company_intel_discover.py
git commit -m "feat(job_search): choose which company pages to crawl

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Resolve the end client

**Files:**
- Create: `packages/core/core/company_intel/resolve.py`
- Test: `packages/core/tests/integration/test_company_intel_resolve.py`

**Interfaces:**
- Consumes: `store.read_resolution/save_resolution` (Task 3), `llm.run_task` (Task 2), `fetch.Fetcher/Page` (Task 4), `JobContext`, `JobSource`.
- Produces:
  - `ManualOverride(end_client: str | None = None, website_url: str | None = None)` frozen dataclass
  - `ResolveResult(resolution: Resolution, calls: list[dict])`
  - `resolve_company(engine, job: JobContext, source: JobSource, *, adapters, override: ManualOverride | None = None, config_path: Path | None = None) -> ResolveResult`
  - `verify_website(fetcher: Fetcher, website_url: str, company_name: str) -> Page | None` (the homepage when it loads and mentions the company, else None)
  - `parse_resolution_reply(text: str) -> dict` (the JSON object or `{}` when the reply is unusable)

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for resolving which company a job is really for."""

from __future__ import annotations

import json
import unittest

from sqlalchemy import text
from tests.company_intel_fixtures import FakeAdapter, FakeFetcher, make_job, make_source
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.company_intel import store
from core.company_intel.fetch import Page
from core.company_intel.resolve import (
    ManualOverride,
    parse_resolution_reply,
    resolve_company,
    verify_website,
)

_JOB = "zzfixture-intel-job"


def _reply(kind: str, end_client: str | None, website: str | None) -> str:
    """Build a scripted LLM reply.

    Args:
        kind: The poster kind.
        end_client: The end client name or None.
        website: The website or None.

    Returns:
        A JSON string.
    """
    return json.dumps(
        {"poster_kind": kind, "end_client": end_client, "website_url": website}
    )


class TestParseReply(unittest.TestCase):
    """parse_resolution_reply tolerates fences and junk."""

    def test_a_fenced_object_is_read(self) -> None:
        reply = "```json\n" + _reply("agency", "Big Bank", None) + "\n```"
        self.assertEqual(parse_resolution_reply(reply)["end_client"], "Big Bank")

    def test_junk_gives_an_empty_dict(self) -> None:
        for junk in ("", "no idea", "[1, 2]", "{"):
            self.assertEqual(parse_resolution_reply(junk), {}, junk)


class TestVerifyWebsite(unittest.TestCase):
    """A website is accepted only when its homepage names the company."""

    def test_the_company_name_on_the_homepage_accepts_it(self) -> None:
        page = Page("https://acme.example", "ok", "Welcome to ACME, makers of widgets")
        fetcher = FakeFetcher({"https://acme.example": page})
        self.assertEqual(verify_website(fetcher, "https://acme.example", "Acme Ltd"), page)

    def test_another_company_rejects_it(self) -> None:
        page = Page("https://other.example", "ok", "Welcome to Other Co")
        fetcher = FakeFetcher({"https://other.example": page})
        self.assertIsNone(verify_website(fetcher, "https://other.example", "Acme Ltd"))

    def test_a_failed_page_rejects_it(self) -> None:
        self.assertIsNone(verify_website(FakeFetcher({}), "https://x.example", "Acme"))


class TestResolveCompany(unittest.TestCase):
    """resolve_company decides, stores, and respects manual overrides."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def tearDown(self) -> None:
        """Remove the fixture resolution."""
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM intel.job_company WHERE job_group_id = :j"), {"j": _JOB}
            )

    def test_an_employer_is_researched_under_the_posted_name(self) -> None:
        adapter = FakeAdapter([_reply("employer", None, "https://zz.example")])
        result = resolve_company(
            self.engine, make_job(), make_source(), adapters={"anthropic": adapter}
        )
        res = result.resolution
        self.assertEqual(
            (res.poster_kind, res.end_client, res.website_url, res.method),
            ("employer", "Zzfixture Intel Ltd", "https://zz.example", "employer"),
        )
        self.assertEqual(len(result.calls), 1)
        self.assertEqual(store.read_resolution(self.engine, _JOB), res)

    def test_an_agency_that_names_its_client_uses_the_client(self) -> None:
        adapter = FakeAdapter([_reply("agency", "Northern Bank", "https://bank.example")])
        res = resolve_company(
            self.engine, make_job(), make_source(), adapters={"anthropic": adapter}
        ).resolution
        self.assertEqual((res.poster_kind, res.end_client, res.method), ("agency", "Northern Bank", "llm"))

    def test_an_agency_without_a_named_client_has_no_end_client(self) -> None:
        adapter = FakeAdapter([_reply("agency", None, None)])
        res = resolve_company(
            self.engine, make_job(), make_source(), adapters={"anthropic": adapter}
        ).resolution
        self.assertIsNone(res.end_client)

    def test_an_unusable_reply_leaves_the_end_client_unknown(self) -> None:
        adapter = FakeAdapter(["I cannot tell"])
        res = resolve_company(
            self.engine, make_job(), make_source(), adapters={"anthropic": adapter}
        ).resolution
        self.assertEqual((res.poster_kind, res.end_client), ("unknown", None))

    def test_a_stored_resolution_is_reused_without_another_call(self) -> None:
        adapter = FakeAdapter([_reply("employer", None, None)])
        adapters = {"anthropic": adapter}
        resolve_company(self.engine, make_job(), make_source(), adapters=adapters)
        again = resolve_company(self.engine, make_job(), make_source(), adapters=adapters)
        self.assertEqual(len(adapter.prompts), 1)
        self.assertEqual(again.calls, [])

    def test_a_manual_override_wins_and_survives_a_later_resolution(self) -> None:
        adapter = FakeAdapter([_reply("agency", "Guess Ltd", None)])
        adapters = {"anthropic": adapter}
        manual = resolve_company(
            self.engine,
            make_job(),
            make_source(),
            adapters=adapters,
            override=ManualOverride(end_client="Real Bank", website_url="https://bank.example"),
        ).resolution
        self.assertEqual((manual.end_client, manual.website_url, manual.method), ("Real Bank", "https://bank.example", "manual"))
        later = resolve_company(self.engine, make_job(), make_source(), adapters=adapters)
        self.assertEqual(later.resolution, manual)
        self.assertEqual(adapter.prompts, [])

    def test_a_partial_override_keeps_the_other_stored_field(self) -> None:
        adapters = {"anthropic": FakeAdapter([_reply("agency", "Bank A", "https://a.example")])}
        resolve_company(self.engine, make_job(), make_source(), adapters=adapters)
        res = resolve_company(
            self.engine,
            make_job(),
            make_source(),
            adapters=adapters,
            override=ManualOverride(website_url="https://b.example"),
        ).resolution
        self.assertEqual((res.end_client, res.website_url, res.method), ("Bank A", "https://b.example", "manual"))

    def test_prompt_text_with_braces_is_safe(self) -> None:
        adapter = FakeAdapter([_reply("employer", None, None)])
        resolve_company(
            self.engine,
            make_job(description="Use {curly} and {{double}} braces"),
            make_source(),
            adapters={"anthropic": adapter},
        )
        self.assertIn("{curly} and {{double}}", adapter.prompts[0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_resolve -v`
Expected: `ModuleNotFoundError: No module named 'core.company_intel.resolve'`.

- [ ] **Step 3: Implement**

```python
"""Decide which company a job is really for (PLAN.md Step 19a).

A manual row wins. Otherwise an LLM reads the posting: is the poster the
employer or an agency, which client (if any) does the description name, and
what is the official website. A website guess is only trusted after its
homepage is fetched and names the company (`verify_website`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from core.company_intel import store
from core.company_intel.fetch import Fetcher, Page
from core.company_intel.llm import LlmTaskError, run_task
from core.company_intel.schema import Resolution
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter
from core.normalisation.company import normalise_company
from core.tailoring.schema import JobContext, JobSource

_DESCRIPTION_CHARS = 6000
_PROMPT_VERSION = 1


@dataclass(frozen=True)
class ManualOverride:
    """What the user typed to correct a job's company.

    Attributes:
        end_client: The company to research, or None to keep the stored one.
        website_url: Its official website, or None to keep the stored one.
    """

    end_client: str | None = None
    website_url: str | None = None


@dataclass(frozen=True)
class ResolveResult:
    """The outcome of resolving a job's company.

    Attributes:
        resolution: The stored resolution.
        calls: LLM usage records made now (empty when nothing was asked).
    """

    resolution: Resolution
    calls: list[dict]


def parse_resolution_reply(text: str) -> dict:
    """Read the resolution JSON object from an LLM reply.

    Args:
        text: The reply text.

    Returns:
        The decoded object, or an empty dict when it is unusable.
    """
    try:
        parsed = parse_json_response(text.strip())
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean(value: object) -> str | None:
    """Return a stripped string, or None for anything empty or non-text.

    Args:
        value: A JSON value.

    Returns:
        The text, or None for `null`, blanks and the word "null".
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return None if not text or text.lower() in {"null", "none", "unknown"} else text


def _https(value: object) -> str | None:
    """Accept only an http(s) URL.

    Args:
        value: A JSON value.

    Returns:
        The URL, or None.
    """
    url = _clean(value)
    return url if url and re.match(r"^https?://\S+$", url) else None


def resolve_company(
    engine: Engine,
    job: JobContext,
    source: JobSource,
    *,
    adapters: dict[str, LLMAdapter],
    override: ManualOverride | None = None,
    config_path: Path | None = None,
) -> ResolveResult:
    """Resolve (and store) the company a job should be researched under.

    Args:
        engine: The app-role engine.
        job: The job's title, company and description.
        source: Where the posting came from.
        adapters: Provider name to LLM adapter.
        override: A manual correction, which is stored and wins from then on.
        config_path: Task-config override (tests).

    Returns:
        The stored resolution and any LLM usage records made now.
    """
    existing = store.read_resolution(engine, job.job_group_id)
    if override is not None:
        base = existing or Resolution(
            job.job_group_id, job.company, "unknown", None, None, "manual"
        )
        manual = Resolution(
            job_group_id=job.job_group_id,
            posted_company=base.posted_company or job.company,
            poster_kind=base.poster_kind,
            end_client=_clean(override.end_client) or base.end_client,
            website_url=_https(override.website_url) or base.website_url,
            method="manual",
        )
        return ResolveResult(store.save_resolution(engine, manual), [])
    if existing is not None:
        return ResolveResult(existing, [])

    run = run_task(
        "company_resolution",
        _PROMPT_VERSION,
        {
            "title": job.title_for_display or "",
            "company": job.company or "",
            "location": "",
            "urls": ", ".join(source.source_urls) or "none",
            "description": job.description[:_DESCRIPTION_CHARS],
        },
        adapters,
        config_path=config_path,
        max_tokens=512,
    )
    data = parse_resolution_reply(run.text)
    kind = data.get("poster_kind")
    if kind not in ("employer", "agency", "unknown"):
        kind = "unknown"
    if kind == "employer":
        end_client = _clean(job.company)
        method = "employer"
    else:
        end_client = _clean(data.get("end_client"))
        method = "llm"
    resolution = Resolution(
        job_group_id=job.job_group_id,
        posted_company=job.company,
        poster_kind=kind,
        end_client=end_client,
        website_url=_https(data.get("website_url")),
        method=method,
    )
    return ResolveResult(store.save_resolution(engine, resolution), [run.record])


def verify_website(
    fetcher: Fetcher, website_url: str, company_name: str
) -> Page | None:
    """Fetch a candidate homepage and check it names the company.

    Args:
        fetcher: Fetches the homepage.
        website_url: The candidate website.
        company_name: The company the website should belong to.

    Returns:
        The homepage when it loads and its text contains the company's
        normalised name (ignoring case and spacing), else None.
    """
    page = fetcher.fetch(website_url)
    if page.status != "ok":
        return None
    needle = re.sub(r"\s+", " ", (normalise_company(company_name) or "")).casefold()
    haystack = re.sub(r"\s+", " ", page.markdown).casefold()
    return page if needle and needle in haystack else None
```
(`LlmTaskError` is imported for the service in Task 8; remove the unused import now if ruff flags it and re-add there.)

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_resolve -v` → `OK`.

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/company_intel/resolve.py tests/integration/test_company_intel_resolve.py && cd ../..
git add packages/core/core/company_intel/resolve.py packages/core/tests/integration/test_company_intel_resolve.py
git commit -m "feat(job_search): resolve a job's end client (LLM + manual override)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Extraction with the fabrication guard

**Files:**
- Create: `packages/core/core/company_intel/extract.py`
- Create: `prompts/company_intel_extraction/claude.v1.md`
- Test: `packages/core/tests/test_company_intel_extract.py`

**Interfaces:**
- Consumes: `llm.run_task`, `schema.*`, `fetch.Page`.
- Produces:
  - `guard_content(raw: object, crawled: set[str]) -> IntelContent` (applies every rule below)
  - `ExtractResult(content: IntelContent, model: str, prompt_version: str, calls: list[dict])`
  - `extract_intel(display_name: str, pages: list[Page], previous: IntelContent | None, *, adapters, config_path=None) -> ExtractResult`
  - `ExtractError(Exception)` for an unusable reply.

Guard rules (each has a test): a fact keeps only if `text` is non-blank and `source_url` is in `crawled`; competitors: a name is required, de-duplicated case-insensitively, a `site` competitor keeps only if its `source_url` is in `crawled`, a `model_knowledge` competitor has its URL cleared, `basis` other than the two values becomes `model_knowledge`, at most `MAX_COMPETITORS` kept (first ones win); unknown top-level keys are ignored; a non-list field is treated as empty.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for extracting cited company facts."""

from __future__ import annotations

import json
import unittest

from tests.company_intel_fixtures import FakeAdapter

from core.company_intel.extract import (
    ExtractError,
    extract_intel,
    guard_content,
)
from core.company_intel.fetch import Page
from core.company_intel.schema import IntelContent, IntelFact

_ABOUT = "https://acme.example/about"
_JOBS = "https://acme.example/careers"


def _raw(**fields: object) -> dict:
    """Build a raw extraction object.

    Args:
        **fields: Field values.

    Returns:
        The dict.
    """
    return dict(fields)


class TestGuard(unittest.TestCase):
    """guard_content drops anything the crawl does not support."""

    def test_facts_citing_uncrawled_urls_are_dropped(self) -> None:
        content = guard_content(
            _raw(
                business_model=[
                    {"text": "Sells widgets.", "source_url": _ABOUT},
                    {"text": "Raised $9bn.", "source_url": "https://news.example/x"},
                    {"text": "   ", "source_url": _ABOUT},
                    {"text": "No url."},
                ]
            ),
            {_ABOUT},
        )
        self.assertEqual(
            content.business_model, [IntelFact(text="Sells widgets.", source_url=_ABOUT)]
        )

    def test_more_than_five_competitors_keeps_the_first_five(self) -> None:
        competitors = [
            {"name": f"Rival {i}", "basis": "model_knowledge"} for i in range(8)
        ]
        content = guard_content(_raw(competitors=competitors), {_ABOUT})
        self.assertEqual([c.name for c in content.competitors], [f"Rival {i}" for i in range(5)])

    def test_model_knowledge_competitors_lose_any_url(self) -> None:
        content = guard_content(
            _raw(competitors=[{"name": "Rival", "basis": "model_knowledge", "source_url": _ABOUT}]),
            {_ABOUT},
        )
        self.assertEqual(content.competitors[0].source_url, None)
        self.assertEqual(content.competitors[0].basis, "model_knowledge")

    def test_a_site_competitor_needs_a_crawled_url(self) -> None:
        content = guard_content(
            _raw(
                competitors=[
                    {"name": "Good", "basis": "site", "source_url": _ABOUT},
                    {"name": "Bad", "basis": "site", "source_url": "https://x.example"},
                    {"name": "NoUrl", "basis": "site"},
                ]
            ),
            {_ABOUT},
        )
        self.assertEqual([c.name for c in content.competitors], ["Good"])

    def test_competitors_are_deduplicated_and_unknown_basis_is_unverified(self) -> None:
        content = guard_content(
            _raw(
                competitors=[
                    {"name": "Rival", "basis": "weird"},
                    {"name": "rival", "basis": "model_knowledge"},
                    {"name": "", "basis": "model_knowledge"},
                ]
            ),
            set(),
        )
        self.assertEqual([(c.name, c.basis) for c in content.competitors], [("Rival", "model_knowledge")])

    def test_bad_shapes_give_empty_fields_not_errors(self) -> None:
        content = guard_content(
            _raw(business_model="oops", tech_stack=[1, None, "x"], extra=[1]), {_ABOUT}
        )
        self.assertTrue(content.is_empty())
        self.assertTrue(guard_content("not a dict", set()).is_empty())


class TestExtractIntel(unittest.TestCase):
    """extract_intel builds the prompt, calls the model and guards the reply."""

    def _pages(self) -> list[Page]:
        """Build two crawled pages, one containing braces.

        Returns:
            Two `ok` pages.
        """
        return [
            Page(_ABOUT, "ok", "Acme makes widgets. {not a placeholder}"),
            Page(_JOBS, "ok", "We use dbt and Snowflake."),
        ]

    def test_it_returns_guarded_content_and_usage(self) -> None:
        reply = json.dumps(
            {
                "business_model": [{"text": "Makes widgets.", "source_url": _ABOUT}],
                "tech_stack": [{"text": "dbt, Snowflake.", "source_url": _JOBS}],
                "competitors": [{"name": "Rival", "why": "Same market", "basis": "model_knowledge", "source_url": None}],
            }
        )
        adapter = FakeAdapter([reply])
        result = extract_intel("Acme", self._pages(), None, adapters={"anthropic": adapter})
        self.assertEqual(result.content.tech_stack[0].source_url, _JOBS)
        self.assertEqual(result.content.competitors[0].name, "Rival")
        self.assertEqual(result.prompt_version, "claude.v1")
        self.assertEqual(result.calls[0]["task"], "company_intel_extraction")

    def test_page_text_with_braces_reaches_the_prompt_intact(self) -> None:
        adapter = FakeAdapter(["{}"])
        extract_intel("Acme", self._pages(), None, adapters={"anthropic": adapter})
        prompt = adapter.prompts[0]
        self.assertIn("{not a placeholder}", prompt)
        self.assertIn(_ABOUT, prompt)
        self.assertIn(_JOBS, prompt)

    def test_the_previous_profile_is_offered_on_a_refresh(self) -> None:
        previous = IntelContent(
            business_model=[IntelFact(text="Old claim.", source_url=_ABOUT)]
        )
        adapter = FakeAdapter(["{}"])
        extract_intel("Acme", self._pages(), previous, adapters={"anthropic": adapter})
        self.assertIn("Old claim.", adapter.prompts[0])
        adapter2 = FakeAdapter(["{}"])
        extract_intel("Acme", self._pages(), None, adapters={"anthropic": adapter2})
        self.assertNotIn("Previous profile", adapter2.prompts[0])

    def test_an_unreadable_reply_is_an_error(self) -> None:
        with self.assertRaises(ExtractError):
            extract_intel(
                "Acme", self._pages(), None, adapters={"anthropic": FakeAdapter(["no json"])}
            )

    def test_a_long_page_is_cut_before_it_is_sent(self) -> None:
        adapter = FakeAdapter(["{}"])
        long_page = Page(_ABOUT, "ok", "x" * 50_000)
        extract_intel("Acme", [long_page], None, adapters={"anthropic": adapter})
        self.assertLess(len(adapter.prompts[0]), 20_000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run (from `packages/core`): `../../venv/bin/python -m unittest tests.test_company_intel_extract -v` → `ModuleNotFoundError`.

- [ ] **Step 3: Write the prompt**

`prompts/company_intel_extraction/claude.v1.md`:
```markdown
You build a short, factual company profile from the pages below. Use ONLY the text of the pages for facts.

Company: {company}

{previous}

Pages (each starts with its URL):
{pages}

Rules:
- Every item in the fact lists must cite, in "source_url", the exact URL of the page its text comes from. If no page supports a statement, leave it out. An empty list is fine.
- Do not add numbers, dates, funding figures or names that are not in the pages. Keep each "text" to one or two sentences.
- "business_model": what the company sells and to whom. "size_signals": headcount, offices, growth wording. "tech_stack": technologies the pages mention. "recent_announcements": news the pages date or describe as recent (include the date if the page gives one). "notes": anything else useful for a cover letter.
- "competitors": up to 5 companies that compete with {company}. Use basis "site" (and cite the page in "source_url") only if the pages name or clearly imply the competitor. Otherwise use basis "model_knowledge" with "source_url": null, from your general knowledge - only well-known real companies you are confident compete in the same market. Fewer than 5 is fine. "why" is one sentence.

Respond with ONLY a JSON object, no other text:
{{"business_model": [{{"text": "...", "source_url": "..."}}], "size_signals": [], "tech_stack": [], "recent_announcements": [], "notes": [], "competitors": [{{"name": "...", "why": "...", "basis": "site|model_knowledge", "source_url": "<url or null>"}}]}}
```

- [ ] **Step 4: Implement**

```python
"""Extract cited company facts from crawled pages (PLAN.md Step 19a).

The model reads only the crawled text. Everything it returns passes through
`guard_content`: a fact must cite a page that was really crawled, competitors
are capped and tagged, and anything malformed is dropped rather than
repaired.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.company_intel.fetch import Page
from core.company_intel.llm import run_task
from core.company_intel.schema import (
    MAX_COMPETITORS,
    Competitor,
    IntelContent,
    IntelFact,
)
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter

_PROMPT_VERSION = 1
_PAGE_CHARS = 6000
_FACT_FIELDS = (
    "business_model",
    "size_signals",
    "tech_stack",
    "recent_announcements",
    "notes",
)


class ExtractError(Exception):
    """The model's reply could not be read as a profile."""


@dataclass(frozen=True)
class ExtractResult:
    """A guarded profile and what producing it cost.

    Attributes:
        content: The profile after the fabrication guard.
        model: The model that wrote it.
        prompt_version: The prompt version used.
        calls: LLM usage records.
    """

    content: IntelContent
    model: str
    prompt_version: str
    calls: list[dict]


def _facts(raw: object, crawled: set[str]) -> list[IntelFact]:
    """Keep the well-formed, properly cited facts of one list.

    Args:
        raw: The model's value for one fact field.
        crawled: The URLs that were actually crawled.

    Returns:
        The facts whose text is non-blank and whose `source_url` was crawled.
    """
    if not isinstance(raw, list):
        return []
    facts: list[IntelFact] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text, url = item.get("text"), item.get("source_url")
        if (
            isinstance(text, str)
            and text.strip()
            and isinstance(url, str)
            and url in crawled
        ):
            facts.append(IntelFact(text=text.strip(), source_url=url))
    return facts


def _competitors(raw: object, crawled: set[str]) -> list[Competitor]:
    """Apply the competitor rules.

    Args:
        raw: The model's `competitors` value.
        crawled: The URLs that were actually crawled.

    Returns:
        At most `MAX_COMPETITORS` named, de-duplicated competitors. A `site`
        competitor needs a crawled URL; anything else is `model_knowledge`
        with no URL.
    """
    if not isinstance(raw, list):
        return []
    kept: list[Competitor] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        if name.strip().casefold() in seen:
            continue
        why = item.get("why") if isinstance(item.get("why"), str) else ""
        url = item.get("source_url") if isinstance(item.get("source_url"), str) else None
        if item.get("basis") == "site":
            if url not in crawled:
                continue
            competitor = Competitor(name=name.strip(), why=why, basis="site", source_url=url)
        else:
            competitor = Competitor(name=name.strip(), why=why, basis="model_knowledge")
        seen.add(name.strip().casefold())
        kept.append(competitor)
        if len(kept) == MAX_COMPETITORS:
            break
    return kept


def guard_content(raw: object, crawled: set[str]) -> IntelContent:
    """Apply the fabrication guard to a raw model reply.

    Args:
        raw: The decoded JSON reply.
        crawled: The URLs that were actually crawled.

    Returns:
        The profile with every unsupported or malformed item removed.
    """
    if not isinstance(raw, dict):
        return IntelContent()
    fields = {name: _facts(raw.get(name), crawled) for name in _FACT_FIELDS}
    return IntelContent(competitors=_competitors(raw.get("competitors"), crawled), **fields)


def _render_pages(pages: list[Page]) -> str:
    """Lay the pages out for the prompt, each cut to a safe length.

    Args:
        pages: The crawled pages.

    Returns:
        The labelled page texts.
    """
    return "\n\n".join(
        f"=== {page.url}\n{page.markdown[:_PAGE_CHARS]}" for page in pages
    )


def extract_intel(
    display_name: str,
    pages: list[Page],
    previous: IntelContent | None,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> ExtractResult:
    """Ask the model for a profile and guard it.

    Args:
        display_name: The company's name.
        pages: The successfully crawled pages.
        previous: The last stored profile, offered as context on a refresh.
        adapters: Provider name to LLM adapter.
        config_path: Task-config override (tests).

    Returns:
        The guarded profile and its usage.

    Raises:
        ExtractError: If the reply is not a JSON object.
    """
    previous_block = ""
    if previous is not None and not previous.is_empty():
        previous_block = (
            "Previous profile (update it: keep what the pages still support, drop "
            "what they contradict, add what is new):\n"
            + previous.model_dump_json()
        )
    run = run_task(
        "company_intel_extraction",
        _PROMPT_VERSION,
        {
            "company": display_name,
            "previous": previous_block,
            "pages": _render_pages(pages),
        },
        adapters,
        config_path=config_path,
        max_tokens=4096,
    )
    try:
        raw = parse_json_response(run.text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ExtractError(f"the profile reply was not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExtractError("the profile reply was not a JSON object")
    crawled = {page.url for page in pages}
    return ExtractResult(
        content=guard_content(raw, crawled),
        model=run.model,
        prompt_version=run.prompt_version,
        calls=[run.record],
    )
```

- [ ] **Step 5: Run to verify it passes**

Run (from `packages/core`): `../../venv/bin/python -m unittest tests.test_company_intel_extract -v` → `OK`.

- [ ] **Step 6: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/company_intel/extract.py tests/test_company_intel_extract.py && cd ../..
git add packages/core/core/company_intel/extract.py packages/core/tests/test_company_intel_extract.py prompts/company_intel_extraction
git commit -m "feat(job_search): extract cited company facts and competitors with a fabrication guard

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The service — `get_or_refresh_intel`

**Files:**
- Create: `packages/core/core/company_intel/service.py`
- Test: `packages/core/tests/integration/test_company_intel_service.py`

**Interfaces:**
- Consumes: Tasks 2–7.
- Produces:
  - `IntelOutcome = Literal["reused", "refreshed", "no_content", "failed", "end_client_unknown", "website_unverified"]`
  - `IntelResult(outcome: IntelOutcome, resolution: Resolution, intel: StoredIntel | None, message: str)` frozen dataclass
  - `is_fresh(researched_at: datetime, now: datetime, refresh_after_days: int) -> bool`
  - `get_or_refresh_intel(engine, job: JobContext, source: JobSource, *, adapters, fetcher, config: CompanyIntelConfig, force: bool = False, override: ManualOverride | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC), config_path: Path | None = None) -> IntelResult`

Flow: resolve → (no end client → `end_client_unknown`) → key → latest; fresh and not forced → `reused` → website missing or unverified → `website_unverified` → fetch home + `pick_pages` pages → no ok page → `failed` (nothing written) → extract (error → `failed`) → append (`no_content` if the profile is empty else `ok`) → `refreshed` / `no_content`. Usage = resolution calls + extraction calls.

- [ ] **Step 1: Write the failing tests**

```python
"""Integration tests for get_or_refresh_intel (real Postgres, fake web + LLM)."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from tests.company_intel_fixtures import FakeAdapter, FakeFetcher, make_job, make_source
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.company_intel import store
from core.company_intel.config import CompanyIntelConfig
from core.company_intel.fetch import Page
from core.company_intel.keys import company_key
from core.company_intel.resolve import ManualOverride
from core.company_intel.service import get_or_refresh_intel, is_fresh

_JOB = "zzfixture-intel-job"
_SITE = "https://zzfixture-intel.example/"
_ABOUT = "https://zzfixture-intel.example/about"
_NAME = "Zzfixture Intel Ltd"
_CONFIG = CompanyIntelConfig(refresh_after_days=90, crawl_cache_days=7, max_pages=4)
_NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def _resolution_reply(kind: str = "employer", client: str | None = None) -> str:
    """Build a resolution reply.

    Args:
        kind: The poster kind.
        client: The end client for an agency.

    Returns:
        A JSON string naming the fixture website.
    """
    return json.dumps({"poster_kind": kind, "end_client": client, "website_url": _SITE})


def _profile_reply(fact: str = "Makes widgets.") -> str:
    """Build an extraction reply.

    Args:
        fact: The business-model fact text.

    Returns:
        A JSON string citing the about page.
    """
    return json.dumps(
        {
            "business_model": [{"text": fact, "source_url": _ABOUT}],
            "competitors": [{"name": "Rival", "basis": "model_knowledge"}],
        }
    )


def _web() -> FakeFetcher:
    """Build a fake site: a homepage naming the company and an about page.

    Returns:
        The fake fetcher.
    """
    return FakeFetcher(
        {
            _SITE: Page(_SITE, "ok", f"Welcome to {_NAME}", (_ABOUT,)),
            _ABOUT: Page(_ABOUT, "ok", "We make widgets."),
        }
    )


class TestIsFresh(unittest.TestCase):
    """is_fresh compares age with the refresh threshold."""

    def test_inside_and_outside_the_window(self) -> None:
        self.assertTrue(is_fresh(_NOW - timedelta(days=89), _NOW, 90))
        self.assertFalse(is_fresh(_NOW - timedelta(days=91), _NOW, 90))


class TestGetOrRefreshIntel(unittest.TestCase):
    """The research flow, end to end, with fakes at the edges."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()
        cls.key = company_key(_NAME)

    def tearDown(self) -> None:
        """Remove every fixture row."""
        with self.owner.begin() as conn:
            for table, col in (
                ("company_intel", "company_key"),
                ("job_company", "job_group_id"),
                ("crawl_cache", "url"),
            ):
                value = self.key if col == "company_key" else (_JOB if col == "job_group_id" else "https://zzfixture-intel.example%")
                op = "LIKE" if col == "url" else "="
                conn.execute(text(f"DELETE FROM intel.{table} WHERE {col} {op} :v"), {"v": value})

    def _run(self, adapter: FakeAdapter, web: FakeFetcher, **kwargs):  # type: ignore[no-untyped-def]
        """Call the service with the standard fixtures.

        Args:
            adapter: The scripted LLM.
            web: The fake web.
            **kwargs: Extra arguments for the service.

        Returns:
            The `IntelResult`.
        """
        return get_or_refresh_intel(
            self.engine,
            make_job(),
            make_source(),
            adapters={"anthropic": adapter},
            fetcher=web,
            config=_CONFIG,
            now=lambda: _NOW,
            **kwargs,
        )

    def test_the_first_research_crawls_extracts_and_stores_version_one(self) -> None:
        adapter = FakeAdapter([_resolution_reply(), _profile_reply()])
        result = self._run(adapter, _web())
        self.assertEqual(result.outcome, "refreshed")
        assert result.intel is not None
        self.assertEqual((result.intel.version, result.intel.status), (1, "ok"))
        self.assertEqual(result.intel.content.business_model[0].source_url, _ABOUT)
        self.assertEqual([p["url"] for p in result.intel.pages], [_SITE, _ABOUT])
        self.assertEqual(len(result.intel.usage["calls"]), 2)
        self.assertEqual(result.intel.resolved_from_job, _JOB)

    def test_a_second_research_reuses_the_stored_version(self) -> None:
        adapter = FakeAdapter([_resolution_reply(), _profile_reply()])
        web = _web()
        self._run(adapter, web)
        calls_before, fetched_before = len(adapter.prompts), len(web.requested)
        result = self._run(adapter, web)
        self.assertEqual(result.outcome, "reused")
        assert result.intel is not None
        self.assertEqual(result.intel.version, 1)
        self.assertEqual((len(adapter.prompts), len(web.requested)), (calls_before, fetched_before))

    def test_a_stale_version_is_refreshed_with_the_previous_one_as_context(self) -> None:
        adapter = FakeAdapter([_resolution_reply(), _profile_reply("Old claim.")])
        self._run(adapter, _web())
        later = _NOW + timedelta(days=100)
        adapter2 = FakeAdapter([_profile_reply("New claim.")])
        result = get_or_refresh_intel(
            self.engine, make_job(), make_source(), adapters={"anthropic": adapter2},
            fetcher=_web(), config=_CONFIG, now=lambda: later,
        )
        self.assertEqual(result.outcome, "refreshed")
        assert result.intel is not None
        self.assertEqual(result.intel.version, 2)
        self.assertIn("Old claim.", adapter2.prompts[0])
        old = store.read_intel_version(self.engine, self.key, 1)
        assert old is not None
        self.assertEqual(old.content.business_model[0].text, "Old claim.")

    def test_force_refreshes_a_fresh_version(self) -> None:
        adapter = FakeAdapter([_resolution_reply(), _profile_reply()])
        self._run(adapter, _web())
        result = self._run(FakeAdapter([_profile_reply("Again.")]), _web(), force=True)
        self.assertEqual(result.outcome, "refreshed")
        assert result.intel is not None
        self.assertEqual(result.intel.version, 2)

    def test_an_agency_with_no_named_client_is_not_crawled(self) -> None:
        adapter = FakeAdapter([_resolution_reply("agency", None)])
        web = _web()
        result = self._run(adapter, web)
        self.assertEqual(result.outcome, "end_client_unknown")
        self.assertIsNone(result.intel)
        self.assertEqual(web.requested, [])
        self.assertEqual(len(adapter.prompts), 1)

    def test_a_website_that_does_not_name_the_company_is_rejected(self) -> None:
        web = FakeFetcher({_SITE: Page(_SITE, "ok", "Welcome to Somebody Else")})
        result = self._run(FakeAdapter([_resolution_reply()]), web)
        self.assertEqual(result.outcome, "website_unverified")
        self.assertIsNone(store.read_latest_intel(self.engine, self.key))

    def test_no_website_at_all_is_unverified(self) -> None:
        reply = json.dumps({"poster_kind": "employer", "end_client": None, "website_url": None})
        result = self._run(FakeAdapter([reply]), _web())
        self.assertEqual(result.outcome, "website_unverified")

    def test_a_manual_website_fixes_a_rejected_one(self) -> None:
        self._run(FakeAdapter([_resolution_reply()]), FakeFetcher({_SITE: Page(_SITE, "ok", "Nope")}))
        result = self._run(
            FakeAdapter([_profile_reply()]),
            _web(),
            override=ManualOverride(website_url=_SITE),
        )
        self.assertEqual(result.outcome, "refreshed")
        self.assertEqual(result.resolution.method, "manual")

    def test_if_every_page_fails_nothing_is_written_and_the_old_version_stays(self) -> None:
        self._run(FakeAdapter([_resolution_reply(), _profile_reply()]), _web())
        later = _NOW + timedelta(days=100)
        down = FakeFetcher({})
        result = get_or_refresh_intel(
            self.engine, make_job(), make_source(), adapters={"anthropic": FakeAdapter(["{}"])},
            fetcher=down, config=_CONFIG, now=lambda: later,
        )
        self.assertEqual(result.outcome, "website_unverified")
        latest = store.read_latest_intel(self.engine, self.key)
        assert latest is not None
        self.assertEqual(latest.version, 1)

    def test_pages_that_yield_nothing_are_stored_as_no_content(self) -> None:
        result = self._run(FakeAdapter([_resolution_reply(), "{}"]), _web())
        self.assertEqual(result.outcome, "no_content")
        assert result.intel is not None
        self.assertEqual(result.intel.status, "no_content")

    def test_an_unreadable_extraction_is_a_failure_and_writes_nothing(self) -> None:
        result = self._run(FakeAdapter([_resolution_reply(), "not json"]), _web())
        self.assertEqual(result.outcome, "failed")
        self.assertIsNone(store.read_latest_intel(self.engine, self.key))

    def test_at_most_max_pages_are_crawled(self) -> None:
        links = tuple(f"https://zzfixture-intel.example/blog/p{i}" for i in range(10))
        pages = {_SITE: Page(_SITE, "ok", f"Welcome to {_NAME}", links)}
        pages.update({u: Page(u, "ok", "post") for u in links})
        web = FakeFetcher(pages)
        self._run(FakeAdapter([_resolution_reply(), "{}"]), web)
        self.assertLessEqual(len(web.requested), _CONFIG.max_pages)


if __name__ == "__main__":
    unittest.main()
```
(In `test_if_every_page_fails…` the homepage itself cannot be fetched, so the verification step fails first and the outcome is `website_unverified`; the previous version is untouched either way. The `failed` outcome for "homepage verified but a later fetch set empty" is impossible by construction — the verified homepage is itself an ok page — so `failed` is reserved for extraction errors.)

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_service -v` → `ModuleNotFoundError: ... service`.

- [ ] **Step 3: Implement**

```python
"""The company-intel service: reuse stored intel or research and store a new
version (PLAN.md Step 19a). Steps 19 and 20a call `get_or_refresh_intel`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine

from core.company_intel import store
from core.company_intel.config import CompanyIntelConfig
from core.company_intel.discover import pick_pages
from core.company_intel.extract import ExtractError, extract_intel
from core.company_intel.fetch import Fetcher, Page
from core.company_intel.keys import company_key
from core.company_intel.llm import LlmTaskError, total_usage
from core.company_intel.resolve import ManualOverride, resolve_company, verify_website
from core.company_intel.schema import Resolution, StoredIntel
from core.llm.types import LLMAdapter
from core.tailoring.schema import JobContext, JobSource

IntelOutcome = Literal[
    "reused",
    "refreshed",
    "no_content",
    "failed",
    "end_client_unknown",
    "website_unverified",
]


@dataclass(frozen=True)
class IntelResult:
    """What a research call did.

    Attributes:
        outcome: One of `IntelOutcome`.
        resolution: Which company was (or could not be) researched.
        intel: The current stored intel, or None when there is none.
        message: A one-line explanation for the user.
    """

    outcome: IntelOutcome
    resolution: Resolution
    intel: StoredIntel | None
    message: str


def is_fresh(researched_at: datetime, now: datetime, refresh_after_days: int) -> bool:
    """Say whether intel is still inside its refresh window.

    Args:
        researched_at: When the version was written.
        now: The current time.
        refresh_after_days: The window in days.

    Returns:
        True when the version is younger than the window.
    """
    return now - researched_at < timedelta(days=refresh_after_days)


def _utc_now() -> datetime:
    """Return the current UTC time.

    Returns:
        A timezone-aware datetime.
    """
    return datetime.now(UTC)


def _page_record(page: Page) -> dict:
    """Describe a crawled page for the stored provenance.

    Args:
        page: A fetched page.

    Returns:
        `{url, status, error}`.
    """
    return {"url": page.url, "status": page.status, "error": page.error}


def get_or_refresh_intel(
    engine: Engine,
    job: JobContext,
    source: JobSource,
    *,
    adapters: dict[str, LLMAdapter],
    fetcher: Fetcher,
    config: CompanyIntelConfig,
    force: bool = False,
    override: ManualOverride | None = None,
    now: Callable[[], datetime] = _utc_now,
    config_path: Path | None = None,
) -> IntelResult:
    """Return a job's company intel, researching only when needed.

    Args:
        engine: The app-role engine.
        job: The job being applied to.
        source: Where the posting came from.
        adapters: Provider name to LLM adapter.
        fetcher: Fetches web pages (wrap it in `CachedFetcher` to cache).
        config: Freshness and crawl limits.
        force: Research again even if the stored version is fresh.
        override: A manual end client / website to store first.
        now: Clock for tests.
        config_path: Task-config override (tests).

    Returns:
        The outcome, the resolution and the current intel. If every page
        fails nothing is written and the previous version stays current.
    """
    resolved = resolve_company(
        engine, job, source, adapters=adapters, override=override, config_path=config_path
    )
    resolution, calls = resolved.resolution, list(resolved.calls)
    if resolution.end_client is None:
        return IntelResult(
            "end_client_unknown",
            resolution,
            None,
            "The posting does not name the end client; set it to research it.",
        )
    key = company_key(resolution.end_client)
    if key is None:
        return IntelResult(
            "end_client_unknown", resolution, None, "The company name is unusable."
        )
    latest = store.read_latest_intel(engine, key)
    if latest and not force and is_fresh(latest.researched_at, now(), config.refresh_after_days):
        return IntelResult("reused", resolution, latest, "Stored intel is still fresh.")

    home = (
        verify_website(fetcher, resolution.website_url, resolution.end_client)
        if resolution.website_url
        else None
    )
    if home is None:
        return IntelResult(
            "website_unverified",
            resolution,
            latest,
            "No verified website for this company; enter its website to research it.",
        )
    pages: list[Page] = [home]
    for url in pick_pages(home.url, home.links, config.max_pages)[1:]:
        pages.append(fetcher.fetch(url))
    good = [page for page in pages if page.status == "ok"]

    try:
        extracted = extract_intel(
            resolution.end_client,
            good,
            latest.content if latest else None,
            adapters=adapters,
            config_path=config_path,
        )
    except (ExtractError, LlmTaskError) as exc:
        return IntelResult("failed", resolution, latest, f"Research failed: {exc}")
    calls.extend(extracted.calls)
    status = "no_content" if extracted.content.is_empty() else "ok"
    store.append_intel(
        engine,
        company_key=key,
        display_name=resolution.end_client,
        status=status,
        content=extracted.content,
        pages=[_page_record(page) for page in pages],
        resolved_from_job=job.job_group_id,
        model=extracted.model,
        prompt_version=extracted.prompt_version,
        usage=total_usage(calls),
    )
    stored = store.read_latest_intel(engine, key)
    outcome: IntelOutcome = "no_content" if status == "no_content" else "refreshed"
    return IntelResult(outcome, resolution, stored, "Researched and stored a new version.")
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_service -v` → `OK` (13 tests).

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/company_intel/service.py tests/integration/test_company_intel_service.py && cd ../..
git add packages/core/core/company_intel/service.py packages/core/tests/integration/test_company_intel_service.py
git commit -m "feat(job_search): get_or_refresh_intel — reuse fresh intel, otherwise research and version it

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The competitive angle

**Files:**
- Create: `packages/core/core/company_intel/angle.py`
- Create: `prompts/competitive_angle/claude.v1.md`
- Test: `packages/core/tests/integration/test_company_intel_angle.py`

**Interfaces:**
- Consumes: `llm.run_task`, `store.read_angle/append_angle`, `schema.*`.
- Produces:
  - `guard_angle(raw: object, competitor_names: set[str]) -> AngleContent` (drops advantages that name an unlisted competitor; keeps at most 5; `competitor == ""` allowed)
  - `get_or_create_angle(engine, job: JobContext, intel: StoredIntel, *, adapters, force: bool = False, config_path: Path | None = None) -> StoredAngle | None` (returns the stored angle for `intel.version` if present and not forced; otherwise writes one; `None` if the intel is `no_content`/has nothing to reason from or the reply is unusable).

- [ ] **Step 1: Write the failing tests**

```python
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
    content = IntelContent() if empty else IntelContent(
        business_model=[IntelFact(text="Sells widgets.", source_url="https://x.example")],
        competitors=[Competitor(name="Rival", basis="model_knowledge")],
    )
    return StoredIntel(
        company_key=_KEY, version=version, display_name="Zz Co", status=status,  # type: ignore[arg-type]
        researched_at=datetime(2026, 10, 14, tzinfo=UTC), content=content, pages=[],
        resolved_from_job=_JOB, model="m", prompt_version="claude.v1", usage={},
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
            "advantages": advantages
            if advantages is not None
            else [{"competitor": "Rival", "point": "Fresher data for customers."}],
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
        self.assertEqual([(a.competitor, a.point) for a in content.advantages], [("Rival", "A."), ("", "C.")])

    def test_at_most_five_advantages(self) -> None:
        items = [{"competitor": "", "point": f"p{i}"} for i in range(9)]
        self.assertEqual(len(guard_angle({"advantages": items}, set()).advantages), 5)

    def test_junk_gives_an_empty_angle(self) -> None:
        self.assertEqual(guard_angle("x", set()).advantages, [])
        self.assertEqual(guard_angle({"transformation": 5, "caveats": "no"}, set()).transformation, "")


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
        first = get_or_create_angle(self.engine, make_job(), _intel(1), adapters=adapters)
        again = get_or_create_angle(self.engine, make_job(), _intel(1), adapters=adapters)
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
        second = get_or_create_angle(self.engine, make_job(), _intel(2), adapters=adapters)
        assert second is not None
        self.assertEqual((len(adapter.prompts), second.intel_version), (2, 2))

    def test_force_writes_another_angle_for_the_same_version(self) -> None:
        adapter = FakeAdapter([_reply()])
        adapters = {"anthropic": adapter}
        get_or_create_angle(self.engine, make_job(), _intel(1), adapters=adapters)
        get_or_create_angle(self.engine, make_job(), _intel(1), adapters=adapters, force=True)
        self.assertEqual(len(adapter.prompts), 2)

    def test_an_empty_intel_gives_no_angle_and_no_call(self) -> None:
        adapter = FakeAdapter([_reply()])
        found = get_or_create_angle(
            self.engine, make_job(), _intel(1, "no_content", empty=True), adapters={"anthropic": adapter}
        )
        self.assertIsNone(found)
        self.assertEqual(adapter.prompts, [])

    def test_an_unreadable_reply_gives_no_angle(self) -> None:
        found = get_or_create_angle(
            self.engine, make_job(), _intel(1), adapters={"anthropic": FakeAdapter(["nope"])}
        )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_angle -v` → `ModuleNotFoundError: ... angle`.

- [ ] **Step 3: Write the prompt**

`prompts/competitive_angle/claude.v1.md`:
```markdown
You help a candidate decide how to position themselves for one job. Using ONLY the job description and the company profile below, write a short analysis. This is analysis, not fact: do not invent figures, events or capabilities of the company or its competitors.

Job: {title} at {company}

Job description:
{description}

Company profile (JSON; every fact cites its source):
{profile}

Listed competitors: {competitors}

Write:
- "transformation": 2-3 sentences on what change or transformation the job description implies for the company (what they are building, replacing or scaling).
- "advantages": up to 5 items. Each says how that change could put the company ahead of ONE listed competitor (put its exact name in "competitor") or of the field in general ("competitor": ""). Each "point" is one sentence grounded in the description or the profile; if you cannot ground a point, leave it out.
- "caveats": 1-3 short notes on what is uncertain.

Respond with ONLY a JSON object, no other text:
{{"transformation": "...", "advantages": [{{"competitor": "...", "point": "..."}}], "caveats": ["..."]}}
```

- [ ] **Step 4: Implement**

```python
"""The per-job competitive angle: how this job, and the transformation it
implies, could put the company ahead of its competitors (PLAN.md Step 19a).

It is analysis, not fact. The prompt may use only the job description and
the stored intel; `guard_angle` drops any point that names a competitor the
intel does not list.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Engine

from core.company_intel import store
from core.company_intel.llm import LlmTaskError, run_task, total_usage
from core.company_intel.schema import (
    AngleAdvantage,
    AngleContent,
    StoredAngle,
    StoredIntel,
)
from core.llm.json_response import parse_json_response
from core.llm.types import LLMAdapter
from core.tailoring.schema import JobContext

_PROMPT_VERSION = 1
_DESCRIPTION_CHARS = 6000
_MAX_ADVANTAGES = 5


def guard_angle(raw: object, competitor_names: set[str]) -> AngleContent:
    """Keep only well-formed, grounded items of a model reply.

    Args:
        raw: The decoded JSON reply.
        competitor_names: Lower-cased names of the listed competitors.

    Returns:
        The angle; an advantage naming an unlisted competitor, or with no
        point, is dropped, and at most five are kept.
    """
    if not isinstance(raw, dict):
        return AngleContent()
    transformation = raw.get("transformation")
    advantages: list[AngleAdvantage] = []
    items = raw.get("advantages")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        competitor = item.get("competitor")
        competitor = competitor.strip() if isinstance(competitor, str) else ""
        point = item.get("point")
        if not isinstance(point, str) or not point.strip():
            continue
        if competitor and competitor.casefold() not in competitor_names:
            continue
        advantages.append(AngleAdvantage(competitor=competitor, point=point.strip()))
        if len(advantages) == _MAX_ADVANTAGES:
            break
    caveats = raw.get("caveats")
    return AngleContent(
        transformation=transformation.strip() if isinstance(transformation, str) else "",
        advantages=advantages,
        caveats=[c.strip() for c in caveats if isinstance(c, str) and c.strip()]
        if isinstance(caveats, list)
        else [],
    )


def get_or_create_angle(
    engine: Engine,
    job: JobContext,
    intel: StoredIntel,
    *,
    adapters: dict[str, LLMAdapter],
    force: bool = False,
    config_path: Path | None = None,
) -> StoredAngle | None:
    """Return the job's angle for this intel version, writing it if missing.

    Args:
        engine: The app-role engine.
        job: The job (title, company, description).
        intel: The stored intel the angle is built from.
        adapters: Provider name to LLM adapter.
        force: Write a new angle even if one exists for this version.
        config_path: Task-config override (tests).

    Returns:
        The stored angle, or None when the intel holds nothing to reason
        from or the model's reply is unusable.
    """
    if not force:
        existing = store.read_angle(
            engine, job.job_group_id, intel.company_key, intel.version
        )
        if existing is not None:
            return existing
    if intel.content.is_empty():
        return None
    names = [c.name for c in intel.content.competitors]
    try:
        run = run_task(
            "competitive_angle",
            _PROMPT_VERSION,
            {
                "title": job.title_for_display or "",
                "company": intel.display_name,
                "description": job.description[:_DESCRIPTION_CHARS],
                "profile": intel.content.model_dump_json(),
                "competitors": ", ".join(names) or "none listed",
            },
            adapters,
            config_path=config_path,
            max_tokens=2048,
        )
        raw = parse_json_response(run.text)
    except (LlmTaskError, json.JSONDecodeError, ValueError):
        return None
    content = guard_angle(raw, {name.casefold() for name in names})
    if not content.transformation and not content.advantages:
        return None
    return store.append_angle(
        engine,
        job_group_id=job.job_group_id,
        company_key=intel.company_key,
        intel_version=intel.version,
        content=content,
        model=run.model,
        prompt_version=run.prompt_version,
        usage=total_usage([run.record]),
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.integration.test_company_intel_angle -v` → `OK`.

- [ ] **Step 6: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/company_intel/angle.py tests/integration/test_company_intel_angle.py && cd ../..
git add packages/core/core/company_intel/angle.py packages/core/tests/integration/test_company_intel_angle.py prompts/competitive_angle
git commit -m "feat(job_search): per-job competitive angle (analysis, grounded in the stored intel)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The `research-company` CLI command

**Files:**
- Modify: `apps/pipeline/app/cli.py` (imports, `_cmd_research_company`, parser, dispatch)
- Modify: `packages/core/tests/test_pipeline_registry.py` (`_EXCLUDED_FROM_RUN_BUTTON` gains `"research-company"`)
- Test: `packages/core/tests/test_pipeline_cli_research.py`

**Interfaces:**
- Consumes: `get_or_refresh_intel`, `get_or_create_angle`, `Crawl4AIFetcher`, `CachedFetcher`, `load_config`, `load_job_context`, `load_job_source`.
- Produces: CLI `research-company --job-group-id ID [--force] [--end-client NAME] [--website URL] [--no-angle]`; exit code 0 for `reused`/`refreshed`/`no_content`, 1 for `failed`, `end_client_unknown`, `website_unverified` or an unknown job. Output line: `research-company <outcome>: <company> v<version> — <message>` plus, when an angle exists, `competitive angle: ready` (or `skipped`).

- [ ] **Step 1: Write the failing CLI tests**

```python
"""Unit tests for the `research-company` CLI subcommand (service patched out)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402
from tests.company_intel_fixtures import make_job, make_source  # noqa: E402

from core.company_intel.schema import (  # noqa: E402
    IntelContent,
    Resolution,
    StoredIntel,
)
from core.company_intel.service import IntelResult  # noqa: E402

_ARGS = ["research-company", "--job-group-id", "zzfixture-intel-job"]


def _intel() -> StoredIntel:
    """Build a stored intel.

    Returns:
        A version-3 intel.
    """
    return StoredIntel(
        company_key="k", version=3, display_name="Zz Co", status="ok",
        researched_at=datetime(2026, 10, 14, tzinfo=UTC), content=IntelContent(),
        pages=[], resolved_from_job="j", model="m", prompt_version="claude.v1", usage={},
    )


def _result(outcome: str, intel: StoredIntel | None = None) -> IntelResult:
    """Build a service result.

    Args:
        outcome: The outcome name.
        intel: The intel, if any.

    Returns:
        An `IntelResult`.
    """
    res = Resolution("zzfixture-intel-job", "Zz", "employer", "Zz Co", None, "employer")
    return IntelResult(outcome, res, intel, "a message")  # type: ignore[arg-type]


def _run(argv: list[str], result: IntelResult, job=make_job()):  # type: ignore[no-untyped-def]
    """Run the CLI with the database and service patched.

    Args:
        argv: CLI arguments.
        result: What the service returns.
        job: The job context returned by the loader (None = unknown job).

    Returns:
        `(exit code, stdout, service mock, angle mock)`.
    """
    out = io.StringIO()
    with (
        mock.patch("app.cli.build_engine"),
        mock.patch("app.cli.load_job_context", return_value=job),
        mock.patch("app.cli.load_job_source", return_value=make_source()),
        mock.patch("app.cli.get_or_refresh_intel", return_value=result) as service,
        mock.patch("app.cli.get_or_create_angle", return_value=object()) as angle,
        mock.patch("app.cli._build_llm_adapters", return_value={}),
        contextlib.redirect_stdout(out),
    ):
        code = main(argv)
    return code, out.getvalue(), service, angle


class TestResearchCompany(unittest.TestCase):
    """The research-company subcommand."""

    def test_a_fresh_research_prints_the_version_and_makes_the_angle(self) -> None:
        code, text, _service, angle = _run(_ARGS, _result("refreshed", _intel()))
        self.assertEqual(code, 0)
        self.assertIn("research-company refreshed", text)
        self.assertIn("v3", text)
        self.assertIn("competitive angle: ready", text)
        angle.assert_called_once()

    def test_reused_intel_exits_zero(self) -> None:
        code, text, _s, _a = _run(_ARGS, _result("reused", _intel()))
        self.assertEqual(code, 0)
        self.assertIn("reused", text)

    def test_no_angle_flag_skips_it(self) -> None:
        code, text, _s, angle = _run([*_ARGS, "--no-angle"], _result("refreshed", _intel()))
        self.assertEqual(code, 0)
        angle.assert_not_called()
        self.assertIn("competitive angle: skipped", text)

    def test_unknown_end_client_and_unverified_website_exit_one(self) -> None:
        for outcome in ("end_client_unknown", "website_unverified", "failed"):
            code, text, _s, angle = _run(_ARGS, _result(outcome))
            self.assertEqual(code, 1, outcome)
            self.assertIn(outcome, text)
            angle.assert_not_called()

    def test_flags_reach_the_service(self) -> None:
        _c, _t, service, _a = _run(
            [*_ARGS, "--force", "--end-client", "Real Bank", "--website", "https://bank.example"],
            _result("refreshed", _intel()),
        )
        kwargs = service.call_args.kwargs
        self.assertTrue(kwargs["force"])
        self.assertEqual(kwargs["override"].end_client, "Real Bank")
        self.assertEqual(kwargs["override"].website_url, "https://bank.example")

    def test_no_override_flags_means_no_override(self) -> None:
        _c, _t, service, _a = _run(_ARGS, _result("reused", _intel()))
        self.assertIsNone(service.call_args.kwargs["override"])

    def test_an_unknown_job_exits_one(self) -> None:
        code, text, service, _a = _run(_ARGS, _result("reused"), job=None)
        self.assertEqual(code, 1)
        self.assertIn("no such job", text)
        service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.test_pipeline_cli_research -v` → fails (`invalid choice: 'research-company'` / missing names).

- [ ] **Step 3: Implement in `apps/pipeline/app/cli.py`**

Add imports next to the existing `core.tailoring.context` import:
```python
from core.company_intel.angle import get_or_create_angle
from core.company_intel.config import load_config as load_company_intel_config
from core.company_intel.fetch import CachedFetcher, Crawl4AIFetcher
from core.company_intel.resolve import ManualOverride
from core.company_intel.service import get_or_refresh_intel
```
and extend the context import to `from core.tailoring.context import load_job_context, load_job_source`.

Add the command function after `_cmd_render_cv`:
```python
def _cmd_research_company(args: argparse.Namespace) -> int:
    """Run the `research-company` subcommand: research a job's company (Step 19a).

    Reuses stored intel when it is still fresh; otherwise crawls the
    company's site with Crawl4AI, extracts a cited profile with its top
    competitors and stores a new version. Then writes the job's competitive
    angle (unless `--no-angle`). On demand, not a batch stage, so it is
    deliberately absent from the pipeline dashboard (core.pipeline.registry).

    Args:
        args: Parsed CLI arguments — `job_group_id`, `force`, `end_client`,
            `website`, `no_angle`.

    Returns:
        0 when intel was reused, refreshed or stored as `no_content`; 1 when
        the job is unknown, the end client is unknown, the website cannot be
        verified, or the research failed.
    """
    settings = get_settings()
    engine = build_engine(settings.app_database_url)
    job = load_job_context(engine, args.job_group_id)
    if job is None:
        print(f"research-company: no such job {args.job_group_id}")
        return 1
    source = load_job_source(engine, args.job_group_id)
    override = None
    if args.end_client or args.website:
        override = ManualOverride(end_client=args.end_client, website_url=args.website)
    config = load_company_intel_config()
    http_client = httpx.Client(timeout=300.0)
    try:
        adapters = _build_llm_adapters(http_client)
        fetcher = CachedFetcher(
            Crawl4AIFetcher(settings.crawl4ai_url),
            engine,
            cache_days=config.crawl_cache_days,
        )
        result = get_or_refresh_intel(
            engine,
            job,
            source,
            adapters=adapters,
            fetcher=fetcher,
            config=config,
            force=args.force,
            override=override,
        )
        company = result.resolution.end_client or result.resolution.posted_company
        version = f" v{result.intel.version}" if result.intel else ""
        print(
            f"research-company {result.outcome}: {company}{version} — {result.message}"
        )
        if result.outcome not in ("reused", "refreshed", "no_content"):
            return 1
        if result.intel is not None:
            if args.no_angle:
                print("competitive angle: skipped")
            else:
                angle = get_or_create_angle(
                    engine, job, result.intel, adapters=adapters, force=args.force
                )
                print(f"competitive angle: {'ready' if angle else 'not available'}")
        return 0
    finally:
        http_client.close()
```
Add the parser before `args = parser.parse_args(argv)`:
```python
    research_parser = subparsers.add_parser(
        "research-company",
        help="Research a job's company (site crawl, competitors, competitive "
        "angle) and store it, or reuse fresh stored intel (PLAN.md Step 19a); "
        "on demand, not a pipeline stage",
    )
    research_parser.add_argument("--job-group-id", required=True)
    research_parser.add_argument(
        "--force",
        action="store_true",
        help="Research again even if the stored intel is still fresh",
    )
    research_parser.add_argument(
        "--end-client",
        help="The company to research when the posting is from an agency (stored "
        "as a manual override)",
    )
    research_parser.add_argument(
        "--website", help="The company's official website (stored as a manual override)"
    )
    research_parser.add_argument(
        "--no-angle",
        action="store_true",
        help="Do not write the job's competitive angle",
    )
```
and the dispatch line `if args.command == "research-company": return _cmd_research_company(args)` next to the other commands.

`tests/test_pipeline_registry.py`: change the set to `{"ingest", "run-evals", "tailor-cv", "render-cv", "research-company"}` and extend its comment: `research-company is on demand per job (Step 19a), driven from the Company Intel page / CLI.`

- [ ] **Step 4: Run the tests**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.test_pipeline_cli_research tests.test_pipeline_registry -v` → `OK`.

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh ../../apps/pipeline/app/cli.py tests/test_pipeline_cli_research.py tests/test_pipeline_registry.py && cd ../..
git add apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli_research.py packages/core/tests/test_pipeline_registry.py
git commit -m "feat(job_search): research-company CLI command

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: API endpoints

**Files:**
- Create: `apps/api/app/routers/intel.py`
- Modify: `apps/api/app/main.py` (import + `include_router`)
- Modify: `apps/api/app/dependencies.py` (add `get_fetcher`)
- Test: `packages/core/tests/test_intel_router.py`

**Interfaces:**
- Consumes: Task 8/9 services; `get_app_db_engine`, `get_llm_adapters` (existing).
- Produces (all JSON):
  - `GET /intel/jobs/{job_group_id}` → `IntelView {job_group_id, resolution, intel, versions, angle}`; 404 for an unknown job. `resolution` = `{poster_kind, posted_company, end_client, website_url, method} | null`; `intel` = latest `{version, status, researched_at, display_name, fresh, content, pages} | null` (the company is the resolution's `end_client`); `versions` = `[{version, researched_at, status}]`; `angle` = the job's latest angle `{intel_version, created_at, content} | null`.
  - `GET /intel/jobs/{job_group_id}?version=N` → same, but `intel` is that version.
  - `POST /intel/jobs/{job_group_id}/research` body `{force: bool = false}` → `ResearchView {outcome, message, view}`; runs synchronously.
  - `PUT /intel/jobs/{job_group_id}/resolution` body `{end_client?: str, website_url?: str}` (at least one) → `IntelView`; 422 if both empty.
  - `get_fetcher() -> Fetcher` in `dependencies.py` (a `CachedFetcher(Crawl4AIFetcher(settings.crawl4ai_url))` over the app engine).

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the intel API router (database and services patched)."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.dependencies import get_app_db_engine, get_fetcher, get_llm_adapters  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.company_intel_fixtures import make_job, make_source  # noqa: E402

from core.company_intel.schema import (  # noqa: E402
    AngleAdvantage,
    AngleContent,
    Competitor,
    IntelContent,
    Resolution,
    StoredAngle,
    StoredIntel,
)
from core.company_intel.service import IntelResult  # noqa: E402

_NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
_RES = Resolution("zzfixture-intel-job", "Zz", "employer", "Zz Co", "https://zz.example", "employer")


def _intel(version: int = 2) -> StoredIntel:
    """Build a stored intel with a competitor.

    Args:
        version: The version number.

    Returns:
        A `StoredIntel`.
    """
    return StoredIntel(
        company_key="k", version=version, display_name="Zz Co", status="ok",
        researched_at=_NOW, content=IntelContent(competitors=[Competitor(name="Rival", basis="model_knowledge")]),
        pages=[{"url": "https://zz.example", "status": "ok", "error": None}],
        resolved_from_job="zzfixture-intel-job", model="m", prompt_version="claude.v1", usage={},
    )


def _angle() -> StoredAngle:
    """Build a stored angle.

    Returns:
        A `StoredAngle`.
    """
    return StoredAngle(
        "zzfixture-intel-job", "k", 2,
        AngleContent(transformation="T", advantages=[AngleAdvantage(competitor="Rival", point="P")], caveats=["C"]),
        "m", "claude.v1", {}, _NOW,
    )


class TestIntelRouter(unittest.TestCase):
    """GET / POST / PUT under /intel."""

    def setUp(self) -> None:
        """Override the collaborators and patch the data access."""
        app.dependency_overrides[get_app_db_engine] = lambda: object()
        app.dependency_overrides[get_llm_adapters] = lambda: {}
        app.dependency_overrides[get_fetcher] = lambda: object()
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        patches = {
            "load_job_context": mock.patch("app.routers.intel.load_job_context", return_value=make_job()),
            "load_job_source": mock.patch("app.routers.intel.load_job_source", return_value=make_source()),
            "read_resolution": mock.patch("app.routers.intel.store.read_resolution", return_value=_RES),
            "read_latest_intel": mock.patch("app.routers.intel.store.read_latest_intel", return_value=_intel()),
            "read_intel_version": mock.patch("app.routers.intel.store.read_intel_version", return_value=_intel(1)),
            "list_intel_versions": mock.patch("app.routers.intel.store.list_intel_versions", return_value=[(2, _NOW, "ok"), (1, _NOW, "ok")]),
            "read_latest_angle": mock.patch("app.routers.intel.store.read_latest_angle", return_value=_angle()),
        }
        self.mocks = {}
        for name, patch in patches.items():
            self.mocks[name] = patch.start()
            self.addCleanup(patch.stop)

    def test_get_returns_resolution_intel_versions_and_angle(self) -> None:
        body = self.client.get("/intel/jobs/zzfixture-intel-job").json()
        self.assertEqual(body["resolution"]["end_client"], "Zz Co")
        self.assertEqual(body["intel"]["version"], 2)
        self.assertEqual(body["intel"]["content"]["competitors"][0]["name"], "Rival")
        self.assertEqual([v["version"] for v in body["versions"]], [2, 1])
        self.assertEqual(body["angle"]["content"]["advantages"][0]["competitor"], "Rival")

    def test_a_version_query_reads_that_version(self) -> None:
        body = self.client.get("/intel/jobs/zzfixture-intel-job?version=1").json()
        self.assertEqual(body["intel"]["version"], 1)

    def test_an_unknown_job_is_404(self) -> None:
        self.mocks["load_job_context"].return_value = None
        self.assertEqual(self.client.get("/intel/jobs/nope").status_code, 404)

    def test_a_job_with_no_resolution_has_null_parts(self) -> None:
        self.mocks["read_resolution"].return_value = None
        body = self.client.get("/intel/jobs/zzfixture-intel-job").json()
        self.assertIsNone(body["resolution"])
        self.assertIsNone(body["intel"])
        self.assertEqual(body["versions"], [])

    def test_post_research_runs_the_service_and_returns_the_view(self) -> None:
        result = IntelResult("refreshed", _RES, _intel(), "Researched.")
        with (
            mock.patch("app.routers.intel.get_or_refresh_intel", return_value=result) as service,
            mock.patch("app.routers.intel.get_or_create_angle", return_value=_angle()) as angle,
        ):
            response = self.client.post("/intel/jobs/zzfixture-intel-job/research", json={"force": True})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["outcome"], body["message"]), ("refreshed", "Researched."))
        self.assertTrue(service.call_args.kwargs["force"])
        angle.assert_called_once()

    def test_post_research_skips_the_angle_when_there_is_no_intel(self) -> None:
        result = IntelResult("end_client_unknown", _RES, None, "Set the end client.")
        with (
            mock.patch("app.routers.intel.get_or_refresh_intel", return_value=result),
            mock.patch("app.routers.intel.get_or_create_angle") as angle,
        ):
            body = self.client.post("/intel/jobs/zzfixture-intel-job/research", json={}).json()
        self.assertEqual(body["outcome"], "end_client_unknown")
        angle.assert_not_called()

    def test_put_resolution_stores_a_manual_override(self) -> None:
        with mock.patch("app.routers.intel.resolve_company") as resolve:
            response = self.client.put(
                "/intel/jobs/zzfixture-intel-job/resolution",
                json={"end_client": "Real Bank", "website_url": "https://bank.example"},
            )
        self.assertEqual(response.status_code, 200)
        override = resolve.call_args.kwargs["override"]
        self.assertEqual((override.end_client, override.website_url), ("Real Bank", "https://bank.example"))

    def test_put_resolution_with_nothing_is_422(self) -> None:
        response = self.client.put("/intel/jobs/zzfixture-intel-job/resolution", json={})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm --entrypoint python api -m unittest tests.test_intel_router -v` (service `api`; if the `api` container lacks `tests`, use `pipeline`)
Expected: `ImportError: cannot import name 'get_fetcher'`.

- [ ] **Step 3: Implement**

`dependencies.py` — add (imports: `from core.company_intel.config import load_config as load_company_intel_config`, `from core.company_intel.fetch import CachedFetcher, Crawl4AIFetcher, Fetcher`):
```python
@lru_cache
def get_fetcher() -> Fetcher:
    """Build the process-wide page fetcher: Crawl4AI behind the day cache.

    Returns:
        A `CachedFetcher` over `Crawl4AIFetcher(settings.crawl4ai_url)`.
    """
    settings = get_settings()
    return CachedFetcher(
        Crawl4AIFetcher(settings.crawl4ai_url, timeout=120.0),
        get_app_db_engine(),
        cache_days=load_company_intel_config().crawl_cache_days,
    )
```

`routers/intel.py`:
```python
"""Company-intel endpoints (PLAN.md Step 19a).

Intel is shared data, keyed on the job's resolved company, so these
endpoints take no user. Research runs synchronously (a crawl and two LLM
calls); the UI waits behind a spinner.
"""

from __future__ import annotations

from datetime import datetime

from app.dependencies import get_app_db_engine, get_fetcher, get_llm_adapters
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import Engine

from core.company_intel import store
from core.company_intel.angle import get_or_create_angle
from core.company_intel.config import load_config
from core.company_intel.fetch import Fetcher
from core.company_intel.keys import company_key
from core.company_intel.resolve import ManualOverride, resolve_company
from core.company_intel.service import get_or_refresh_intel, is_fresh
from core.llm.types import LLMAdapter
from core.tailoring.context import load_job_context, load_job_source

router = APIRouter()


class ResolutionModel(BaseModel):
    """Which company a job is researched under."""

    poster_kind: str
    posted_company: str | None
    end_client: str | None
    website_url: str | None
    method: str


class IntelModel(BaseModel):
    """One stored intel version."""

    version: int
    status: str
    researched_at: datetime
    display_name: str
    fresh: bool
    content: dict
    pages: list[dict]


class VersionModel(BaseModel):
    """One entry of a company's version history."""

    version: int
    researched_at: datetime
    status: str


class AngleModel(BaseModel):
    """A job's competitive angle (analysis, not fact)."""

    intel_version: int
    created_at: datetime
    content: dict


class IntelView(BaseModel):
    """Everything the Company Intel page shows for a job."""

    job_group_id: str
    resolution: ResolutionModel | None
    intel: IntelModel | None
    versions: list[VersionModel]
    angle: AngleModel | None


class ResearchRequest(BaseModel):
    """Request body for starting a research."""

    force: bool = False


class ResearchView(BaseModel):
    """The result of a research call."""

    outcome: str
    message: str
    view: IntelView


class ResolutionRequest(BaseModel):
    """Request body for a manual company override."""

    end_client: str | None = None
    website_url: str | None = None

    @model_validator(mode="after")
    def _needs_something(self) -> ResolutionRequest:
        """Require at least one non-blank field.

        Returns:
            The validated request.

        Raises:
            ValueError: If both fields are empty.
        """
        if not (self.end_client or "").strip() and not (self.website_url or "").strip():
            raise ValueError("give an end client, a website, or both")
        return self


def _build_view(
    engine: Engine, job_group_id: str, version: int | None = None
) -> IntelView:
    """Assemble the page view for a job.

    Args:
        engine: The app-role engine.
        job_group_id: The job.
        version: A specific intel version, or None for the latest.

    Returns:
        The resolution, the chosen intel version, the version list and the
        job's latest angle.
    """
    config = load_config()
    resolution = store.read_resolution(engine, job_group_id)
    key = company_key(resolution.end_client) if resolution else None
    stored = None
    versions: list[VersionModel] = []
    if key is not None:
        stored = (
            store.read_intel_version(engine, key, version)
            if version is not None
            else store.read_latest_intel(engine, key)
        )
        versions = [
            VersionModel(version=v, researched_at=at, status=status)
            for v, at, status in store.list_intel_versions(engine, key)
        ]
    angle = store.read_latest_angle(engine, job_group_id)
    now = datetime.now(stored.researched_at.tzinfo) if stored else None
    return IntelView(
        job_group_id=job_group_id,
        resolution=ResolutionModel(
            poster_kind=resolution.poster_kind,
            posted_company=resolution.posted_company,
            end_client=resolution.end_client,
            website_url=resolution.website_url,
            method=resolution.method,
        )
        if resolution
        else None,
        intel=IntelModel(
            version=stored.version,
            status=stored.status,
            researched_at=stored.researched_at,
            display_name=stored.display_name,
            fresh=is_fresh(stored.researched_at, now, config.refresh_after_days),  # type: ignore[arg-type]
            content=stored.content.model_dump(),
            pages=stored.pages,
        )
        if stored
        else None,
        versions=versions,
        angle=AngleModel(
            intel_version=angle.intel_version,
            created_at=angle.created_at,
            content=angle.content.model_dump(),
        )
        if angle
        else None,
    )


def _require_job(engine: Engine, job_group_id: str):  # type: ignore[no-untyped-def]
    """Load the job or answer 404.

    Args:
        engine: The app-role engine.
        job_group_id: The job.

    Returns:
        The `JobContext`.

    Raises:
        HTTPException: 404 when the job does not exist.
    """
    job = load_job_context(engine, job_group_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job


@router.get("/intel/jobs/{job_group_id}", response_model=IntelView)
def get_intel(
    job_group_id: str,
    version: int | None = Query(None, ge=1),
    engine: Engine = Depends(get_app_db_engine),
) -> IntelView:
    """Show a job's resolution, company intel and competitive angle.

    Args:
        job_group_id: The job.
        version: A specific intel version; the latest when omitted.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The page view.
    """
    _require_job(engine, job_group_id)
    return _build_view(engine, job_group_id, version)


@router.post("/intel/jobs/{job_group_id}/research", response_model=ResearchView)
def research(
    job_group_id: str,
    body: ResearchRequest,
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
    fetcher: Fetcher = Depends(get_fetcher),
) -> ResearchView:
    """Research a job's company, or reuse fresh stored intel.

    Args:
        job_group_id: The job.
        body: `force` researches again even if the intel is fresh.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.
        fetcher: Injected via `get_fetcher`.

    Returns:
        The outcome and the refreshed page view. The competitive angle is
        written whenever there is intel to reason from.
    """
    job = _require_job(engine, job_group_id)
    source = load_job_source(engine, job_group_id)
    result = get_or_refresh_intel(
        engine,
        job,
        source,
        adapters=adapters,
        fetcher=fetcher,
        config=load_config(),
        force=body.force,
    )
    if result.intel is not None:
        get_or_create_angle(engine, job, result.intel, adapters=adapters, force=body.force)
    return ResearchView(
        outcome=result.outcome,
        message=result.message,
        view=_build_view(engine, job_group_id),
    )


@router.put("/intel/jobs/{job_group_id}/resolution", response_model=IntelView)
def put_resolution(
    job_group_id: str,
    body: ResolutionRequest,
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> IntelView:
    """Set the end client and/or website for a job (a manual override).

    Args:
        job_group_id: The job.
        body: The end client and/or website to store.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The refreshed page view.
    """
    job = _require_job(engine, job_group_id)
    resolve_company(
        engine,
        job,
        load_job_source(engine, job_group_id),
        adapters=adapters,
        override=ManualOverride(
            end_client=(body.end_client or "").strip() or None,
            website_url=(body.website_url or "").strip() or None,
        ),
    )
    return _build_view(engine, job_group_id)
```
`main.py`: add `intel` to the `from app.routers import (...)` list and `app.include_router(intel.router)`.

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm --entrypoint python pipeline -m unittest tests.test_intel_router -v` → `OK`. (The `pipeline` image has FastAPI via `requirements.txt`; if `app.main` is not importable there, run the test in the `api` image instead: `docker compose run --rm --entrypoint python api -m unittest tests.test_intel_router -v`.)

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh ../../apps/api/app/routers/intel.py ../../apps/api/app/main.py ../../apps/api/app/dependencies.py tests/test_intel_router.py && cd ../..
git add apps/api packages/core/tests/test_intel_router.py
git commit -m "feat(job_search): company-intel API (view, research, manual resolution)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The Company Intel page

**Files:**
- Create: `apps/ui/app/pages/11_Company_Intel.py`
- Test: `packages/core/tests/test_ui_company_intel.py`

**Interfaces:**
- Consumes: `GET /tailoring/candidates` (existing, for the job picker), `GET /intel/jobs/{id}[?version=N]`, `POST /intel/jobs/{id}/research`, `PUT /intel/jobs/{id}/resolution` (Task 11).
- Produces: a Streamlit page; the picker honours `?job=<job_group_id>` like the Tailored CV Review page; keys: `intel-job` (job select), `intel-research` / `intel-refresh` (buttons), `intel-version` (version select), `intel-end-client`, `intel-website`, `intel-save-resolution`.

Page content (all API text rendered through the page's `_plain` escape, as in `10_Tailored_CV_Review.py`): resolution block (posted by / kind / end client / website / method, with the override form); intel block (status, researched date, `Fresh`/`Stale` badge, version picker, then Business model / Size signals / Tech stack / Recent announcements / Notes — each fact followed by its source URL as plain text); **Competitors** (name — why, with the badge `site` or `model knowledge — unverified`); **Competitive angle** under the caption `analysis — not verified` (transformation, advantages as "vs Rival: point", caveats); when no intel yet: info "No intel yet — click Research"; when the resolution has no end client: warning with the override form.

- [ ] **Step 1: Write the failing tests** (AppTest, modelled on `test_ui_tailored_cv_review.py`)

```python
"""Headless render tests for the Company Intel page (Streamlit AppTest)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import httpx
import streamlit as st
from streamlit.testing.v1 import AppTest

_PAGE = (
    Path(__file__).resolve().parents[3] / "apps" / "ui" / "app" / "pages" / "11_Company_Intel.py"
)
_HOSTILE = "[x](http://evil.example) **b**"
_CANDIDATE = {
    "job_group_id": "zzfixture-job", "title_for_display": "Data Engineer", "company": "Gamma",
    "final_score": 0.8, "latest_run_id": None, "latest_status": None,
}
_VIEW = {
    "job_group_id": "zzfixture-job",
    "resolution": {
        "poster_kind": "employer", "posted_company": "Gamma", "end_client": "Gamma",
        "website_url": "https://gamma.example", "method": "employer",
    },
    "intel": {
        "version": 2, "status": "ok", "researched_at": "2026-10-14T09:00:00+00:00",
        "display_name": "Gamma", "fresh": True,
        "content": {
            "business_model": [{"text": "Sells widgets.", "source_url": "https://gamma.example/about"}],
            "size_signals": [], "tech_stack": [{"text": _HOSTILE, "source_url": "https://gamma.example/jobs"}],
            "recent_announcements": [], "notes": [],
            "competitors": [
                {"name": "Rival", "why": "Same market", "basis": "model_knowledge", "source_url": None},
                {"name": "Sited", "why": "Named on site", "basis": "site", "source_url": "https://gamma.example/about"},
            ],
        },
        "pages": [{"url": "https://gamma.example", "status": "ok", "error": None}],
    },
    "versions": [
        {"version": 2, "researched_at": "2026-10-14T09:00:00+00:00", "status": "ok"},
        {"version": 1, "researched_at": "2026-07-01T09:00:00+00:00", "status": "ok"},
    ],
    "angle": {
        "intel_version": 2, "created_at": "2026-10-14T09:01:00+00:00",
        "content": {
            "transformation": "Moves to a modern data stack.",
            "advantages": [{"competitor": "Rival", "point": "Fresher data."}],
            "caveats": ["One job description only."],
        },
    },
}
_EMPTY = {"job_group_id": "zzfixture-job", "resolution": None, "intel": None, "versions": [], "angle": None}


def _shown(app: AppTest) -> str:
    """Collect everything the page shows as text, with markdown escapes removed.

    Args:
        app: A finished `AppTest`.

    Returns:
        The markdown, text, caption, info and warning values joined, minus the
        backslashes the page adds to keep API text literal.
    """
    parts = [
        element.value
        for group in (app.markdown, app.text, app.caption, app.info, app.warning)
        for element in group
    ]
    return " ".join(parts).replace("\\", "")


def _fake_get(candidates, view):
    """Build a fake `httpx.get` for the page.

    Args:
        candidates: The `/tailoring/candidates` answer.
        view: The `/intel/jobs/...` answer, or None for a 404.

    Returns:
        A callable for `mock.patch("httpx.get", ...)`.
    """
    def _fake(url: str, **_kwargs) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "/tailoring/candidates" in url:
            return httpx.Response(200, json=candidates, request=request)
        if "/intel/jobs/" in url:
            if view is None:
                return httpx.Response(404, json={"detail": "unknown job"}, request=request)
            return httpx.Response(200, json=view, request=request)
        return httpx.Response(200, json=[], request=request)

    return _fake


class TestCompanyIntelPage(unittest.TestCase):
    """The page renders intel, competitors and the labelled angle."""

    def setUp(self) -> None:
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)

    def _run(self, candidates, view) -> AppTest:  # type: ignore[no-untyped-def]
        """Render the page against a fake API.

        Args:
            candidates: The candidates answer.
            view: The intel view answer.

        Returns:
            The finished `AppTest`.
        """
        with mock.patch("httpx.get", side_effect=_fake_get(candidates, view)):
            return AppTest.from_file(str(_PAGE), default_timeout=10).run()

    def test_it_renders_intel_with_sources_and_a_fresh_badge(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        self.assertEqual(len(app.exception), 0)
        shown = _shown(app)
        self.assertIn("Sells widgets.", shown)
        self.assertIn("https://gamma.example/about", shown)
        self.assertIn("Fresh", shown)

    def test_competitors_show_their_basis_badge(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        shown = _shown(app)
        self.assertIn("model knowledge — unverified", shown)
        self.assertIn("Rival", shown)
        self.assertIn("Sited", shown)

    def test_the_angle_is_labelled_as_analysis(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        shown = _shown(app)
        self.assertIn("analysis — not verified", shown)
        self.assertIn("Moves to a modern data stack.", shown)
        self.assertIn("Fresher data.", shown)

    def test_hostile_text_is_not_rendered_as_markdown(self) -> None:
        app = self._run([_CANDIDATE], _VIEW)
        markdown = " ".join(m.value for m in app.markdown)
        self.assertNotIn("[x](http://evil.example)", markdown)

    def test_with_no_intel_it_offers_to_research(self) -> None:
        app = self._run([_CANDIDATE], _EMPTY)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("intel-research", [b.key for b in app.button])
        self.assertIn("No intel yet", " ".join(i.value for i in app.info))

    def test_an_unknown_end_client_asks_for_one(self) -> None:
        view = {**_EMPTY, "resolution": {
            "poster_kind": "agency", "posted_company": "Hays", "end_client": None,
            "website_url": None, "method": "llm",
        }}
        app = self._run([_CANDIDATE], view)
        self.assertIn("end client", _shown(app).lower())
        self.assertIn("intel-save-resolution", [b.key for b in app.button])

    def test_a_stale_version_says_so(self) -> None:
        view = {**_VIEW, "intel": {**_VIEW["intel"], "fresh": False}}
        self.assertIn("Stale", _shown(self._run([_CANDIDATE], view)))

    def test_a_job_link_opens_the_page_on_that_job(self) -> None:
        other = {**_CANDIDATE, "job_group_id": "zzfixture-other", "company": "Delta"}
        with mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE, other], _EMPTY)):
            app = AppTest.from_file(str(_PAGE), default_timeout=10)
            app.query_params["job"] = "zzfixture-other"
            app.run()
        self.assertEqual(app.selectbox[0].value, "zzfixture-other")

    def test_no_candidates_and_an_api_error_do_not_crash(self) -> None:
        self.assertEqual(len(self._run([], None).exception), 0)
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("down")):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)

    def test_clicking_research_posts_to_the_research_endpoint(self) -> None:
        posted = {}

        def fake_post(url: str, **kwargs) -> httpx.Response:
            posted["url"], posted["json"] = url, kwargs.get("json")
            return httpx.Response(
                200,
                json={"outcome": "refreshed", "message": "Researched.", "view": _VIEW},
                request=httpx.Request("POST", url),
            )

        with (
            mock.patch("httpx.get", side_effect=_fake_get([_CANDIDATE], _EMPTY)),
            mock.patch("httpx.post", side_effect=fake_post),
        ):
            app = AppTest.from_file(str(_PAGE), default_timeout=10).run()
            next(b for b in app.button if b.key == "intel-research").click().run()
        self.assertTrue(posted["url"].endswith("/intel/jobs/zzfixture-job/research"))
        self.assertEqual(posted["json"], {"force": False})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm --entrypoint python ui -m unittest tests.test_ui_company_intel -v` → fails (page file missing).

- [ ] **Step 3: Implement the page**

`apps/ui/app/pages/11_Company_Intel.py`:

```python
"""Company Intel - what the system knows about the company behind a job
(PLAN.md Step 19a): the resolved company, its researched profile, its top
competitors and a per-job competitive angle. Intel is shared and versioned;
the angle is analysis, not fact."""

from __future__ import annotations

from datetime import datetime

import httpx
import streamlit as st

from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Company Intel", layout="wide")
apply_theme()
st.title("Company Intel")

with st.expander("User manual"):
    st.markdown(
        """
Pick a job. **Research** looks up the company behind it - its own website, a
profile with sources, its top competitors - and stores it; looking at the same
company again reuses what is stored until it is 90 days old. When a job is
posted by an agency, the page needs the **end client**: open *Correct the
company*, type it (and its website) and save.

Facts show the page they came from. **Competitors** marked *model knowledge -
unverified* come from the model's general knowledge. The **competitive angle**
is analysis written for this job - not verified fact.
"""
    )

_base = get_settings().api_base_url
_MD_META = set("\\`*_{}[]()#+-.!|<>~$:&")
_FACT_FIELDS = (
    ("business_model", "Business model"),
    ("size_signals", "Size signals"),
    ("tech_stack", "Tech stack"),
    ("recent_announcements", "Recent announcements"),
    ("notes", "Notes"),
)


def _plain(text: object) -> str:
    """Escape text so Streamlit renders it literally, never as markdown.

    Args:
        text: Untrusted text (anything `str()`-able).

    Returns:
        The text with every markdown metacharacter backslash-escaped.
    """
    return "".join(f"\\{ch}" if ch in _MD_META else ch for ch in str(text))


def _date(value: object) -> str:
    """Format an API timestamp as a short date.

    Args:
        value: An ISO-8601 string.

    Returns:
        e.g. `14 Oct 2026`; the input as text when it cannot be parsed.
    """
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y")
    except ValueError:
        return str(value)


def _get(path: str) -> httpx.Response:
    """GET from the API.

    Args:
        path: The path, starting with `/`.

    Returns:
        The response.
    """
    return httpx.get(f"{_base}{path}", timeout=15.0)


def _post(path: str, json: dict) -> httpx.Response:
    """POST to the API (research can take minutes).

    Args:
        path: The path, starting with `/`.
        json: The JSON body.

    Returns:
        The response.
    """
    return httpx.post(f"{_base}{path}", json=json, timeout=600.0)


def _put(path: str, json: dict) -> httpx.Response:
    """PUT to the API.

    Args:
        path: The path, starting with `/`.
        json: The JSON body.

    Returns:
        The response.
    """
    return httpx.put(f"{_base}{path}", json=json, timeout=30.0)


def _load_view(job_id: str, version: int | None = None) -> dict:
    """Load a job's intel view, stopping the page on an API error.

    Args:
        job_id: The job.
        version: A specific intel version, or None for the latest.

    Returns:
        The `GET /intel/jobs/{id}` answer.
    """
    path = f"/intel/jobs/{job_id}" + (f"?version={version}" if version else "")
    try:
        response = _get(path)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        st.error(_plain(f"Failed to load the intel: {exc}"))
        st.stop()


try:
    response = _get("/tailoring/candidates")
    response.raise_for_status()
    candidates = response.json()
except (httpx.HTTPError, ValueError) as exc:
    st.error(_plain(f"Failed to load your jobs: {exc}"))
    st.stop()

if not candidates:
    st.info("No scored jobs yet - run the scoring stages from the Pipeline Dashboard first.")
    st.stop()

labels = {
    c["job_group_id"]: f"{c['title_for_display'] or '(no title)'} - "
    f"{c['company'] or 'unknown company'}"
    for c in candidates
}
# A link from a saved job spec (`?job=<job_group_id>`) opens on that job.
_linked_job = st.query_params.get("job")
if _linked_job in labels and "intel_linked_job" not in st.session_state:
    st.session_state["intel_linked_job"] = _linked_job
    st.session_state["intel-job"] = _linked_job
selected = st.selectbox(
    "Job", options=list(labels), format_func=lambda job_id: labels[job_id], key="intel-job"
)

_last = st.session_state.pop("intel-last-message", None)
if _last:
    (st.success if _last[0] in ("refreshed", "reused") else st.warning)(_plain(_last[1]))

view = _load_view(selected)
versions = view["versions"]
if len(versions) > 1:
    dates = {v["version"]: _date(v["researched_at"]) for v in versions}
    chosen = st.selectbox(
        "Version",
        options=list(dates),
        format_func=lambda n: f"v{n} - {dates[n]}",
        key=f"intel-version-{selected}",
    )
    if view["intel"] is None or chosen != view["intel"]["version"]:
        view = _load_view(selected, chosen)
resolution, intel, angle = view["resolution"], view["intel"], view["angle"]

st.subheader("Company")
if resolution is not None:
    st.markdown(
        "\n".join(
            [
                f"- **Posted by:** {_plain(resolution['posted_company'] or 'unknown')}",
                f"- **Poster is:** {_plain(resolution['poster_kind'])}",
                f"- **Researching:** {_plain(resolution['end_client'] or 'not known yet')}",
                f"- **Website:** {_plain(resolution['website_url'] or 'not known yet')}",
                f"- **Set by:** {_plain(resolution['method'])}",
            ]
        )
    )
    if resolution["end_client"] is None:
        st.warning("This posting does not name the end client - enter it below to research it.")
if intel is None:
    st.info("No intel yet - click Research.")

with st.expander("Correct the company"):
    end_client = st.text_input("End client", key="intel-end-client")
    website = st.text_input("Website", key="intel-website")
    if st.button("Save", key="intel-save-resolution"):
        body = {"end_client": end_client or None, "website_url": website or None}
        try:
            saved = _put(f"/intel/jobs/{selected}/resolution", body)
            saved.raise_for_status()
        except httpx.HTTPError as exc:
            st.error(_plain(f"Could not save: {exc}"))
        else:
            st.rerun()

label, button_key = ("Refresh", "intel-refresh") if intel else ("Research", "intel-research")
if st.button(label, key=button_key):
    with st.spinner("Researching - this can take a few minutes"):
        try:
            answer = _post(f"/intel/jobs/{selected}/research", {"force": bool(intel)})
            answer.raise_for_status()
            outcome = answer.json()
        except (httpx.HTTPError, ValueError) as exc:
            st.error(_plain(f"Research failed: {exc}"))
            st.stop()
    st.session_state["intel-last-message"] = (outcome["outcome"], outcome["message"])
    st.rerun()

if intel is not None:
    badge = "Fresh" if intel["fresh"] else "Stale"
    st.markdown(
        f"**{badge}** - v{intel['version']}, researched {_date(intel['researched_at'])}, "
        f"status {_plain(intel['status'])}"
    )
    if intel["status"] == "no_content":
        st.info("The company's pages gave no usable facts.")
    for field, title in _FACT_FIELDS:
        facts = intel["content"].get(field) or []
        if not facts:
            continue
        st.markdown(f"**{title}**")
        for fact in facts:
            st.markdown(f"- {_plain(fact['text'])}")
            st.caption(_plain(fact["source_url"]))
    competitors = intel["content"].get("competitors") or []
    if competitors:
        st.subheader("Competitors")
        for competitor in competitors:
            why = f" - {_plain(competitor['why'])}" if competitor.get("why") else ""
            st.markdown(f"**{_plain(competitor['name'])}**{why}")
            if competitor["basis"] == "site":
                st.caption(f"site: {_plain(competitor['source_url'])}")
            else:
                st.caption("model knowledge — unverified")

if angle is not None:
    st.subheader("Competitive angle")
    st.caption("analysis — not verified")
    content = angle["content"]
    if content.get("transformation"):
        st.markdown(_plain(content["transformation"]))
    for item in content.get("advantages") or []:
        who = _plain(item["competitor"]) if item.get("competitor") else "the field"
        st.markdown(f"- vs {who}: {_plain(item['point'])}")
    for caveat in content.get("caveats") or []:
        st.caption(_plain(caveat))
    if intel is not None and angle["intel_version"] != intel["version"]:
        st.caption(f"Written from intel v{angle['intel_version']}.")
```

(The page uses plain hyphens in labels it builds; the two badge captions keep the em dash the tests and the spec use.)

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm --entrypoint python ui -m unittest tests.test_ui_company_intel -v` → `OK`.

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh ../../apps/ui/app/pages/11_Company_Intel.py tests/test_ui_company_intel.py && cd ../..
git add apps/ui/app/pages/11_Company_Intel.py packages/core/tests/test_ui_company_intel.py
git commit -m "feat(job_search): Company Intel page

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Infrastructure, the job-spec link, docs and the plan

**Files:**
- Modify: `docker-compose.yml` (add `crawl4ai`; pass `CRAWL4AI_URL`)
- Modify: `packages/core/core/render/layout.py` (add `intel_ui_link`) and `render_job_spec` (one more line)
- Modify: `apps/pipeline/app/cli.py` (`_cmd_render_cv` passes the intel link)
- Modify: `packages/core/tests/test_render_layout.py`, `packages/core/tests/test_pipeline_cli_render.py`
- Modify: `README.md`, `PLAN.md`

**Interfaces:**
- Produces: `layout.intel_ui_link(base_url: str, job_group_id: str) -> str` returning `<base>/Company_Intel?job=<id>`; `render_job_spec(job, source, ui_link, intel_link: str | None = None)` adding `- **Company intel:** <link>` after the `In the UI` line when `intel_link` is given.

- [ ] **Step 1: Write the failing layout and CLI tests**

In `test_render_layout.py` add:
```python
    def test_the_company_intel_link_is_listed_when_given(self) -> None:
        link = intel_ui_link("http://localhost:8501/", "abc123")
        self.assertEqual(link, "http://localhost:8501/Company_Intel?job=abc123")
        text = render_job_spec(_job(), JobSource(), "http://u", link)
        self.assertIn(f"**Company intel:** {link}", text)

    def test_without_an_intel_link_the_line_is_absent(self) -> None:
        self.assertNotIn("Company intel", render_job_spec(_job(), JobSource(), "http://u"))
```
(import `intel_ui_link` from `core.render.layout`). In `test_pipeline_cli_render.py`, extend `test_the_job_spec_is_written_beside_the_cv_with_its_links` with `self.assertIn("http://ui.test:9/Company_Intel?job=j1", spec)`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.test_render_layout -v` → `ImportError: cannot import name 'intel_ui_link'`.

- [ ] **Step 3: Implement**

`layout.py`:
```python
def intel_ui_link(base_url: str, job_group_id: str) -> str:
    """Build the link to a job in the Company Intel page of the UI.

    Args:
        base_url: Where the UI runs, for example `http://localhost:8501`.
        job_group_id: The job's id.

    Returns:
        A URL that opens the page with the job pre-selected.
    """
    return f"{base_url.rstrip('/')}/Company_Intel?job={job_group_id}"
```
and change `render_job_spec(job, source, ui_link)` to `render_job_spec(job, source, ui_link, intel_link=None)`, appending after the `In the UI` line: `if intel_link: lines.append(f"- **Company intel:** {intel_link}")`; update its docstring. In `cli.py` `_cmd_render_cv`, import `intel_ui_link` and pass `intel_ui_link(ui_base, args.job_group_id)` as the fourth argument.

`docker-compose.yml` — add the service after `ollama`:
```yaml
  # Step 19a company research: a headless-browser crawler with a REST API.
  # Not started by default (about 1.7 GB image, needs Docker memory):
  #   docker compose --profile research up -d crawl4ai
  crawl4ai:
    image: unclecode/crawl4ai:0.9.4
    profiles: [research]
    shm_size: 1gb
    ports:
      - "11235:11235"
```
The `api` and `pipeline` services read `CRAWL4AI_URL` from `.env` (`env_file: .env` is already set), so no per-service change is needed. Add `CRAWL4AI_URL=http://crawl4ai:11235` to `.env` as well if the line is missing.

`README.md`: add a "Company intel (Step 19a)" section after the rendered-CV section: what `research-company` does, `docker compose --profile research up -d crawl4ai`, the command with its flags, where the page is, the 90-day freshness rule, the `analysis — not verified` label, and the follow-ups (Companies House, GitHub, news).

`PLAN.md`: (a) in the Phase 5 numbering line change `→ 19 → 19A →` to `→ 19A → 19 →`; (b) under Step 19 add a short paragraph: "Built after 19a: the Researcher does not fetch - it reads the stored `intel.company_intel` and the job's competitive angle via `get_or_refresh_intel` / `get_or_create_angle`."; (c) under Step 19a add "Built in `core.company_intel` (spec: docs/superpowers/specs/2026-10-14-step19a-company-intel-design.md): table `intel.company_intel` stands for `dim_company_intel`; competitors and a per-job competitive angle added; Companies House, GitHub signals, news and sentiment remain follow-ups; the Company Intel page stands in for the interview-prep view until Step 20a."

- [ ] **Step 4: Run the tests**

Run: `cd packages/core && ../../venv/bin/python -m unittest tests.test_render_layout -v` → `OK`; then `cd ../.. && docker compose run --rm --entrypoint python pipeline -m unittest tests.test_pipeline_cli_render -v` → `OK`.
Also validate the compose file: `docker compose --profile research config --quiet && echo compose ok`.

- [ ] **Step 5: Format and commit**

```bash
cd packages/core && ../../../.superpowers/fmt.sh core/render/layout.py ../../apps/pipeline/app/cli.py tests/test_render_layout.py tests/test_pipeline_cli_render.py && cd ../..
git add docker-compose.yml README.md PLAN.md packages/core apps/pipeline
git commit -m "feat(job_search): Crawl4AI service, job-spec link to Company Intel, docs and plan order

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Live check (manual, no code)

**Files:** none (the only changes are database rows and cache entries).

- [ ] **Step 1: Start the services**

```bash
docker compose up -d postgres api ui
docker compose --profile research up -d crawl4ai
curl -s http://localhost:11235/health
```
Expected: the health call answers (JSON with a status). If the container is still starting, wait 30 s and retry.

- [ ] **Step 2: Check the real Crawl4AI answer shape**

```bash
curl -s -X POST http://localhost:11235/crawl -H 'Content-Type: application/json' -d '{"urls":["https://stripe.com/about"],"browser_config":{"type":"BrowserConfig","params":{"headless":true}},"crawler_config":{"type":"CrawlerRunConfig","params":{"cache_mode":"bypass","check_robots_txt":true}}}' | python3 -c "import json,sys; d=json.load(sys.stdin); r=d['results'][0]; print(list(r)[:12]); print(type(r['markdown']).__name__); print(list(r['links'])[:3])"
```
Expected: the result keys include `markdown` and `links`. If `markdown` or `links` has a different shape from what `parse_crawl_payload` handles, add a test case with the real shape to `tests/test_company_intel_fetch.py`, fix the parser, and commit before continuing.

- [ ] **Step 3: Research a real employer, then again**

Pick an employer job that exists in the database (for example a Stripe posting): `docker compose exec -T postgres psql -U job_search_owner -d job_search -At -c "select job_group_id from gold.dim_job where company = 'Stripe' limit 1"`. Then:
```bash
docker compose run --rm pipeline research-company --job-group-id <id>
docker compose run --rm pipeline research-company --job-group-id <id>
```
Expected: the first prints `research-company refreshed: Stripe v1 …` and `competitive angle: ready`; the second prints `research-company reused: Stripe v1 …` (no crawl, no extraction call).

- [ ] **Step 4: Check an agency job**

Run `research-company` for the Sanderson job (`5425399c96984a668244cae23369f54f`). Expected: `end_client_unknown` (exit 1) unless the posting names a client; then run it again with `--end-client "<a company>" --website https://…` and confirm it researches that company.

- [ ] **Step 5: Look at the page**

Open `http://localhost:8501/Company_Intel?job=<id>`: the job is preselected; facts show their source URLs; competitors carry the right badge; the angle carries `analysis — not verified`; the version list and the Fresh badge show.

- [ ] **Step 6: Stop what you started and record the result**

```bash
docker compose --profile research stop crawl4ai
docker compose stop api ui postgres
```
Report the two commands' output, the page check and any parser fix in the PR description. Remove no rows: the stored intel for the real employer is real data.

---

## Self-Review

**Spec coverage**
- Data tables, key, config, staleness, versioning → Tasks 1–3, 8.
- Resolution (employer/agency/LLM/manual, website verification) → Task 6, service branch in Task 8.
- Crawl4AI as a separate service, cache, same-domain/robots/max pages → Tasks 4, 5, 13.
- Extraction with cited-URL guard, 5 competitors, `site`/`model_knowledge` rules → Task 7.
- Competitive angle (per job, analysis label, regenerate on new intel version) → Task 9; label rendered in Task 12.
- CLI (`--force/--end-client/--website/--no-angle`) → Task 10; API (GET/POST/PUT) → Task 11; Company Intel page → Task 12.
- Failure rules (all pages fail → nothing written; `no_content` stored) → Task 8 tests (`website_unverified` covers "homepage unreachable"; extraction errors → `failed`).
- LLM tasks/prompts/config → Tasks 2, 6, 7, 9. Plan changes + README + job-spec link → Task 13. "Done when" (second run reuses; page shows it) → Task 8 test + Task 14.
- Spec deviations recorded: `crawl_cache` also stores `links` and `error`; the CLI has no `--user-id` (Task 1 Step 5 edits the spec).

**Placeholder scan:** no "TBD"/"implement later"; every code step carries its code, including the full Company Intel page.

**Type consistency:** `Page`/`Fetcher` (Task 4) are used by Tasks 5–8 and the fixtures; `Resolution`, `StoredIntel`, `StoredAngle`, `IntelContent` fields match between `schema.py`, `store.py`, the router views and the tests; `run_task(...)` returns `TaskRun` with `.record` used by `resolve`, `extract`, `angle`; `get_or_refresh_intel(...)` and `get_or_create_angle(...)` signatures match the CLI and router calls; `ManualOverride` is imported from `resolve` everywhere.
