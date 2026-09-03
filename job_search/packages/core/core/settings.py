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
