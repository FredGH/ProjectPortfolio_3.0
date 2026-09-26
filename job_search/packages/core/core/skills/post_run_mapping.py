"""Map newly extracted skill strings to ESCO once an extraction run completes.

This is what the `map-skills` CLI command does, packaged as a callable the API
process can run in-process: extraction only writes raw strings
(`silver.job_skill_raw`); until they are mapped they cannot reach the review
list or the job–skill bridge. Everything it touches — the seed aliases and
`silver.skill_mapping` writes, the ESCO reads — is within `job_search_app`'s
grants, so it runs with the app-role engine.

It does NOT refresh the dbt bridge (`silver__skill`, `silver__bridge_job_skill`):
dbt cannot run inside the API image (dbt-core needs protobuf>=6 while
streamlit needs <6 — see requirements-dbt.txt), so that step stays a
developer-machine command.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from sqlalchemy import Engine

from core.embedding.ollama import embed_text
from core.llm.types import LLMAdapter
from core.skills.aliases import sync_seed_aliases
from core.skills.llm_map import propose_matches
from core.skills.mapper import map_pending


def build_post_run_mapping(
    engine: Engine,
    *,
    ollama_base_url: str,
    embedding_model: str,
    http_client: httpx.Client,
    raw_norms: list[str] | None = None,
    llm_adapters: dict[str, LLMAdapter] | None = None,
    llm_limit: int = 300,
) -> Callable[[], str]:
    """Build a zero-argument function that maps every still-unmapped string.

    Args:
        engine: The app-role engine.
        ollama_base_url: The Ollama server to embed with — the same location
            the run used, so a run on native Ollama does not also need the
            Docker one.
        embedding_model: The embedding model in use; `map_pending` refuses to
            run (`EmbeddingModelMismatch`) if the stored ESCO vectors came
            from a different one.
        http_client: The client for the embedding calls.
        raw_norms: Restrict to these normalised strings; `None` covers every
            unmapped string. Exists so tests never touch unrelated rows in
            the shared dev DB.
        llm_adapters: Adapters for the Claude pre-review that runs after the
            embedding mapping. `None` switches the pre-review off (summary is
            unchanged); a dict without an `anthropic` entry reports it as
            skipped for want of a key.
        llm_limit: Most unmapped strings the pre-review looks at per run.

    Returns:
        A function that syncs the seed aliases, maps the pending strings,
        optionally runs the Claude pre-review, and returns a summary for the
        run row. It raises on embedding failure (server down, model mismatch)
        — the caller decides what that means for the run — but never on a
        pre-review failure, which is reported in the summary instead.
    """

    def embed(text: str) -> list[float]:
        return embed_text(
            text,
            base_url=ollama_base_url,
            model=embedding_model,
            client=http_client,
        )

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
        except Exception as exc:  # noqa: BLE001 - never fail the run over this
            return f"{message} Claude pre-review failed: {exc}."
        tail = (
            f", {reviewed.failed} not answered (retried next run)."
            if reviewed.failed
            else "."
        )
        return (
            f"{message} Claude pre-review: {reviewed.checked} checked, "
            f"{reviewed.applied} applied, {reviewed.left_open} left for review{tail}"
        )

    return run
