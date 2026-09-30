"""One wrapper function per automated pipeline stage — each a faithful
port of its `_cmd_*` counterpart in `apps/pipeline/app/cli.py`: same
underlying core function, same engine choice, same arguments. Ported
rather than imported because `apps/pipeline/app` and `apps/api/app` are
separate top-level packages both named `app` (PLAN.md Step 1's
PYTHONPATH note) -- the CLI module isn't importable from here.

Every function takes a `params: dict` (even when unused, for call-site
uniformity across `core.pipeline.registry.STAGES`) and returns a small
result dict for `pipeline.stage_run.result`. None of these catch
exceptions -- `core.pipeline.runner.run_stage` (Task 6) is what turns
a raised exception into a `failed` row; a wrapper that swallowed its
own errors would hide them from that mechanism.
"""

from __future__ import annotations

import httpx

from core.classification.write_job_category import write_job_category
from core.db.session import build_engine
from core.dedup.write_blocking_keys import write_blocking_keys
from core.dedup.write_job_identity_map import write_job_identity_map
from core.dedup.write_job_survivorship import write_job_survivorship
from core.dedup.write_similarity_features import write_similarity_features
from core.dedup.write_title_similarity_scores import write_title_similarity_scores
from core.enrichment.write_engagement_terms import write_engagement_terms
from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.types import LLMAdapter
from core.settings import get_settings


def _build_llm_adapters(http_client: httpx.Client) -> dict[str, LLMAdapter]:
    """Same shape as apps/pipeline/app/cli.py's own helper of this name
    and apps/api/app/dependencies.get_llm_adapters -- duplicated, not
    shared, for the same PYTHONPATH-separation reason as this module's
    own docstring explains."""
    settings = get_settings()
    adapters: dict[str, LLMAdapter] = {
        "ollama": OllamaAdapter(base_url=settings.ollama_base_url, client=http_client),
    }
    if settings.anthropic_api_key:
        import anthropic

        adapters["anthropic"] = AnthropicAdapter(
            api_key=settings.anthropic_api_key,
            client=anthropic.Anthropic(api_key=settings.anthropic_api_key),
        )
    return adapters


def run_enrich_engagement_terms(params: dict) -> dict:
    """Wraps `_cmd_enrich_engagement_terms` (cli.py:576)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_engagement_terms(engine)}


def run_compute_blocking_keys(params: dict) -> dict:
    """Wraps `_cmd_compute_blocking_keys` (cli.py:592)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_blocking_keys(engine)}


def run_compute_similarity_features(params: dict) -> dict:
    """Wraps `_cmd_compute_similarity_features` (cli.py:608)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_similarity_features(engine)}


def run_compute_title_similarity_scores(params: dict) -> dict:
    """Wraps `_cmd_compute_title_similarity_scores` (cli.py:624)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_title_similarity_scores(engine)}


def run_cluster_jobs(params: dict) -> dict:
    """Wraps `_cmd_cluster_jobs` (cli.py:640)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_job_identity_map(engine)}


def run_compute_survivorship(params: dict) -> dict:
    """Wraps `_cmd_compute_survivorship` (cli.py:656)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_job_survivorship(engine)}


def run_classify_jobs(params: dict) -> dict:
    """Wraps `_cmd_classify_jobs` (cli.py:672). Raises RuntimeError
    (rather than cli.py's print-and-return-1) when no Anthropic key is
    configured -- run_stage (Task 6) turns any raised exception into a
    `failed` row with the exception's message, which is the dashboard
    equivalent of cli.py's stderr message."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "classify-jobs: ANTHROPIC_API_KEY is not configured — the LLM "
            "residual stage cannot run"
        )
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        adapters = _build_llm_adapters(http_client)
        written = write_job_category(engine, adapters=adapters, http_client=http_client)
    finally:
        http_client.close()
    return {"rows_written": written}
