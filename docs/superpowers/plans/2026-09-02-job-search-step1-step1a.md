# Job Search — Step 1 (Repo/Container Scaffold) + Step 1a (Tenancy/RLS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the job_search monorepo's containerised skeleton (FastAPI, Streamlit, pipeline images, Postgres/pgvector, the LLM gateway) and the multi-user tenancy convention (row-level security, DB roles, per-user quota tables) before any application table exists, so tenancy is never retrofitted.

**Architecture:** Three Docker images (`apps/api`, `apps/ui`, `apps/pipeline`) share one plain-Python package (`packages/core`) added to `PYTHONPATH`, not pip-installed — it holds settings, the LLM gateway, and the DB session/RLS layer so all three images import identical logic. Postgres runs two roles: a migration/owner role (bypasses RLS, used by Alembic) and `job_search_app` (subject to RLS, used by the API and by the RLS proof test). Row-level security policies key off a Postgres session GUC (`app.current_user_id`) set via `SET LOCAL` inside each transaction — never trusted from a client-supplied field.

**Tech Stack:** Python 3.11, FastAPI + uvicorn, Streamlit, pydantic-settings, SQLAlchemy 2.0 + Alembic, psycopg (binary), httpx, anthropic SDK, PyYAML, unittest + coverage, Docker Compose, Postgres 16 (`pgvector/pgvector:pg16`).

**Spec:** `job_search/PLAN.md` (Step 1, lines ~97–177; Step 1a, lines ~179–236), `job_search/plan/backlog.yml` (`STEP-01` / `JOB-16`, `STEP-01A` / `JOB-33`), `job_search/DECISIONS.md` §1 (dual-track LLM providers) and §7 (multi-user tenancy).

## Global Constraints

- Python 3.11 only (repo-wide convention; `dlt`/tooling elsewhere in this workspace breaks on 3.13).
- Formatting/linting: `black` (line length 88), `isort` (profile `black`), `ruff` for linting, `mypy` for type checking — all four wired into `.pre-commit-config.yaml` (job_search's own Step 1 subtask calls for ruff/ruff-format/mypy; the repo's binding `.claude/rules/python-style.md` mandates black+isort+ruff, so black+isort supersede ruff-format here — no functional conflict, both produce PEP8-compatible output).
- Type hints required on all public function signatures; use `from __future__ import annotations`; use `list[T]`/`dict[K, V]` not `List`/`Dict`.
- Google-style docstrings on every function/class, public and private, with `Args`/`Returns`/`Raises`.
- Tests use `unittest` + `coverage`, never `pytest`; run from the sub-package root (`cd packages/core && coverage run -m unittest discover && coverage report -m`).
- No mocking the database or the Postgres connection in integration tests — real connections only. LLM provider adapters use dependency-injected fakes/protocols in unit tests (not "the database"), so this rule doesn't block them; live-provider calls are out of scope for Step 1/1a (that's Step 12a's eval harness).
- No bare `except:`; prefer f-strings; max line length 88.
- SQL in migrations: keywords UPPERCASE, 4-space indent, snake_case identifiers, `snake_case` table/column names, `<entity>_id` for FKs, `is_`/`has_` for booleans, `_at` for timestamps — per `.claude/rules/sql-style.md`.
- RLS session variable name is fixed for the whole project: `app.current_user_id` — every future per-user table's policy must reference exactly this GUC, so a table added in a later phase is a two-line migration (`ENABLE ROW LEVEL SECURITY` + `CREATE POLICY ... USING (user_id = current_setting('app.current_user_id', true)::uuid)`), never a new mechanism.
- `current_setting(..., true)` (the `missing_ok` form) is mandatory in every policy — it returns `NULL` instead of erroring when the GUC is unset, and `user_id = NULL` is never true, so isolation fails **closed** (zero rows) rather than open.

---

## File Structure

```
job_search/
  apps/
    api/
      Dockerfile
      app/__init__.py
      app/main.py                  # FastAPI app: GET /health, GET /whoami
    ui/
      Dockerfile
      app/Home.py                  # Streamlit hello-world page
    pipeline/
      Dockerfile
      app/__init__.py
      app/cli.py                   # placeholder batch entrypoint
  packages/
    core/
      core/__init__.py
      core/settings.py             # pydantic-settings Settings
      core/llm/__init__.py
      core/llm/types.py            # LLMResponse, LLMAdapter protocol
      core/llm/task_config.py      # config/llm_tasks.yml -> TaskConfig
      core/llm/call_log.py         # structured call logging
      core/llm/adapters/__init__.py
      core/llm/adapters/ollama.py
      core/llm/adapters/anthropic.py
      core/llm/gateway.py          # complete(task=..., ...)
      core/db/__init__.py
      core/db/models.py            # AppUser, UserQuota, SharedApiQuota
      core/db/session.py           # engines, session_scope(), get_current_user_id
      core/db/quota.py             # check_and_increment_shared_quota()
      tests/__init__.py
      tests/test_settings.py
      tests/test_task_config.py
      tests/test_llm_gateway.py
      tests/test_api_whoami.py
      tests/integration/__init__.py
      tests/integration/test_db_session.py
      tests/integration/test_rls_isolation.py
      tests/integration/test_quota.py
  db/
    alembic.ini
    migrations/env.py
    migrations/script.py.mako
    migrations/versions/0001_create_app_user.py
    migrations/versions/0002_create_user_quota_and_rls.py
    init/01_create_roles.sh
  config/
    llm_tasks.yml
  docs/
    tenancy.md                     # the two-zone rule, documented
  dbt/
    README.md                      # per-user dbt grain convention, for Step 5
  data/landing/.gitkeep
  infra/.gitkeep
  docker-compose.yml
  .pre-commit-config.yaml
  pyproject.toml                   # tool config: black, isort, ruff, mypy
  requirements.txt                 # extended
  .env.example                     # extended
```

---

## Task 1: Monorepo layout, tool config, and dependency baseline

**Files:**
- Create: `job_search/data/landing/.gitkeep`, `job_search/infra/.gitkeep`, `job_search/dbt/README.md`
- Create: `job_search/pyproject.toml`
- Create: `job_search/.pre-commit-config.yaml`
- Modify: `job_search/requirements.txt`
- Modify: `job_search/.env.example`

**Interfaces:**
- Produces: the dependency set and tool config every later task assumes is already installed/configured. No runtime code.

- [ ] **Step 1: Create the placeholder directories**

```bash
mkdir -p job_search/data/landing job_search/infra
touch job_search/data/landing/.gitkeep job_search/infra/.gitkeep
```

- [ ] **Step 2: Write `job_search/dbt/README.md`**

```markdown
# dbt project — placeholder

The real dbt project lands in Step 5 (`dbt project and staging models`).

Convention fixed now, in Step 1a, so Step 5 doesn't have to relitigate it:

- **Shared marts** (job postings, `dim_job`, `dim_company`, market marts,
  taxonomy) build once, with no per-user grain at all.
- **Per-user models** take `user_id` as part of the model's **grain** — a
  column selected and grouped on — never as a dbt `var`. A `var` is a
  build-time constant; a per-user model must return every user's rows in one
  build (Postgres RLS then scopes what each user's session can see), not be
  rebuilt once per user.
```

- [ ] **Step 3: Write `job_search/pyproject.toml`**

```toml
[tool.black]
line-length = 88
target-version = ["py311"]

[tool.isort]
profile = "black"
line_length = 88

[tool.ruff]
line-length = 88
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
disallow_untyped_defs = true
exclude = ["tests/"]
```

- [ ] **Step 4: Write `job_search/.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 24.10.0
    hooks:
      - id: black
  - repo: https://github.com/pycqa/isort
    rev: 5.13.2
    hooks:
      - id: isort
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0
    hooks:
      - id: mypy
        additional_dependencies: [pydantic, pydantic-settings, sqlalchemy]
```

- [ ] **Step 5: Extend `job_search/requirements.txt`**

```
git+https://github.com/FredGH/jira_sync_kit.git@0.2.0

fastapi==0.115.0
uvicorn[standard]==0.32.0
streamlit==1.39.0
pydantic-settings==2.6.0
sqlalchemy==2.0.36
alembic==1.13.3
psycopg[binary]==3.2.3
httpx==0.27.2
anthropic==0.39.0
pyyaml==6.0.2
coverage==7.6.4
pre-commit==4.0.1
ruff==0.8.0
black==24.10.0
isort==5.13.2
mypy==1.13.0
```

- [ ] **Step 6: Extend `job_search/.env.example`**

```
# .env.example
JIRA_SITE_URL=https://yoursite.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=

# --- Environment ---
ENV=local

# --- Database (migration/owner role — bypasses RLS, used by Alembic) ---
POSTGRES_USER=job_search_owner
POSTGRES_PASSWORD=change-me
POSTGRES_DB=job_search
DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@postgres:5432/job_search

# --- Database (app role — RLS-enforced, used by the API at runtime) ---
APP_DB_PASSWORD=change-me-too
APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@postgres:5432/job_search

DB_CONNECTION_MODE=dsn

# --- Landing zone ---
LANDING_URI=file:///data/landing

# --- Embeddings ---
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
OLLAMA_BASE_URL=http://ollama:11434

# --- LLM (informational default only — see config/llm_tasks.yml for the
# per-task provider routing that actually decides which model runs) ---
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=

# --- Source API keys ---
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
REED_API_KEY=
JOOBLE_KEY=

API_BASE_URL=http://localhost:8000
```

- [ ] **Step 7: Verify tool config parses**

Run: `cd job_search && python3.11 -m pip install -r requirements.txt`
Expected: installs cleanly (no resolver conflicts).

Run: `cd job_search && python3.11 -m black --check pyproject.toml 2>&1 | head -1; python3.11 -m ruff check --config pyproject.toml . 2>&1 | tail -3`
Expected: commands run without config errors (there's no Python code yet, so ruff reports nothing to check or zero findings).

- [ ] **Step 8: Commit**

```bash
cd job_search
git add pyproject.toml .pre-commit-config.yaml requirements.txt .env.example \
  data/landing/.gitkeep infra/.gitkeep dbt/README.md
git commit -m "chore(job_search): scaffold monorepo layout and tool config"
```

---

## Task 2: `core.settings` — pydantic-settings config module

**Files:**
- Create: `job_search/packages/core/core/__init__.py`
- Create: `job_search/packages/core/core/settings.py`
- Create: `job_search/packages/core/tests/__init__.py`
- Create: `job_search/packages/core/tests/test_settings.py`

**Interfaces:**
- Produces: `class Settings(BaseSettings)` with fields `env`, `database_url`, `app_database_url`, `db_connection_mode`, `landing_uri`, `embedding_provider`, `embedding_model`, `ollama_base_url`, `llm_provider`, `anthropic_api_key`, `adzuna_app_id`, `adzuna_app_key`, `reed_api_key`, `jooble_key`, `api_base_url`. Function `get_settings() -> Settings` (cached singleton via `functools.lru_cache`).

- [ ] **Step 1: Write the failing test**

```python
# job_search/packages/core/tests/test_settings.py
from __future__ import annotations

import unittest

from core.settings import Settings, get_settings


class TestSettings(unittest.TestCase):
    def test_reads_required_fields_from_env(self) -> None:
        settings = Settings(
            _env_file=None,
            database_url="postgresql+psycopg://owner:pw@localhost:5432/job_search",
            app_database_url="postgresql+psycopg://app:pw@localhost:5432/job_search",
        )
        self.assertEqual(
            settings.database_url,
            "postgresql+psycopg://owner:pw@localhost:5432/job_search",
        )

    def test_defaults_env_to_local(self) -> None:
        settings = Settings(
            _env_file=None,
            database_url="postgresql+psycopg://owner:pw@localhost:5432/job_search",
            app_database_url="postgresql+psycopg://app:pw@localhost:5432/job_search",
        )
        self.assertEqual(settings.env, "local")

    def test_rejects_unknown_env_value(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                env="staging",
                database_url="postgresql+psycopg://owner:pw@localhost:5432/job_search",
                app_database_url="postgresql+psycopg://app:pw@localhost:5432/job_search",
            )

    def test_get_settings_returns_cached_singleton(self) -> None:
        import os

        os.environ["DATABASE_URL"] = "postgresql+psycopg://owner:pw@localhost:5432/job_search"
        os.environ["APP_DATABASE_URL"] = "postgresql+psycopg://app:pw@localhost:5432/job_search"
        get_settings.cache_clear()
        first = get_settings()
        second = get_settings()
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_settings -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'core'` (or `core.settings`).

- [ ] **Step 3: Write `job_search/packages/core/core/__init__.py`**

```python
"""Shared library code used by apps/api, apps/ui, and apps/pipeline."""
```

- [ ] **Step 4: Write `job_search/packages/core/core/settings.py`**

```python
"""Application configuration, sourced entirely from environment variables.

Every environment difference (local vs. GCP) is an environment variable
here, never a code path or a separate image — see PLAN.md Step 1.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration, one instance per process.

    Attributes:
        env: Deployment environment. Drives no behaviour by itself — it
            exists so individual settings (e.g. embedding provider) can be
            environment-appropriate defaults, per the "every difference is
            an env var" rule.
        database_url: DSN for the migration/owner Postgres role. This role
            owns every table and therefore bypasses row-level security —
            used only by Alembic and one-off bootstrap scripts, never by
            request-serving code.
        app_database_url: DSN for the `job_search_app` Postgres role. This
            role is subject to row-level security on every per-user table
            and is the only role the API and pipeline use at runtime.
        db_connection_mode: How the app resolves a live connection from the
            DSNs above. "dsn" uses the DSN directly (local, Neon); GCP's
            Cloud SQL IAM-auth path is a later addition, not built here.
        landing_uri: Root URI of the immutable landing zone. `file://` for
            local, `gs://` in GCP — the code path never branches on this.
        embedding_provider: "ollama" locally; GCP still reads
            Ollama-generated vectors (see DECISIONS.md §1's Ollama-always
            rule) rather than switching provider.
        embedding_model: Name of the embedding model in use. Stored
            alongside every vector at query time so a future model change
            is detectable, not silent.
        ollama_base_url: Base URL of the local Ollama server.
        llm_provider: Informational default only. It is NEVER used to route
            an individual task's model choice — see core.llm.task_config,
            which resolves (task, provider, model) per call from
            config/llm_tasks.yml. A global switch here is exactly the
            anti-pattern DECISIONS.md §1 rejects.
        anthropic_api_key: API key for the Anthropic adapter. None when no
            Anthropic-routed task is configured yet.
        adzuna_app_id: Adzuna connector application ID.
        adzuna_app_key: Adzuna connector application key.
        reed_api_key: Reed.co.uk connector API key.
        jooble_key: Jooble connector API key.
        api_base_url: Base URL the UI and pipeline use to reach the API.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["local", "gcp"] = "local"

    database_url: str
    app_database_url: str
    db_connection_mode: Literal["dsn", "cloud_sql_connector"] = "dsn"

    landing_uri: str = "file:///data/landing"

    embedding_provider: Literal["ollama", "vertex"] = "ollama"
    embedding_model: str = "nomic-embed-text"
    ollama_base_url: str = "http://ollama:11434"

    llm_provider: str = "anthropic"
    anthropic_api_key: str | None = None

    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None
    reed_api_key: str | None = None
    jooble_key: str | None = None

    api_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide `Settings` singleton.

    Returns:
        The cached `Settings` instance, constructed once per process from
        the environment (and `.env` locally).
    """
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_settings -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
cd job_search
git add packages/core/core/__init__.py packages/core/core/settings.py \
  packages/core/tests/__init__.py packages/core/tests/test_settings.py
git commit -m "feat(job_search): add pydantic-settings config module"
```

---

## Task 3: `core.llm.task_config` — per-task provider/model resolution

**Files:**
- Create: `job_search/config/llm_tasks.yml`
- Create: `job_search/packages/core/core/llm/__init__.py`
- Create: `job_search/packages/core/core/llm/task_config.py`
- Create: `job_search/packages/core/tests/test_task_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `@dataclass(frozen=True) class TaskConfig` with fields `task: str`, `provider: Literal["ollama", "anthropic"]`, `model: str`, `prompt_family: str`. Function `load_task_config(task: str, config_path: Path | None = None) -> TaskConfig`, raising `TaskConfigError` (a plain `Exception` subclass defined in this module) if `task` is not in the YAML.

- [ ] **Step 1: Write the failing test**

```python
# job_search/packages/core/tests/test_task_config.py
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.llm.task_config import TaskConfig, TaskConfigError, load_task_config

_SAMPLE_YAML = """
tasks:
  skill_extraction:
    provider: ollama
    model: llama3.1:8b
    prompt_family: local
  fabrication_critic:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
"""


class TestLoadTaskConfig(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        )
        self._tmp.write(_SAMPLE_YAML)
        self._tmp.close()
        self.config_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self.config_path.unlink(missing_ok=True)

    def test_resolves_a_known_task(self) -> None:
        config = load_task_config("skill_extraction", config_path=self.config_path)
        self.assertEqual(
            config,
            TaskConfig(
                task="skill_extraction",
                provider="ollama",
                model="llama3.1:8b",
                prompt_family="local",
            ),
        )

    def test_resolves_a_second_known_task_on_a_different_provider(self) -> None:
        config = load_task_config("fabrication_critic", config_path=self.config_path)
        self.assertEqual(config.provider, "anthropic")
        self.assertEqual(config.model, "claude-sonnet-5")

    def test_raises_on_unknown_task(self) -> None:
        with self.assertRaises(TaskConfigError):
            load_task_config("does_not_exist", config_path=self.config_path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_task_config -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm'`.

- [ ] **Step 3: Write `job_search/packages/core/core/llm/__init__.py`**

```python
"""The LLM gateway: per-task provider resolution, adapters, and call logging."""
```

- [ ] **Step 4: Write `job_search/packages/core/core/llm/task_config.py`**

```python
"""Per-task LLM provider/model resolution.

Model resolves per TASK from `config/llm_tasks.yml`, never from a single
global provider switch — see DECISIONS.md §1. This is what lets the
local/target boundary move one task at a time instead of forcing an
all-or-nothing migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "llm_tasks.yml"
)


class TaskConfigError(Exception):
    """Raised when a requested task has no entry in the task config file."""


@dataclass(frozen=True)
class TaskConfig:
    """Resolved provider/model configuration for one LLM task.

    Attributes:
        task: The task name, e.g. "skill_extraction".
        provider: Which adapter serves this task.
        model: The provider-specific model identifier.
        prompt_family: Which prompt variant family to load — prompts are
            versioned per (task, model_family) and never converted between
            families (DECISIONS.md §1).
    """

    task: str
    provider: Literal["ollama", "anthropic"]
    model: str
    prompt_family: str


def load_task_config(task: str, config_path: Path | None = None) -> TaskConfig:
    """Resolve a task's provider/model configuration from YAML.

    Args:
        task: The task name to resolve, e.g. "skill_extraction".
        config_path: Path to the task-config YAML file. Defaults to
            `config/llm_tasks.yml` at the repository root.

    Returns:
        The resolved `TaskConfig` for the requested task.

    Raises:
        TaskConfigError: If `task` has no entry in the config file.
    """
    path = config_path or _DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text())
    tasks = raw.get("tasks", {}) if raw else {}

    if task not in tasks:
        raise TaskConfigError(
            f"No task-config entry for {task!r} in {path}. "
            f"Known tasks: {sorted(tasks)}"
        )

    entry = tasks[task]
    return TaskConfig(
        task=task,
        provider=entry["provider"],
        model=entry["model"],
        prompt_family=entry["prompt_family"],
    )
```

- [ ] **Step 5: Write `job_search/config/llm_tasks.yml`**

```yaml
# Per-task LLM provider/model routing. See DECISIONS.md §1 — the whole
# point of this file is that a task's provider moves independently of
# every other task's, with no global switch anywhere in the code.
#
# The task list mirrors the split in DECISIONS.md §1's "task split" table.
# Populated as each task's producing step (Step 13+) is implemented;
# entries below are the ones referenced by name elsewhere in this repo.
tasks:
  fabrication_critic:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_task_config -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
cd job_search
git add config/llm_tasks.yml packages/core/core/llm/__init__.py \
  packages/core/core/llm/task_config.py packages/core/tests/test_task_config.py
git commit -m "feat(job_search): add per-task LLM provider/model resolution"
```

---

## Task 4: `core.llm` adapters and call logging

**Files:**
- Create: `job_search/packages/core/core/llm/types.py`
- Create: `job_search/packages/core/core/llm/call_log.py`
- Create: `job_search/packages/core/core/llm/adapters/__init__.py`
- Create: `job_search/packages/core/core/llm/adapters/ollama.py`
- Create: `job_search/packages/core/core/llm/adapters/anthropic.py`
- Create: `job_search/packages/core/tests/test_llm_adapters.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (adapters are self-contained).
- Produces: `class LLMResponse` (dataclass: `text: str`, `provider: str`, `model: str`, `input_tokens: int`, `output_tokens: int`) in `core.llm.types`. `class LLMAdapter(Protocol)` with `def complete(self, *, model: str, prompt: str) -> LLMResponse`. `class OllamaAdapter` and `class AnthropicAdapter`, both implementing `LLMAdapter`, each constructed with an injectable HTTP/SDK client so tests never hit the network. `def log_llm_call(*, task: str, response: LLMResponse, prompt_version: str) -> dict[str, object]` in `core.llm.call_log`, returning the structured record it logs (so tests can assert on it) and emitting it via the standard `logging` module at INFO level.

- [ ] **Step 1: Write the failing test**

```python
# job_search/packages/core/tests/test_llm_adapters.py
from __future__ import annotations

import logging
import unittest
from unittest import mock

import httpx

from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.call_log import log_llm_call
from core.llm.types import LLMResponse


class TestOllamaAdapter(unittest.TestCase):
    def test_complete_parses_ollama_response_shape(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/generate")
            return httpx.Response(
                200,
                json={
                    "response": "hello from ollama",
                    "prompt_eval_count": 12,
                    "eval_count": 7,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OllamaAdapter(base_url="http://ollama:11434", client=client)

        result = adapter.complete(model="llama3.1:8b", prompt="say hello")

        self.assertEqual(result.text, "hello from ollama")
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model, "llama3.1:8b")
        self.assertEqual(result.input_tokens, 12)
        self.assertEqual(result.output_tokens, 7)


class TestAnthropicAdapter(unittest.TestCase):
    def test_complete_parses_anthropic_response_shape(self) -> None:
        fake_message = mock.Mock()
        fake_message.content = [mock.Mock(text="hello from claude")]
        fake_message.usage = mock.Mock(input_tokens=20, output_tokens=9)

        fake_client = mock.Mock()
        fake_client.messages.create.return_value = fake_message

        adapter = AnthropicAdapter(api_key="test-key", client=fake_client)
        result = adapter.complete(model="claude-sonnet-5", prompt="say hello")

        self.assertEqual(result.text, "hello from claude")
        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(result.input_tokens, 20)
        self.assertEqual(result.output_tokens, 9)
        fake_client.messages.create.assert_called_once_with(
            model="claude-sonnet-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": "say hello"}],
        )


class TestLogLlmCall(unittest.TestCase):
    def test_returns_and_logs_structured_record(self) -> None:
        response = LLMResponse(
            text="hi",
            provider="ollama",
            model="llama3.1:8b",
            input_tokens=1,
            output_tokens=1,
        )
        with self.assertLogs("core.llm.call_log", level="INFO") as captured:
            record = log_llm_call(
                task="skill_extraction", response=response, prompt_version="local.v1"
            )

        self.assertEqual(record["task"], "skill_extraction")
        self.assertEqual(record["provider"], "ollama")
        self.assertEqual(record["prompt_version"], "local.v1")
        self.assertEqual(record["input_tokens"], 1)
        self.assertEqual(record["output_tokens"], 1)
        self.assertTrue(any("skill_extraction" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_adapters -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm.types'`.

- [ ] **Step 3: Write `job_search/packages/core/core/llm/types.py`**

```python
"""Shared types for the LLM gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    """The normalised shape every adapter returns, regardless of provider.

    Attributes:
        text: The completion text.
        provider: Which adapter produced this response ("ollama" or
            "anthropic").
        model: The provider-specific model identifier used.
        input_tokens: Prompt token count, as reported by the provider.
        output_tokens: Completion token count, as reported by the provider.
    """

    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMAdapter(Protocol):
    """The interface every provider adapter implements identically."""

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Run one completion call.

        Args:
            model: The provider-specific model identifier.
            prompt: The prompt text.

        Returns:
            The normalised `LLMResponse`.
        """
        ...
```

- [ ] **Step 4: Write `job_search/packages/core/core/llm/adapters/__init__.py`**

```python
"""Provider adapters — identical call signature and response shape."""
```

- [ ] **Step 5: Write `job_search/packages/core/core/llm/adapters/ollama.py`**

```python
"""Ollama adapter — local completions via Ollama's HTTP API."""

from __future__ import annotations

import httpx

from core.llm.types import LLMResponse


class OllamaAdapter:
    """Calls a local Ollama server's `/api/generate` endpoint.

    Attributes:
        base_url: Base URL of the Ollama server.
        client: Injected HTTP client — a real `httpx.Client` at runtime, a
            `httpx.MockTransport`-backed one in tests, so no test needs a
            live Ollama server.
    """

    def __init__(self, *, base_url: str, client: httpx.Client) -> None:
        """Initialise the adapter.

        Args:
            base_url: Base URL of the Ollama server, e.g.
                "http://ollama:11434".
            client: The HTTP client to issue requests with.
        """
        self.base_url = base_url
        self.client = client

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Run one completion call against Ollama.

        Args:
            model: The Ollama model tag, e.g. "llama3.1:8b".
            prompt: The prompt text.

        Returns:
            The normalised `LLMResponse`.
        """
        response = self.client.post(
            f"{self.base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        response.raise_for_status()
        payload = response.json()
        return LLMResponse(
            text=payload["response"],
            provider="ollama",
            model=model,
            input_tokens=payload.get("prompt_eval_count", 0),
            output_tokens=payload.get("eval_count", 0),
        )
```

- [ ] **Step 6: Write `job_search/packages/core/core/llm/adapters/anthropic.py`**

```python
"""Anthropic adapter — target-provider completions via the Claude API."""

from __future__ import annotations

from typing import Any

from core.llm.types import LLMResponse

_MAX_TOKENS = 4096


class AnthropicAdapter:
    """Calls the Anthropic Messages API.

    Attributes:
        api_key: The Anthropic API key (used only when `client` isn't
            injected — tests always inject a fake client instead).
        client: Injected Anthropic SDK client (`anthropic.Anthropic`-shaped:
            exposes `.messages.create(...)`), so no test needs network
            access or a real API key.
    """

    def __init__(self, *, api_key: str | None, client: Any) -> None:
        """Initialise the adapter.

        Args:
            api_key: The Anthropic API key. May be `None` when `client` is
                already constructed (as in every unit test).
            client: The Anthropic SDK client to issue requests with.
        """
        self.api_key = api_key
        self.client = client

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Run one completion call against Claude.

        Args:
            model: The Anthropic model identifier, e.g. "claude-sonnet-5".
            prompt: The prompt text.

        Returns:
            The normalised `LLMResponse`.
        """
        message = self.client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMResponse(
            text=message.content[0].text,
            provider="anthropic",
            model=model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
```

- [ ] **Step 7: Write `job_search/packages/core/core/llm/call_log.py`**

```python
"""Structured logging for every LLM call — provider, model, tokens, cost."""

from __future__ import annotations

import logging

from core.llm.types import LLMResponse

_logger = logging.getLogger(__name__)


def log_llm_call(
    *, task: str, response: LLMResponse, prompt_version: str
) -> dict[str, object]:
    """Log one LLM call's provider, model, tokens, and prompt version.

    Every call is logged from day one (PLAN.md Step 1) so cost and quality
    are traceable to a specific change rather than to vibes (DECISIONS.md
    §1 / Step 12a).

    Args:
        task: The task name this call served, e.g. "skill_extraction".
        response: The adapter's `LLMResponse`.
        prompt_version: The versioned prompt identifier that produced this
            call's prompt, e.g. "local.v7".

    Returns:
        The structured record that was logged, for callers/tests that want
        to persist or assert on it directly.
    """
    record: dict[str, object] = {
        "task": task,
        "provider": response.provider,
        "model": response.model,
        "prompt_version": prompt_version,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
    }
    _logger.info("llm_call task=%s provider=%s model=%s", task, response.provider, response.model, extra=record)
    return record
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_adapters -v`
Expected: PASS (3 tests).

- [ ] **Step 9: Commit**

```bash
cd job_search
git add packages/core/core/llm/types.py packages/core/core/llm/call_log.py \
  packages/core/core/llm/adapters/ packages/core/tests/test_llm_adapters.py
git commit -m "feat(job_search): add Ollama/Anthropic adapters and call logging"
```

---

## Task 5: `core.llm.gateway.complete()` — the public entrypoint

**Files:**
- Create: `job_search/packages/core/core/llm/gateway.py`
- Create: `job_search/packages/core/tests/test_llm_gateway.py`

**Interfaces:**
- Consumes: `TaskConfig`/`load_task_config` (Task 3), `LLMAdapter`/`LLMResponse` (Task 4), `log_llm_call` (Task 4).
- Produces: `def complete(task: str, prompt: str, *, prompt_version: str, adapters: dict[str, LLMAdapter], config_path: Path | None = None) -> LLMResponse` — resolves the task's `TaskConfig`, picks the adapter keyed by `TaskConfig.provider` out of `adapters`, calls it, logs the call, returns the response. Raises `TaskConfigError` (re-exported from `core.llm.task_config`) for an unresolvable task.

- [ ] **Step 1: Write the failing test**

```python
# job_search/packages/core/tests/test_llm_gateway.py
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.llm.gateway import complete
from core.llm.task_config import TaskConfigError
from core.llm.types import LLMResponse

_SAMPLE_YAML = """
tasks:
  skill_extraction:
    provider: fake
    model: fake-model-v1
    prompt_family: local
"""


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        self.calls.append((model, prompt))
        return LLMResponse(
            text=f"echo: {prompt}",
            provider="fake",
            model=model,
            input_tokens=3,
            output_tokens=5,
        )


class TestGatewayComplete(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        tmp.write(_SAMPLE_YAML)
        tmp.close()
        self.config_path = Path(tmp.name)
        self.fake_adapter = _FakeAdapter()

    def tearDown(self) -> None:
        self.config_path.unlink(missing_ok=True)

    def test_routes_to_the_configured_adapter_and_returns_its_response(self) -> None:
        result = complete(
            "skill_extraction",
            "extract skills from this JD",
            prompt_version="local.v1",
            adapters={"fake": self.fake_adapter},
            config_path=self.config_path,
        )

        self.assertEqual(result.text, "echo: extract skills from this JD")
        self.assertEqual(
            self.fake_adapter.calls,
            [("fake-model-v1", "extract skills from this JD")],
        )

    def test_raises_when_task_has_no_config_entry(self) -> None:
        with self.assertRaises(TaskConfigError):
            complete(
                "unknown_task",
                "prompt",
                prompt_version="local.v1",
                adapters={"fake": self.fake_adapter},
                config_path=self.config_path,
            )

    def test_raises_when_configured_provider_has_no_adapter_supplied(self) -> None:
        with self.assertRaises(KeyError):
            complete(
                "skill_extraction",
                "prompt",
                prompt_version="local.v1",
                adapters={},  # no "fake" adapter provided
                config_path=self.config_path,
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_gateway -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm.gateway'`.

- [ ] **Step 3: Write `job_search/packages/core/core/llm/gateway.py`**

```python
"""The LLM gateway's public entrypoint: `llm.complete(task=..., ...)`.

This is the module every future LLM-calling step imports — Step 13's CV
extraction, Step 15's re-rank, Step 17's tailoring and critic, and so on.
None of them ever choose a provider directly; they name a task and this
module resolves it (DECISIONS.md §1).
"""

from __future__ import annotations

from pathlib import Path

from core.llm.call_log import log_llm_call
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter, LLMResponse


def complete(
    task: str,
    prompt: str,
    *,
    prompt_version: str,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> LLMResponse:
    """Run a completion for `task`, routed to its configured provider.

    Args:
        task: The task name, resolved against `config/llm_tasks.yml`.
        prompt: The prompt text to send.
        prompt_version: The versioned prompt identifier that produced
            `prompt` — stamped onto the call log and, later, onto every
            generated artefact row.
        adapters: Every available adapter, keyed by provider name (e.g.
            `{"ollama": OllamaAdapter(...), "anthropic": AnthropicAdapter(...)}`).
            Callers construct and inject these explicitly rather than the
            gateway constructing them, so tests never need real credentials
            or network access.
        config_path: Path to the task-config YAML. Defaults to
            `config/llm_tasks.yml` at the repository root.

    Returns:
        The adapter's `LLMResponse`.

    Raises:
        core.llm.task_config.TaskConfigError: If `task` has no entry in the
            task config file.
        KeyError: If the resolved provider has no matching entry in
            `adapters`.
    """
    task_config = load_task_config(task, config_path=config_path)
    adapter = adapters[task_config.provider]
    response = adapter.complete(model=task_config.model, prompt=prompt)
    log_llm_call(task=task, response=response, prompt_version=prompt_version)
    return response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_gateway -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full unit suite so far**

Run: `cd job_search/packages/core && coverage run -m unittest discover && coverage report -m`
Expected: all tests pass; coverage report prints with no import errors.

- [ ] **Step 6: Commit**

```bash
cd job_search
git add packages/core/core/llm/gateway.py packages/core/tests/test_llm_gateway.py
git commit -m "feat(job_search): add llm.complete() gateway entrypoint"
```

---

## Task 6: `apps/api` — Dockerfile and FastAPI hello-world

**Files:**
- Create: `job_search/apps/api/Dockerfile`
- Create: `job_search/apps/api/app/__init__.py`
- Create: `job_search/apps/api/app/main.py`
- Create: `job_search/packages/core/tests/test_api_health.py`

**Interfaces:**
- Consumes: nothing yet from `core` (identity wiring for `/whoami` lands in Task 13).
- Produces: FastAPI `app` object in `apps/api/app/main.py` with `GET /health -> {"status": "ok"}`.

- [ ] **Step 1: Write the failing test**

```python
# job_search/packages/core/tests/test_api_health.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.main import app  # noqa: E402


class TestHealthEndpoint(unittest.TestCase):
    def test_health_returns_ok(self) -> None:
        client = TestClient(app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_api_health -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 3: Write `job_search/apps/api/app/__init__.py`**

```python
"""FastAPI application — all business logic lives behind this API
(DECISIONS.md §2.4); Streamlit and n8n are clients, never a second copy of
the logic."""
```

- [ ] **Step 4: Write `job_search/apps/api/app/main.py`**

```python
"""FastAPI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Job Search Platform API")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check.

    Returns:
        A fixed `{"status": "ok"}` payload once the process is serving
        requests.
    """
    return {"status": "ok"}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_api_health -v`
Expected: PASS.

- [ ] **Step 6: Write `job_search/apps/api/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/core /app/packages/core
COPY apps/api/app /app/apps/api/app

ENV PYTHONPATH=/app/packages/core:/app/apps/api

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/apps/api"]
```

- [ ] **Step 7: Commit**

```bash
cd job_search
git add apps/api/Dockerfile apps/api/app/__init__.py apps/api/app/main.py \
  packages/core/tests/test_api_health.py
git commit -m "feat(job_search): add FastAPI hello-world app and Dockerfile"
```

---

## Task 7: `apps/ui` — Dockerfile and Streamlit hello-world

**Files:**
- Create: `job_search/apps/ui/Dockerfile`
- Create: `job_search/apps/ui/app/Home.py`

**Interfaces:**
- Produces: a Streamlit page that renders without error. Streamlit apps aren't unit-testable the way FastAPI's `TestClient` allows, so verification here is a manual run step, not an automated test — consistent with "no placeholder tests."

- [ ] **Step 1: Write `job_search/apps/ui/app/Home.py`**

```python
"""Streamlit entrypoint."""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Job Search Platform", layout="wide")
st.title("Job Search Platform")
st.write("Skeleton is up. Manual job entry lands in Step 2.")
```

- [ ] **Step 2: Write `job_search/apps/ui/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/core /app/packages/core
COPY apps/ui/app /app/apps/ui/app

ENV PYTHONPATH=/app/packages/core

EXPOSE 8501

CMD ["streamlit", "run", "/app/apps/ui/app/Home.py", "--server.address=0.0.0.0", "--server.port=8501"]
```

- [ ] **Step 3: Verify the page renders (manual — no automated test for Streamlit UI)**

Run: `cd job_search && docker build -f apps/ui/Dockerfile -t job-search-ui-check .`
Expected: image builds successfully.

- [ ] **Step 4: Commit**

```bash
cd job_search
git add apps/ui/Dockerfile apps/ui/app/Home.py
git commit -m "feat(job_search): add Streamlit hello-world app and Dockerfile"
```

---

## Task 8: `apps/pipeline` — Dockerfile and placeholder batch entrypoint

**Files:**
- Create: `job_search/apps/pipeline/Dockerfile`
- Create: `job_search/apps/pipeline/app/__init__.py`
- Create: `job_search/apps/pipeline/app/cli.py`
- Create: `job_search/packages/core/tests/test_pipeline_cli.py`

**Interfaces:**
- Produces: `def main(argv: list[str] | None = None) -> int` in `apps/pipeline/app/cli.py` — an argparse entrypoint that currently accepts no subcommands (Step 3 adds `ingest`), prints a placeholder message, and returns exit code 0. This is real, tested behaviour (not a stub), scoped honestly to what Step 1 actually needs: proof the pipeline image runs as a batch job, not a server.

- [ ] **Step 1: Write the failing test**

```python
# job_search/packages/core/tests/test_pipeline_cli.py
from __future__ import annotations

import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402


class TestPipelineCli(unittest.TestCase):
    def test_runs_with_no_arguments_and_exits_zero(self) -> None:
        with mock.patch("sys.stdout", new_callable=StringIO) as stdout:
            exit_code = main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("pipeline scaffold ready", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_pipeline_cli -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'` (or stale `app` module cached from Task 6 — if so, note the two `apps/*/app` packages are only ever added to `sys.path` one at a time within a single test process; this test file is standalone and safe run individually or via full-suite discovery since `unittest discover`'s import isolation per module still shares `sys.modules`, so run this suite's Step 2/4 checks via the single-file command shown, not intermixed).

- [ ] **Step 3: Write `job_search/apps/pipeline/app/__init__.py`**

```python
"""Batch pipeline entrypoint — dlt + dbt + dedup, no server, no request
lifecycle. Runs as a Cloud Run Job in GCP (PLAN.md Step 1 / Step 12)."""
```

- [ ] **Step 4: Write `job_search/apps/pipeline/app/cli.py`**

```python
"""Pipeline batch entrypoint.

Real subcommands (`ingest`, `dedup`, ...) land in Step 3+. This module's
job for Step 1 is only to prove the pipeline image runs as a one-shot batch
process rather than a long-lived server.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    """Run the pipeline CLI.

    Args:
        argv: Command-line arguments, excluding the program name. Defaults
            to `sys.argv[1:]` when `None`.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(prog="pipeline")
    parser.parse_args(argv)
    print("pipeline scaffold ready — no subcommands yet (see Step 3)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_pipeline_cli -v`
Expected: PASS.

- [ ] **Step 6: Write `job_search/apps/pipeline/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/core /app/packages/core
COPY apps/pipeline/app /app/apps/pipeline/app

ENV PYTHONPATH=/app/packages/core:/app/apps/pipeline

ENTRYPOINT ["python", "-m", "app.cli"]
```

- [ ] **Step 7: Commit**

```bash
cd job_search
git add apps/pipeline/Dockerfile apps/pipeline/app/__init__.py apps/pipeline/app/cli.py \
  packages/core/tests/test_pipeline_cli.py
git commit -m "feat(job_search): add pipeline batch image and placeholder CLI"
```

---

## Task 9: `docker-compose.yml` — wiring all services together

**Files:**
- Create: `job_search/docker-compose.yml`

**Interfaces:**
- Consumes: the three Dockerfiles (Tasks 6–8) and env vars from `.env.example` (Task 1).
- Produces: the `docker compose up` surface that Step 1's acceptance criterion checks directly.

- [ ] **Step 1: Write `job_search/docker-compose.yml`**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
      APP_DB_PASSWORD: ${APP_DB_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 10

  api:
    build:
      context: .
      dockerfile: apps/api/Dockerfile
    env_file: .env
    ports:
      - "8000:8000"
    volumes:
      - ./apps/api/app:/app/apps/api/app
      - ./packages/core:/app/packages/core
      - ./data/landing:/data/landing
    depends_on:
      postgres:
        condition: service_healthy

  ui:
    build:
      context: .
      dockerfile: apps/ui/Dockerfile
    env_file: .env
    ports:
      - "8501:8501"
    volumes:
      - ./apps/ui/app:/app/apps/ui/app
      - ./packages/core:/app/packages/core
    depends_on:
      - api

  pipeline:
    build:
      context: .
      dockerfile: apps/pipeline/Dockerfile
    env_file: .env
    profiles: [cli]
    volumes:
      - ./apps/pipeline/app:/app/apps/pipeline/app
      - ./packages/core:/app/packages/core
      - ./data/landing:/data/landing
    depends_on:
      postgres:
        condition: service_healthy

  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama

  n8n:
    image: n8nio/n8n
    profiles: [orchestration]
    ports:
      - "5678:5678"
    volumes:
      - n8n_data:/home/node/.n8n

volumes:
  postgres_data:
  ollama_data:
  n8n_data:
```

- [ ] **Step 2: Validate the compose file**

Run: `cd job_search && cp .env.example .env && docker compose config >/dev/null`
Expected: no errors (valid YAML, all `${VAR}` interpolations resolve against `.env.example`'s defaults).

- [ ] **Step 3: Commit**

```bash
cd job_search
git add docker-compose.yml
git commit -m "feat(job_search): add docker-compose service topology"
```

---

## Task 10: Postgres roles — migration/owner vs. `job_search_app`

**Files:**
- Create: `job_search/db/init/01_create_roles.sh`

**Interfaces:**
- Consumes: `POSTGRES_USER`, `POSTGRES_DB`, `APP_DB_PASSWORD` (env, from Task 1/9).
- Produces: the `job_search_app` Postgres role that Task 12's RLS policies and Task 14's isolation test both depend on.

- [ ] **Step 1: Write `job_search/db/init/01_create_roles.sh`**

```bash
#!/bin/bash
# Runs once, automatically, the first time the postgres data volume is
# initialised (docker-entrypoint-initdb.d semantics — never on restart of
# an existing volume). Creates the RLS-enforced application role.
#
# The migration/owner role is POSTGRES_USER itself: as the tables' owner it
# bypasses row-level security by default, which is exactly the "migration
# role bypasses RLS" split PLAN.md Step 1a calls for — no second superuser
# role is needed for that half.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'job_search_app') THEN
        CREATE ROLE job_search_app LOGIN PASSWORD '${APP_DB_PASSWORD}';
      END IF;
    END
    \$\$;
EOSQL
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x job_search/db/init/01_create_roles.sh`

- [ ] **Step 3: Verify role creation against a throwaway Postgres**

Run: `cd job_search && docker compose up -d postgres && sleep 3 && docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\du" | grep job_search_app`
Expected: a `job_search_app` row is printed.

Run: `docker compose down -v` (tears the throwaway volume back down — safe, this environment has no real data yet).

- [ ] **Step 4: Commit**

```bash
cd job_search
git add db/init/01_create_roles.sh
git commit -m "feat(job_search): create the RLS-enforced job_search_app Postgres role"
```

---

## Task 11: Alembic setup and migration 0001 — `app_user`

**Files:**
- Create: `job_search/db/alembic.ini`
- Create: `job_search/db/migrations/env.py`
- Create: `job_search/db/migrations/script.py.mako`
- Create: `job_search/db/migrations/versions/0001_create_app_user.py`

**Interfaces:**
- Consumes: `Settings.database_url` (Task 2) as the migration DSN.
- Produces: the `app_user` table, RLS-enabled with a self-row policy, `job_search_app` granted `SELECT, INSERT, UPDATE` (never `DELETE` — users aren't deleted via the app role) on it.

- [ ] **Step 1: Write `job_search/db/alembic.ini`**

```ini
[alembic]
script_location = migrations
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[logger_root]
level = WARNING
handlers = console

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handlers]
keys = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic

[formatters]
keys = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 2: Write `job_search/db/migrations/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 3: Write `job_search/db/migrations/env.py`**

```python
"""Alembic environment — connects using the migration/owner DSN, which
owns every table and therefore bypasses row-level security, per the
migration-role-bypasses-RLS split (PLAN.md Step 1a)."""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "core"))

from core.settings import get_settings  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no live connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live connection, owner role)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Write `job_search/db/migrations/versions/0001_create_app_user.py`**

```python
"""create app_user

Revision ID: 0001
Revises:
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "app_user",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("locale", sa.Text(), nullable=False, server_default="en-GB"),
    )

    # Every per-user table gets RLS enabled and a policy keyed on the fixed
    # `app.current_user_id` GUC — this table is the first of many that will
    # follow exactly this two-statement pattern (see Global Constraints).
    op.execute("ALTER TABLE app_user ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY app_user_isolation ON app_user
        USING (id = current_setting('app.current_user_id', true)::uuid)
        """
    )

    op.execute("GRANT USAGE ON SCHEMA public TO job_search_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app_user TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON app_user FROM job_search_app")
    op.drop_table("app_user")
```

- [ ] **Step 5: Run the migration against a live Postgres**

Run: `cd job_search && docker compose up -d postgres && sleep 3`
Run: `cd job_search/db && python3.11 -m pip install -r ../requirements.txt >/dev/null && alembic upgrade head`
Expected: `Running upgrade  -> 0001, create app_user` printed, no errors.

Run: `cd job_search && docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d app_user"`
Expected: table description printed, including `Policies: app_user_isolation`.

- [ ] **Step 6: Commit**

```bash
cd job_search
git add db/alembic.ini db/migrations/env.py db/migrations/script.py.mako \
  db/migrations/versions/0001_create_app_user.py
git commit -m "feat(job_search): add Alembic and the app_user migration with RLS"
```

---

## Task 12: Migration 0002 — `user_quota` and `shared_api_quota`

**Files:**
- Create: `job_search/db/migrations/versions/0002_create_quota_tables.py`
- Create: `job_search/packages/core/core/db/__init__.py`
- Create: `job_search/packages/core/core/db/models.py`

**Interfaces:**
- Consumes: `app_user` (Task 11).
- Produces: `user_quota` (per-user, RLS-enabled) and `shared_api_quota` (shared, no RLS — it tracks aggregate usage across users, not any one user's data) tables. `core.db.models` gains plain dataclasses `AppUser`, `UserQuota`, `SharedApiQuota` mirroring the columns, for typed access in later tasks (no ORM mapping yet — that's introduced when a task actually needs query-building, per YAGNI; these are read/write via `core.db.quota` in Task 13 using raw parameterised SQL).

- [ ] **Step 1: Write `job_search/db/migrations/versions/0002_create_quota_tables.py`**

```python
"""create user_quota and shared_api_quota

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_quota",
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
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column(
            "monthly_llm_spend_cap_usd", sa.Numeric(10, 2), nullable=False
        ),
        sa.Column(
            "monthly_llm_spend_used_usd",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("artefact_generation_cap", sa.Integer(), nullable=False),
        sa.Column(
            "artefact_generation_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("alert_cap", sa.Integer(), nullable=False),
        sa.Column("alert_used", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "period_start", name="uq_user_quota_period"),
    )
    op.create_index("ix_user_quota_user_id", "user_quota", ["user_id"])

    op.execute("ALTER TABLE user_quota ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY user_quota_isolation ON user_quota
        USING (user_id = current_setting('app.current_user_id', true)::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON user_quota TO job_search_app")

    # Shared across users by design — it tracks the aggregate Adzuna
    # allowance, not any individual's data, so it carries no user_id and no
    # RLS policy (see the "shared tables explicitly marked" subtask).
    op.create_table(
        "shared_api_quota",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("resource_name", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("total_limit", sa.Integer(), nullable=False),
        sa.Column("total_used", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "resource_name", "period_start", name="uq_shared_quota_period"
        ),
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON shared_api_quota TO job_search_app"
    )


def downgrade() -> None:
    op.drop_table("shared_api_quota")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON user_quota FROM job_search_app")
    op.drop_table("user_quota")
```

- [ ] **Step 2: Write `job_search/packages/core/core/db/__init__.py`**

```python
"""DB session management and the tenancy/RLS layer."""
```

- [ ] **Step 3: Write `job_search/packages/core/core/db/models.py`**

```python
"""Plain dataclasses mirroring the tenancy tables' columns.

No ORM mapping yet — introduced only when a later step's query needs it
(YAGNI). These types give `core.db.quota` and its tests something typed to
pass around instead of raw tuples.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AppUser:
    """One row of `app_user`."""

    id: uuid.UUID
    email: str
    display_name: str
    created_at: datetime.datetime
    status: str
    locale: str


@dataclass(frozen=True)
class UserQuota:
    """One row of `user_quota` — one user's caps for one billing period."""

    id: uuid.UUID
    user_id: uuid.UUID
    period_start: datetime.date
    monthly_llm_spend_cap_usd: Decimal
    monthly_llm_spend_used_usd: Decimal
    artefact_generation_cap: int
    artefact_generation_used: int
    alert_cap: int
    alert_used: int


@dataclass(frozen=True)
class SharedApiQuota:
    """One row of `shared_api_quota` — an aggregate cap shared by every
    user, e.g. Adzuna's 1,000 calls/month (PLAN.md Step 1a)."""

    id: uuid.UUID
    resource_name: str
    period_start: datetime.date
    total_limit: int
    total_used: int
```

- [ ] **Step 4: Run the new migration**

Run: `cd job_search/db && alembic upgrade head`
Expected: `Running upgrade 0001 -> 0002, create user_quota and shared_api_quota`, no errors.

- [ ] **Step 5: Commit**

```bash
cd job_search
git add db/migrations/versions/0002_create_quota_tables.py \
  packages/core/core/db/__init__.py packages/core/core/db/models.py
git commit -m "feat(job_search): add user_quota and shared_api_quota tables"
```

---

## Task 13: `core.db.session` — engines, `session_scope`, and the FastAPI identity seam

**Files:**
- Create: `job_search/packages/core/core/db/session.py`
- Create: `job_search/packages/core/tests/integration/__init__.py`
- Create: `job_search/packages/core/tests/integration/test_db_session.py`
- Modify: `job_search/apps/api/app/main.py`
- Create: `job_search/packages/core/tests/test_api_whoami.py`

**Interfaces:**
- Consumes: `Settings` (Task 2), `app_user`/`user_quota` tables (Tasks 11–12).
- Produces: `def build_engine(dsn: str) -> Engine`. `@contextmanager def session_scope(engine: Engine, *, user_id: uuid.UUID | None = None) -> Iterator[Connection]` — sets `SET LOCAL app.current_user_id` inside the transaction when `user_id` is given, yields a `sqlalchemy.Connection`, commits on success, rolls back on exception. FastAPI dependency `def get_current_user_id(request: Request) -> uuid.UUID` reading `request.state.user_id`, raising `HTTPException(501)` when unset (the honest statement that real identity binding is Step 22a's job, not a trusted client-supplied header).

- [ ] **Step 1: Write the failing integration test**

```python
# job_search/packages/core/tests/integration/test_db_session.py
from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

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


class TestSessionScope(unittest.TestCase):
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
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_session_scope_sets_the_session_guc_and_scopes_visibility(self) -> None:
        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            rows = conn.execute(text("SELECT id FROM app_user")).fetchall()
        self.assertEqual([row.id for row in rows], [self.user_id])

    def test_session_scope_without_user_id_sees_nothing_via_the_app_role(self) -> None:
        with session_scope(self.app_engine) as conn:
            rows = conn.execute(text("SELECT id FROM app_user")).fetchall()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_db_session -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.db.session'`.

- [ ] **Step 3: Write `job_search/packages/core/core/db/session.py`**

```python
"""DB engines and the per-request tenancy session context.

Every per-user query must run inside `session_scope(app_engine,
user_id=...)` — that's what sets the `app.current_user_id` GUC every RLS
policy in this project keys on. There is no other sanctioned way to set it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, Request
from sqlalchemy import Connection, Engine, create_engine, text


def build_engine(dsn: str) -> Engine:
    """Build a SQLAlchemy engine for the given DSN.

    Args:
        dsn: A `postgresql+psycopg://...` connection string — either the
            migration/owner DSN or the `job_search_app` DSN.

    Returns:
        A configured `Engine`. Callers are expected to reuse it (one per
        process), not build one per request.
    """
    return create_engine(dsn, pool_pre_ping=True)


@contextmanager
def session_scope(engine: Engine, *, user_id: uuid.UUID | None = None) -> Iterator[Connection]:
    """Open one transaction, optionally scoped to a user via RLS.

    Args:
        engine: The engine to connect through — the app-role engine for any
            per-user query, the migration/owner engine only for
            administrative work that must see across users.
        user_id: When given, sets `app.current_user_id` for the lifetime of
            this transaction via `SET LOCAL`, so every RLS policy in the
            database scopes to this user. When omitted, no GUC is set, so
            an app-role connection sees zero rows of any per-user table
            (fail-closed) and a migration-role connection sees everything
            (it bypasses RLS as the table owner regardless).

    Yields:
        A `Connection` with the transaction open.
    """
    with engine.connect() as conn:
        with conn.begin():
            if user_id is not None:
                # `SET LOCAL` does not support bound parameters; `user_id`
                # is a `uuid.UUID`, not client-supplied text, so its `str()`
                # is a validated UUID literal — safe to interpolate.
                conn.execute(text(f"SET LOCAL app.current_user_id = '{user_id}'"))
            yield conn


def get_current_user_id(request: Request) -> uuid.UUID:
    """FastAPI dependency resolving the authenticated user's ID.

    Deliberately never reads a client-supplied header or query parameter —
    PLAN.md Step 1a requires the session user context come "from a verified
    token only." No verified-token source exists yet (that's Step 22a's
    IAP integration), so this raises until `request.state.user_id` has been
    set by that future middleware. This is the seam Step 22a fills in, not
    a stand-in that trusts anything from the request.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The authenticated user's ID.

    Raises:
        fastapi.HTTPException: 501, when no identity middleware has set
            `request.state.user_id` yet.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(
            status_code=501,
            detail="Authentication not implemented yet — see Step 22a.",
        )
    return user_id
```

- [ ] **Step 4: Run the integration test**

Run: `cd job_search && docker compose up -d postgres && sleep 3 && (cd db && alembic upgrade head)`
Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_db_session -v`
Expected: PASS (2 tests). If Postgres isn't running, both tests report `SKIP` rather than failing — confirm this by also running with `docker compose stop postgres` once, to see the skip path, then bring it back up.

- [ ] **Step 5: Write the `/whoami` endpoint and its test**

Modify `job_search/apps/api/app/main.py`:

```python
"""FastAPI entrypoint."""

from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI

from core.db.session import get_current_user_id

app = FastAPI(title="Job Search Platform API")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check.

    Returns:
        A fixed `{"status": "ok"}` payload once the process is serving
        requests.
    """
    return {"status": "ok"}


@app.get("/whoami")
def whoami(user_id: uuid.UUID = Depends(get_current_user_id)) -> dict[str, str]:
    """Return the caller's resolved user ID.

    Args:
        user_id: Injected by `get_current_user_id` — 501s until Step 22a's
            identity middleware is in place.

    Returns:
        The caller's user ID as a string.
    """
    return {"user_id": str(user_id)}
```

Create `job_search/packages/core/tests/test_api_whoami.py`:

```python
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.main import app  # noqa: E402


class TestWhoamiEndpoint(unittest.TestCase):
    def test_returns_501_with_no_identity_middleware(self) -> None:
        client = TestClient(app)
        response = client.get("/whoami")
        self.assertEqual(response.status_code, 501)

    def test_returns_the_user_id_once_request_state_is_set(self) -> None:
        # Simulates what Step 22a's IAP identity middleware will do.
        user_id = uuid.uuid4()

        @app.middleware("http")
        async def _fake_identity_middleware(request, call_next):  # type: ignore[no-untyped-def]
            request.state.user_id = user_id
            return await call_next(request)

        try:
            client = TestClient(app)
            response = client.get("/whoami")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"user_id": str(user_id)})
        finally:
            app.user_middleware = [
                m for m in app.user_middleware if m.cls is not _fake_identity_middleware
            ]
            app.middleware_stack = app.build_middleware_stack()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run the whoami test**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_api_whoami -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
cd job_search
git add packages/core/core/db/session.py packages/core/tests/integration/__init__.py \
  packages/core/tests/integration/test_db_session.py apps/api/app/main.py \
  packages/core/tests/test_api_whoami.py
git commit -m "feat(job_search): add session_scope RLS context and /whoami seam"
```

---

## Task 14: The RLS negative-isolation proof test

**Files:**
- Create: `job_search/packages/core/tests/integration/test_rls_isolation.py`

**Interfaces:**
- Consumes: `session_scope`, `build_engine` (Task 13), `app_user` + `user_quota` (Tasks 11–12).
- Produces: the automated test that directly proves both stories' acceptance criteria — Step 1a's "a query run as user A cannot return user B's data even with WHERE omitted" and, by extension, Step 1's dependency on it.

- [ ] **Step 1: Write the failing test**

```python
# job_search/packages/core/tests/integration/test_rls_isolation.py
from __future__ import annotations

import datetime
import unittest
import uuid
from decimal import Decimal

from sqlalchemy import text

from core.db.session import build_engine, session_scope
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


class TestRowLevelSecurityIsolation(unittest.TestCase):
    """One negative test per per-user table (PLAN.md Step 1a)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.migration_engine = _live_migration_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_a = uuid.uuid4()
        self.user_b = uuid.uuid4()
        with session_scope(self.migration_engine) as conn:
            for user_id in (self.user_a, self.user_b):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'Test User')"
                    ),
                    {"id": user_id, "email": f"{user_id}@example.com"},
                )
                conn.execute(
                    text(
                        "INSERT INTO user_quota "
                        "(user_id, period_start, monthly_llm_spend_cap_usd, "
                        " artefact_generation_cap, alert_cap) "
                        "VALUES (:user_id, :period_start, 20, 50, 10)"
                    ),
                    {"user_id": user_id, "period_start": datetime.date(2026, 9, 1)},
                )

    def tearDown(self) -> None:
        with session_scope(self.migration_engine) as conn:
            for user_id in (self.user_a, self.user_b):
                conn.execute(
                    text("DELETE FROM user_quota WHERE user_id = :id"), {"id": user_id}
                )
                conn.execute(
                    text("DELETE FROM app_user WHERE id = :id"), {"id": user_id}
                )

    def test_app_user_query_without_where_returns_only_the_session_users_row(self) -> None:
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            rows = conn.execute(text("SELECT id FROM app_user")).fetchall()
        ids = {row.id for row in rows}
        self.assertIn(self.user_a, ids)
        self.assertNotIn(self.user_b, ids)

    def test_user_quota_query_without_where_returns_only_the_session_users_row(self) -> None:
        with session_scope(self.app_engine, user_id=self.user_a) as conn:
            rows = conn.execute(text("SELECT user_id FROM user_quota")).fetchall()
        user_ids = {row.user_id for row in rows}
        self.assertIn(self.user_a, user_ids)
        self.assertNotIn(self.user_b, user_ids)

    def test_switching_session_user_flips_visibility(self) -> None:
        with session_scope(self.app_engine, user_id=self.user_b) as conn:
            rows = conn.execute(text("SELECT user_id FROM user_quota")).fetchall()
        user_ids = {row.user_id for row in rows}
        self.assertIn(self.user_b, user_ids)
        self.assertNotIn(self.user_a, user_ids)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails (before this task, the file doesn't exist)**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_rls_isolation -v`
Expected: FAIL only if Postgres/migrations aren't up yet (`SKIP`, not a hard failure) — otherwise it should already PASS immediately, since Tasks 11–13 already built every piece this test exercises. That's fine: this task's job is to add the acceptance-proving test itself, not new production code.

- [ ] **Step 3: Ensure Postgres and migrations are current, then run for real**

Run: `cd job_search && docker compose up -d postgres && sleep 3 && (cd db && alembic upgrade head)`
Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_rls_isolation -v`
Expected: PASS (3 tests) — this is the literal proof of both stories' acceptance criteria.

- [ ] **Step 4: Commit**

```bash
cd job_search
git add packages/core/tests/integration/test_rls_isolation.py
git commit -m "test(job_search): prove RLS isolation across app_user and user_quota"
```

---

## Task 15: Shared quota guard and the two-zone documentation

**Files:**
- Create: `job_search/packages/core/core/db/quota.py`
- Create: `job_search/packages/core/tests/integration/test_quota.py`
- Create: `job_search/docs/tenancy.md`

**Interfaces:**
- Consumes: `session_scope`, `build_engine` (Task 13), `shared_api_quota` (Task 12).
- Produces: `def check_and_increment_shared_quota(conn: Connection, *, resource_name: str, period_start: date, amount: int = 1) -> bool` — atomically checks `total_used + amount <= total_limit` and increments if so, returning whether it succeeded. This is the reusable "fair-use guard" subtask; Step 3/4's connector runner calls it once it exists — no caller exists yet, so this task only builds and tests the guard itself.

- [ ] **Step 1: Write the failing test**

```python
# job_search/packages/core/tests/integration/test_quota.py
from __future__ import annotations

import datetime
import unittest
import uuid

from sqlalchemy import text

from core.db.quota import check_and_increment_shared_quota
from core.db.session import build_engine, session_scope
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


class TestSharedQuotaGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration_engine = _live_migration_engine()

    def setUp(self) -> None:
        self.resource_name = f"test_resource_{uuid.uuid4().hex[:8]}"
        self.period_start = datetime.date(2026, 9, 1)
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text(
                    "INSERT INTO shared_api_quota "
                    "(resource_name, period_start, total_limit) "
                    "VALUES (:name, :period, 2)"
                ),
                {"name": self.resource_name, "period": self.period_start},
            )

    def tearDown(self) -> None:
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text("DELETE FROM shared_api_quota WHERE resource_name = :name"),
                {"name": self.resource_name},
            )

    def test_allows_calls_up_to_the_limit_then_blocks(self) -> None:
        with session_scope(self.migration_engine) as conn:
            first = check_and_increment_shared_quota(
                conn, resource_name=self.resource_name, period_start=self.period_start
            )
            second = check_and_increment_shared_quota(
                conn, resource_name=self.resource_name, period_start=self.period_start
            )
            third = check_and_increment_shared_quota(
                conn, resource_name=self.resource_name, period_start=self.period_start
            )
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertFalse(third)

    def test_unknown_resource_period_is_blocked_not_an_error(self) -> None:
        with session_scope(self.migration_engine) as conn:
            allowed = check_and_increment_shared_quota(
                conn, resource_name="never_configured", period_start=self.period_start
            )
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_quota -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.db.quota'`.

- [ ] **Step 3: Write `job_search/packages/core/core/db/quota.py`**

```python
"""The fair-use guard on shared API quotas (PLAN.md Step 1a).

One user's heavy ingestion month must not starve the other user's — this
function is the few lines that enforce that, called by Step 3/4's connector
runner before each request against a rate-limited shared source.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Connection, text


def check_and_increment_shared_quota(
    conn: Connection,
    *,
    resource_name: str,
    period_start: datetime.date,
    amount: int = 1,
) -> bool:
    """Atomically check and consume shared quota for one resource/period.

    Args:
        conn: An open connection inside a transaction (typically from
            `session_scope`). The UPDATE below is atomic with respect to
            concurrent callers on the same row because Postgres locks the
            row for the duration of the UPDATE.
        resource_name: The shared resource being consumed, e.g. "adzuna".
        period_start: The billing period this call counts against.
        amount: How many units this call consumes. Defaults to 1.

    Returns:
        `True` if quota was available and has now been consumed. `False`
        if the resource/period has no configured row, or consuming
        `amount` more would exceed `total_limit` — in both cases no row is
        changed, so this is safe to call speculatively before every
        request.
    """
    result = conn.execute(
        text(
            "UPDATE shared_api_quota "
            "SET total_used = total_used + :amount "
            "WHERE resource_name = :resource_name "
            "AND period_start = :period_start "
            "AND total_used + :amount <= total_limit"
        ),
        {
            "amount": amount,
            "resource_name": resource_name,
            "period_start": period_start,
        },
    )
    return result.rowcount > 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd job_search && docker compose up -d postgres && sleep 3 && (cd db && alembic upgrade head)`
Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_quota -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Write `job_search/docs/tenancy.md`**

```markdown
# Tenancy — the two-zone rule

Authoritative reference for `user_id` scoping decisions. Full reasoning:
`PLAN.md` Step 1a, `DECISIONS.md` §7.

| Zone | Contents | Grain |
|---|---|---|
| **Shared** | Job postings, dedup identity map, `dim_job`, `dim_company`, company intel, all market marts, taxonomy, emergent detection, question text, `shared_api_quota` | collected once, identical for everyone |
| **Per-user** | Truth base, scores, artefacts, applications, review progress, offers, preferences, alerts, `app_user`, `user_quota` | scoped to `user_id` |

## Adding a new per-user table

Every per-user table follows exactly this pattern (see
`db/migrations/versions/0001_create_app_user.py` and `0002_create_quota_tables.py`
for worked examples):

1. `user_id` column: `NOT NULL`, `FOREIGN KEY REFERENCES app_user (id)`.
2. An index leading on `user_id`.
3. `ALTER TABLE <table> ENABLE ROW LEVEL SECURITY`.
4. `CREATE POLICY <table>_isolation ON <table> USING (user_id = current_setting('app.current_user_id', true)::uuid)` —
   the GUC name is fixed project-wide; never invent a different one.
5. `GRANT SELECT, INSERT, UPDATE ON <table> TO job_search_app` (add `DELETE`
   only if the table is genuinely meant to support row deletion by the app
   role).
6. A negative test in `packages/core/tests/integration/`, following the
   pattern in `test_rls_isolation.py`: insert rows for two users as the
   migration role, query with no `WHERE` as the app role scoped to user A,
   assert zero rows belonging to user B.

## Adding a new shared table

No `user_id`, no RLS policy. Note in the table's migration comment *why*
it's shared, so a future edit doesn't add `user_id` by reflex (see the
`fct_market_demand` selection-bias precedent in PLAN.md Step 11).
```

- [ ] **Step 6: Commit**

```bash
cd job_search
git add packages/core/core/db/quota.py packages/core/tests/integration/test_quota.py \
  docs/tenancy.md
git commit -m "feat(job_search): add shared-quota guard and tenancy documentation"
```

---

## Task 16: Full-stack verification and acceptance sign-off

**Files:** none created — this task runs the two stories' literal acceptance criteria end to end and records the result.

**Interfaces:** none — verification only.

- [ ] **Step 1: Tear down and bring the full stack up clean**

Run: `cd job_search && docker compose down -v && docker compose up -d --build`
Expected: `postgres`, `api`, `ui`, `ollama` start (pipeline and n8n stay down — they're `profiles`-gated). Wait for `postgres` healthcheck to pass.

- [ ] **Step 2: Run migrations against the fresh stack**

Run: `cd job_search/db && alembic upgrade head`
Expected: both migrations apply cleanly.

- [ ] **Step 3: Verify Step 1's acceptance criterion**

Run: `curl -s http://localhost:8000/health`
Expected: `{"status":"ok"}`

Run: `curl -sI http://localhost:8501 | head -1`
Expected: `HTTP/1.1 200 OK`

Run: `docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extname FROM pg_extension WHERE extname = 'vector'"`
Expected: one row, `vector`.

- [ ] **Step 4: Verify Step 1a's acceptance criterion**

Run: `cd job_search/packages/core && coverage run -m unittest discover && coverage report -m`
Expected: every test passes, including `tests.integration.test_rls_isolation` (not skipped — Postgres is up). Note the reported coverage percentage in the PR description.

- [ ] **Step 5: Run the full project quality gate**

Run: `cd job_search && python3.11 -m black --check . && python3.11 -m isort --check-only . && python3.11 -m ruff check . && python3.11 -m mypy packages/core/core apps/api/app apps/pipeline/app`
Expected: clean on all four. Fix and re-run if anything fails — do not skip this step.

- [ ] **Step 6: Bring the stack back down**

Run: `cd job_search && docker compose down`

- [ ] **Step 7: Open the PR**

Use the `commit-push-pr` skill (per this session's earlier branch-structure decision: one branch, `feat/JOB-16-repo-scaffold`, covering both `STEP-01`/JOB-16 and `STEP-01A`/JOB-33). Reference both Jira keys in the PR description, and paste the coverage percentage and the acceptance-criteria verification output from Steps 3–5 above.
