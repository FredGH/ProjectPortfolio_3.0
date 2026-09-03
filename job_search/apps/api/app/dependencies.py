"""FastAPI dependency providers — the app's collaborator seams."""

from __future__ import annotations

from functools import lru_cache

import httpx
from sqlalchemy import Engine

from core.db.session import build_engine
from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.types import LLMAdapter
from core.settings import get_settings


@lru_cache
def get_app_db_engine() -> Engine:
    """Return the process-wide, RLS-enforced app-role database engine.

    Returns:
        An `Engine` built from `settings.app_database_url`, reused across
        requests. Used for reads that don't need per-user scoping (e.g.
        `GET /sources` against the shared `bronze.raw_jobs` table) — never
        for the migration/owner DSN, which stays out of request-serving
        code entirely.
    """
    return build_engine(get_settings().app_database_url)


@lru_cache
def get_http_client() -> httpx.Client:
    """Return the process-wide HTTP client for outbound requests.

    Returns:
        A shared `httpx.Client`, reused across requests rather than
        rebuilt per call.
    """
    return httpx.Client(timeout=10.0)


@lru_cache
def get_llm_adapters() -> dict[str, LLMAdapter]:
    """Build the process-wide LLM adapter registry.

    Returns:
        A dict keyed by provider name. "ollama" is always present.
        "anthropic" is present only when an API key is configured — a
        task routed to a provider with no adapter here raises `KeyError`
        at call time, per `core.llm.gateway.complete`'s documented
        behaviour.
    """
    settings = get_settings()
    adapters: dict[str, LLMAdapter] = {
        "ollama": OllamaAdapter(
            base_url=settings.ollama_base_url, client=get_http_client()
        ),
    }
    if settings.anthropic_api_key:
        import anthropic

        adapters["anthropic"] = AnthropicAdapter(
            api_key=settings.anthropic_api_key,
            client=anthropic.Anthropic(api_key=settings.anthropic_api_key),
        )
    return adapters
