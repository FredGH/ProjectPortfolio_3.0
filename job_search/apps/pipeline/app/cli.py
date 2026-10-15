"""Pipeline batch entrypoint (PLAN.md Steps 1 and 3).

The `ingest` subcommand runs one connector through the shared runner —
adding a new connector means adding one entry to `_CONNECTOR_BUILDERS`
below and one matching entry to `_QUERY_BUILDERS`, plus one
config/sources.yml block, never touching run_connector itself.
`_KNOWN_SOURCES` is derived from `_CONNECTOR_BUILDERS`, and an assertion
at import time guarantees `_QUERY_BUILDERS` registers exactly the same
set of sources, so the two registries can never silently drift apart.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from core.artefacts.render import RenderError as ArtefactRenderError
from core.artefacts.render import render_cover_letter_files, render_pitch_file
from core.artefacts.schema import CoverLetter, Pitch
from core.artefacts.service import NoCvError, NoTargetTitleError, generate_artefact
from core.artefacts.store import read_latest as read_latest_artefact
from core.classification.write_job_category import write_job_category
from core.company_intel.angle import get_or_create_angle
from core.company_intel.config import load_config as load_company_intel_config
from core.company_intel.fetch import CachedFetcher, Crawl4AIFetcher
from core.company_intel.resolve import ManualOverride, normalise_website_url
from core.company_intel.service import get_or_refresh_intel
from core.db.session import build_engine
from core.dedup.write_blocking_keys import write_blocking_keys
from core.dedup.write_job_identity_map import write_job_identity_map
from core.dedup.write_job_survivorship import write_job_survivorship
from core.dedup.write_similarity_features import write_similarity_features
from core.dedup.write_title_similarity_scores import write_title_similarity_scores
from core.embedding.ollama import embed_text
from core.enrichment.write_engagement_terms import write_engagement_terms
from core.evals.runner import EvalRunResult, run_eval
from core.ingestion.adzuna_connector import AdzunaConnector, AdzunaQuery
from core.ingestion.connector import Connector
from core.ingestion.greenhouse_connector import GreenhouseConnector, GreenhouseQuery
from core.ingestion.jooble_connector import JoobleConnector, JoobleQuery
from core.ingestion.manual_connector import ManualConnector, ManualJobQuery
from core.ingestion.rate_limiter import TokenBucket
from core.ingestion.reed_connector import ReedConnector, ReedQuery
from core.ingestion.runner import run_connector
from core.ingestion.sources_config import load_sources_config
from core.interview import practice as interview_practice
from core.interview import service as interview_service
from core.interview import store as interview_store
from core.interview.anki import export_anki
from core.interview.progress import list_progress as list_interview_progress
from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.types import LLMAdapter
from core.render.layout import (
    JOB_SPEC_FILENAME,
    cv_directory,
    intel_ui_link,
    job_ui_link,
    render_job_spec,
)
from core.render.service import RenderError, render_cv_files
from core.scoring.blend import compute_final_scores
from core.scoring.cv_chunking import chunk_and_embed_cv
from core.scoring.hard_filters import run_hard_filters
from core.scoring.job_chunking import chunk_and_embed_jobs
from core.scoring.llm_rerank import run_llm_rerank
from core.scoring.similarity import run_similarity
from core.scoring.skill_coverage import run_skill_coverage
from core.settings import Settings, get_settings
from core.skills.aliases import sync_seed_aliases
from core.skills.cv_map import map_cv_skills
from core.skills.esco_embed import (
    embed_esco_skills,
    embedding_coverage,
    embedding_coverage_warning,
)
from core.skills.esco_load import EscoLoadCounts, EscoLoadError, load_esco
from core.skills.llm_map import (
    MIN_HIGH_AGREEMENT,
    EvalReport,
    count_eligible,
    evaluate_against_resolved,
    propose_matches,
)
from core.skills.mapper import (
    EmbeddingModelMismatch,
    map_pending,
    remap_all_auto,
    remap_unresolved,
)
from core.skills.write_job_skills import (
    WriteSummary,
    count_pending_jobs,
    write_job_skills,
)
from core.tailoring.backends import resolve_backends
from core.tailoring.context import load_job_context, load_job_source
from core.tailoring.loop import TailoringError, run_tailoring
from core.tailoring.store import latest_run_id, read_run


def _build_llm_adapters(http_client: httpx.Client) -> dict[str, LLMAdapter]:
    """Build the LLM adapter registry for CLI-driven connectors.

    Args:
        http_client: The shared HTTP client, reused for the Ollama adapter.

    Returns:
        A dict keyed by provider name, matching `apps/api/app/dependencies.
        get_llm_adapters`'s shape (duplicated rather than shared, since
        `apps/api/app` and `apps/pipeline/app` are separate top-level
        packages both named `app` — see PLAN.md Step 1's PYTHONPATH note).
    """
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


def _extract_title(payload: dict[str, object]) -> str | None:
    """Best-effort title extraction across every connector's raw payload shape.

    Args:
        payload: One `RawJob.payload` dict — connector-specific, never
            normalised at this layer (normalisation is a later dbt/staging
            concern per PLAN.md Phase 1).

    Returns:
        The title string if a recognisable field is present (Adzuna/
        Greenhouse/Jooble all use "title"; Reed uses "jobTitle"), else
        `None`.
    """
    title = payload.get("title") or payload.get("jobTitle")
    return str(title) if title else None


@dataclass(frozen=True)
class _ConnectorBuildContext:
    """Everything a connector builder might need to construct its connector.

    Attributes:
        http_client: The shared HTTP client.
        llm_adapters: Every available LLM adapter, keyed by provider.
        settings: The process-wide Settings instance.
    """

    http_client: httpx.Client
    llm_adapters: dict[str, LLMAdapter]
    settings: Settings


def _build_manual_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the manual-entry connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        A `ManualConnector` instance.
    """
    # mypy correctly flags this: Connector.fetch(query: object, ...) is a
    # Protocol, and ManualConnector.fetch(query: ManualJobQuery, ...) narrows
    # that parameter, which isn't Liskov-substitutable in general. In
    # practice it's sound: run_connector always pairs a connector_key with
    # the one query type that connector's own query-builder produces (see
    # _QUERY_BUILDERS), so a mismatched query never reaches fetch() — the
    # same out-of-band-knowledge situation documented via typing.cast()
    # elsewhere in this codebase (core/ingestion/manual.py).
    return ManualConnector(  # type: ignore[return-value]
        http_client=ctx.http_client, llm_adapters=ctx.llm_adapters
    )


def _build_adzuna_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the Adzuna connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        An `AdzunaConnector` instance.

    Raises:
        ValueError: If `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` aren't configured.
    """
    if not ctx.settings.adzuna_app_id or not ctx.settings.adzuna_app_key:
        raise ValueError(
            "source=adzuna requires ADZUNA_APP_ID and ADZUNA_APP_KEY to be "
            "set in .env"
        )
    # See _build_manual_connector's comment above — same sound-but-narrower
    # query-type situation.
    return AdzunaConnector(  # type: ignore[return-value]
        http_client=ctx.http_client,
        app_id=ctx.settings.adzuna_app_id,
        app_key=ctx.settings.adzuna_app_key,
    )


def _build_greenhouse_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the Greenhouse connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        A `GreenhouseConnector` instance.
    """
    # See _build_manual_connector's comment above — same sound-but-narrower
    # query-type situation.
    return GreenhouseConnector(  # type: ignore[return-value]
        http_client=ctx.http_client, database_url=ctx.settings.database_url
    )


def _build_reed_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the Reed connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        A `ReedConnector` instance.

    Raises:
        ValueError: If `REED_API_KEY` isn't configured.
    """
    if not ctx.settings.reed_api_key:
        raise ValueError("source=reed requires REED_API_KEY to be set in .env")
    # See _build_manual_connector's comment above — same sound-but-narrower
    # query-type situation.
    return ReedConnector(  # type: ignore[return-value]
        http_client=ctx.http_client, api_key=ctx.settings.reed_api_key
    )


def _build_jooble_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the Jooble connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        A `JoobleConnector` instance.

    Raises:
        ValueError: If `JOOBLE_KEY` isn't configured.
    """
    if not ctx.settings.jooble_key:
        raise ValueError("source=jooble requires JOOBLE_KEY to be set in .env")
    # See _build_manual_connector's comment above — same sound-but-narrower
    # query-type situation.
    return JoobleConnector(  # type: ignore[return-value]
        http_client=ctx.http_client, api_key=ctx.settings.jooble_key
    )


_CONNECTOR_BUILDERS: dict[str, Callable[[_ConnectorBuildContext], Connector]] = {
    "manual": _build_manual_connector,
    "adzuna": _build_adzuna_connector,
    "reed": _build_reed_connector,
    "greenhouse": _build_greenhouse_connector,
    "jooble": _build_jooble_connector,
}

_KNOWN_SOURCES = frozenset(_CONNECTOR_BUILDERS)
"""Every `--source` name the CLI recognises — derived from
`_CONNECTOR_BUILDERS` so the two can never drift apart. Adding a new
connector is one new entry in `_CONNECTOR_BUILDERS`, nothing else, and
`_KNOWN_SOURCES` picks it up automatically."""

_DISCOVERY_CAPABLE_SOURCES = frozenset({"adzuna", "greenhouse"})
"""Sources whose query-builder actually changes behaviour under
`--collection-channel discovery` — Adzuna's category sweep, and
Greenhouse's native full-board dump. Every other source's query-builder
ignores `collection_channel` entirely, so accepting `discovery` for them
would silently mislabel targeted-mode records as discovery in bronze."""


def _make_factory(
    builder: Callable[[_ConnectorBuildContext], Connector],
    ctx: _ConnectorBuildContext,
) -> Callable[[], Connector]:
    """Bind a connector builder's context into a zero-argument factory.

    Args:
        builder: One `_CONNECTOR_BUILDERS` entry.
        ctx: The shared connector-build context to bind in.

    Returns:
        A zero-argument callable that builds the connector.
    """
    return lambda: builder(ctx)


def _build_connector_factories(
    http_client: httpx.Client,
) -> dict[str, Callable[[], Connector]]:
    """Build the CLI's connector registry.

    Args:
        http_client: The shared HTTP client passed to any connector that
            needs one.

    Returns:
        A mapping of `--source` name to a zero-argument factory building
        that connector. Callers only invoke this after confirming
        `args.source in _KNOWN_SOURCES` — a builder may still raise
        ValueError for a known-but-misconfigured source (e.g. Adzuna with
        no API key), which `_cmd_ingest` catches.
    """
    ctx = _ConnectorBuildContext(
        http_client=http_client,
        llm_adapters=_build_llm_adapters(http_client),
        settings=get_settings(),
    )
    return {
        name: _make_factory(builder, ctx)
        for name, builder in _CONNECTOR_BUILDERS.items()
    }


def _build_manual_query(
    raw_query: str, region: str | None, collection_channel: str
) -> ManualJobQuery:
    """Parse `--query`'s JSON string into a ManualJobQuery.

    Args:
        raw_query: The `--query` argument's raw string value.
        region: Unused — manual entry has no region concept. Present only
            so this builder's signature matches every `_QUERY_BUILDERS`
            entry's uniform `(raw_query, region, collection_channel)`
            shape.
        collection_channel: Unused — manual entry has no discovery-mode
            concept. Present only so this builder's signature matches
            every `_QUERY_BUILDERS` entry's uniform
            `(raw_query, region, collection_channel)` shape.

    Returns:
        The parsed `ManualJobQuery`.

    Raises:
        ValueError: If `raw_query` isn't valid JSON, or is missing a
            required field.
    """
    try:
        data = json.loads(raw_query)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"--query must be valid JSON for source=manual: {exc}"
        ) from exc
    try:
        source_name = data["source_name"]
        job_url = data["job_url"]
        job_spec = data["job_spec"]
    except KeyError as exc:
        raise ValueError(
            f"--query is missing required field {exc} for source=manual"
        ) from exc

    posted_date = data.get("posted_date")
    return ManualJobQuery(
        source_name=source_name,
        job_url=job_url,
        job_spec=job_spec,
        posted_date=datetime.date.fromisoformat(posted_date) if posted_date else None,
        company=data.get("company"),
        title=data.get("title"),
        location=data.get("location"),
        notes=data.get("notes"),
    )


def _build_adzuna_query(
    raw_query: str, region: str | None, collection_channel: str
) -> AdzunaQuery:
    """Build an AdzunaQuery from --query, --region, and --collection-channel.

    Args:
        raw_query: The `--query` argument's raw string value — keywords
            in targeted mode, an Adzuna category tag in discovery mode
            (see `collection_channel`).
        region: The `--region` argument's raw string value.
        collection_channel: "targeted" or "discovery". In discovery mode,
            `raw_query` is interpreted as a category tag (e.g.
            "it-jobs") instead of free-text keywords, and pagination is
            capped lower (2 pages instead of the default 5) — a category
            sweep returns far more results per page than a keyword
            search, so the existing per-page cap alone isn't a
            meaningful volume limit for this mode.

    Returns:
        The `AdzunaQuery`.

    Raises:
        ValueError: If `region` is not given, or if `raw_query` is empty
            in targeted mode (a category tag is required in discovery
            mode instead, and empty is valid there via `--query ""`... but
            for clarity this implementation still requires a non-empty
            `raw_query` in BOTH modes — an empty string is never a
            meaningful category tag either).
    """
    if not region:
        raise ValueError("--region is required for source=adzuna")
    if not raw_query:
        raise ValueError(
            "--query is required for source=adzuna (keywords in targeted "
            "mode, a category tag like 'it-jobs' in discovery mode)"
        )
    if collection_channel == "discovery":
        return AdzunaQuery(keywords="", category=raw_query, country=region, max_pages=2)
    return AdzunaQuery(keywords=raw_query, country=region)


def _build_reed_query(
    raw_query: str, region: str | None, collection_channel: str
) -> ReedQuery:
    """Build a ReedQuery from --query and --region.

    Args:
        raw_query: The `--query` argument's raw string value (keywords).
        region: The `--region` argument's raw string value, used as an
            optional UK location filter — unlike Adzuna, Reed doesn't
            require one (it's UK-only already).
        collection_channel: Unused — Reed has no discovery-mode concept
            yet. Present only so this builder's signature matches every
            `_QUERY_BUILDERS` entry's uniform
            `(raw_query, region, collection_channel)` shape.

    Returns:
        The `ReedQuery`.
    """
    return ReedQuery(keywords=raw_query, location=region)


def _build_greenhouse_query(
    raw_query: str, region: str | None, collection_channel: str
) -> GreenhouseQuery:
    """Build a GreenhouseQuery from --query.

    Args:
        raw_query: The `--query` argument's raw string value — a
            comma-separated list of board slugs, or empty to use the
            active target_company registry.
        region: Unused — Greenhouse boards aren't region-scoped. Present
            only so this builder's signature matches every
            `_QUERY_BUILDERS` entry's uniform
            `(raw_query, region, collection_channel)` shape.
        collection_channel: Unused — Greenhouse has no discovery-mode
            concept yet. Present only so this builder's signature matches
            every `_QUERY_BUILDERS` entry's uniform
            `(raw_query, region, collection_channel)` shape.

    Returns:
        The `GreenhouseQuery`.
    """
    slugs = [s.strip() for s in raw_query.split(",") if s.strip()]
    return GreenhouseQuery(board_slugs=slugs or None)


def _build_jooble_query(
    raw_query: str, region: str | None, collection_channel: str
) -> JoobleQuery:
    """Build a JoobleQuery from --query and --region.

    Args:
        raw_query: The `--query` argument's raw string value (keywords).
        region: The `--region` argument's raw string value, used as an
            optional location filter.
        collection_channel: Unused — Jooble has no discovery-mode concept
            yet. Present only so this builder's signature matches every
            `_QUERY_BUILDERS` entry's uniform
            `(raw_query, region, collection_channel)` shape.

    Returns:
        The `JoobleQuery`.
    """
    return JoobleQuery(keywords=raw_query, location=region)


_QUERY_BUILDERS: dict[str, Callable[[str, str | None, str], object]] = {
    "manual": _build_manual_query,
    "adzuna": _build_adzuna_query,
    "reed": _build_reed_query,
    "greenhouse": _build_greenhouse_query,
    "jooble": _build_jooble_query,
}

assert (
    _QUERY_BUILDERS.keys() == _CONNECTOR_BUILDERS.keys()
), "_QUERY_BUILDERS and _CONNECTOR_BUILDERS must register the same sources"


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Run the `ingest` subcommand.

    Args:
        args: Parsed CLI arguments — `source`, `query`, `since`, `region`,
            `collection_channel`.

    Returns:
        0 on success, 1 on a reported error.
    """
    if args.source not in _KNOWN_SOURCES:
        print(
            f"Unknown --source {args.source!r}. Known sources: "
            f"{sorted(_KNOWN_SOURCES)}",
            file=sys.stderr,
        )
        return 1

    if (
        args.collection_channel == "discovery"
        and args.source not in _DISCOVERY_CAPABLE_SOURCES
    ):
        print(
            f"--collection-channel discovery has no effect for source="
            f"{args.source!r} (only {sorted(_DISCOVERY_CAPABLE_SOURCES)} "
            "currently support it) — refusing to silently mislabel "
            "targeted-mode records",
            file=sys.stderr,
        )
        return 1

    try:
        query = _QUERY_BUILDERS[args.source](
            args.query, args.region, args.collection_channel
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    since = datetime.datetime.fromisoformat(args.since) if args.since else None
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=datetime.UTC)

    http_client = httpx.Client(timeout=10.0)
    try:
        factories = _build_connector_factories(http_client)
        sources_config = load_sources_config()
        source_config = sources_config.get(args.source)
        rate_limiter = None
        if source_config is not None and source_config.calls_per_hour:
            rate_limiter = TokenBucket(
                capacity=source_config.calls_per_hour, refill_period_seconds=3600.0
            )

        settings = get_settings()
        try:
            connector = factories[args.source]()
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        result = run_connector(
            connector_key=args.source,
            connector=connector,
            query=query,
            since=since,
            entry_method="manual" if args.source == "manual" else "api",
            collection_channel=args.collection_channel,
            landing_uri=settings.landing_uri,
            database_url=settings.database_url,
            rate_limiter=rate_limiter,
        )
        if args.collection_channel == "discovery":
            titles = {
                title
                for raw_job in result.raw_jobs
                if (title := _extract_title(raw_job.payload)) is not None
            }
            print(
                f"discovery yield: {len(result.raw_jobs)} records, "
                f"{len(titles)} distinct titles"
            )

        print(
            f"ingest complete: source={args.source} "
            f"records={result.run_metadata.records} "
            f"run_id={result.run_metadata.run_id}"
        )
        return 0
    finally:
        http_client.close()


def _cmd_enrich_engagement_terms(args: argparse.Namespace) -> int:
    """Run the `enrich-engagement-terms` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_engagement_terms(engine)
    print(f"enrich-engagement-terms complete: rows_written={written}")
    return 0


def _cmd_compute_blocking_keys(args: argparse.Namespace) -> int:
    """Run the `compute-blocking-keys` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_blocking_keys(engine)
    print(f"compute-blocking-keys complete: rows_written={written}")
    return 0


def _cmd_compute_similarity_features(args: argparse.Namespace) -> int:
    """Run the `compute-similarity-features` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_similarity_features(engine)
    print(f"compute-similarity-features complete: rows_written={written}")
    return 0


def _cmd_compute_title_similarity_scores(args: argparse.Namespace) -> int:
    """Run the `compute-title-similarity-scores` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_title_similarity_scores(engine)
    print(f"compute-title-similarity-scores complete: rows_written={written}")
    return 0


def _cmd_cluster_jobs(args: argparse.Namespace) -> int:
    """Run the `cluster-jobs` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_job_identity_map(engine)
    print(f"cluster-jobs complete: rows_written={written}")
    return 0


def _cmd_compute_survivorship(args: argparse.Namespace) -> int:
    """Run the `compute-survivorship` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_job_survivorship(engine)
    print(f"compute-survivorship complete: rows_written={written}")
    return 0


def _cmd_classify_jobs(args: argparse.Namespace) -> int:
    """Run the `classify-jobs` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        adapters = _build_llm_adapters(http_client)
        if "anthropic" not in adapters:
            print(
                "classify-jobs: ANTHROPIC_API_KEY is not configured — the LLM "
                "residual stage cannot run. Set it before retrying; failing "
                "here up front avoids burning the rules/embedding cascade "
                "against the whole corpus only to error on the first "
                "residual title."
            )
            return 1
        written = write_job_category(engine, adapters=adapters, http_client=http_client)
        print(f"classify-jobs complete: rows_written={written}")
        return 0
    finally:
        http_client.close()


def _esco_duplicates_note(counts: EscoLoadCounts) -> str | None:
    """Describe skill concept ids the release file repeats, if any.

    Args:
        counts: The result of `load_esco`.

    Returns:
        A one-line note saying how many ids repeat, how many of those differ
        in content, which row was kept and (up to five) which ids; None if
        nothing repeats.
    """
    duplicates = counts.duplicate_skill_ids
    if not duplicates:
        return None
    shown = ", ".join(duplicates[:5]) + (", …" if len(duplicates) > 5 else "")
    return (
        f"load-esco: {len(duplicates)} skill concept id(s) appear on more than "
        f"one row of skills_en.csv ({len(counts.differing_duplicate_skill_ids)} "
        f"with differing content); the last row was kept for each skill record "
        f"and the labels of every row were merged — {shown}"
    )


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
    note = _esco_duplicates_note(counts)
    if note:
        print(note)
    return 0


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


def _cmd_map_skills(args: argparse.Namespace) -> int:
    """Run the `map-skills` subcommand.

    Args:
        args: Parsed CLI arguments — `remap_unresolved`, `remap_all_auto`.

    Returns:
        0 on success, 1 if the stored ESCO embeddings are from a different
        model than the configured one.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        synced = sync_seed_aliases(engine)
        with engine.connect() as conn:
            warning = embedding_coverage_warning(
                *embedding_coverage(conn, settings.embedding_model)
            )
        if warning:
            print(f"map-skills: warning: {warning}")
        if args.remap_all_auto:
            cleared = remap_all_auto(engine)
            print(f"map-skills: cleared {cleared} auto-made mappings for re-mapping")
        elif args.remap_unresolved:
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


def _extraction_exit_code(summary: WriteSummary) -> int:
    """Decide the exit status of `extract-job-skills` from its summary.

    A run where jobs failed and none succeeded is an error (a dead LLM
    provider, say) and must not look like a success to a scheduler. A run
    where some succeeded exits 0: the failed jobs are retried on the next run
    and the failed count is printed.

    Args:
        summary: What `write_job_skills` reported.

    Returns:
        1 if every attempted job failed, otherwise 0.
    """
    if summary.failed_jobs > 0 and summary.extracted_jobs == 0:
        print(
            f"extract-job-skills: all {summary.failed_jobs} attempted job(s) "
            f"failed and nothing was extracted — check the LLM provider"
        )
        return 1
    return 0


# Haiku 4.5 list price, USD per million tokens — an estimate for the printout
# only; check the current price list before relying on it.
_HAIKU_INPUT_USD_PER_MTOK = 1.0
_HAIKU_OUTPUT_USD_PER_MTOK = 5.0
_EST_INPUT_TOKENS_PER_STRING = 250
_EST_OUTPUT_TOKENS_PER_STRING = 60


def _usd(input_tokens: int, output_tokens: int) -> float:
    """Estimate the cost of a token count at Haiku 4.5 list prices.

    Args:
        input_tokens: Prompt tokens.
        output_tokens: Completion tokens.

    Returns:
        Estimated USD.
    """
    return (
        input_tokens * _HAIKU_INPUT_USD_PER_MTOK
        + output_tokens * _HAIKU_OUTPUT_USD_PER_MTOK
    ) / 1_000_000


def _cmd_llm_map_skills(args: argparse.Namespace) -> int:
    """Run the `llm-map-skills` subcommand.

    Args:
        args: Parsed CLI arguments — `limit`, `dry_run`, `evaluate`, `sample`.

    Returns:
        0 on success; 1 if there is no Anthropic key, the embedding server is
        unavailable, or `--evaluate` finds agreement below the safe threshold.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("llm-map-skills: ANTHROPIC_API_KEY is not set")
        return 1
    engine = build_engine(settings.database_url)
    if args.dry_run:
        eligible = count_eligible(engine)
        sent = min(eligible, args.limit) if args.limit else eligible
        cost = _usd(
            sent * _EST_INPUT_TOKENS_PER_STRING, sent * _EST_OUTPUT_TOKENS_PER_STRING
        )
        print(f"llm-map-skills: {eligible} eligible; would send {sent} (~${cost:.2f})")
        return 0
    http_client = httpx.Client(timeout=120.0)
    try:
        adapters = _build_llm_adapters(http_client)
        embed = _build_embedder(http_client, settings)
        if args.evaluate:
            return _print_evaluation(
                evaluate_against_resolved(
                    engine,
                    adapters=adapters,
                    embed=embed,
                    embedding_model=settings.embedding_model,
                    sample=args.sample,
                )
            )
        summary = propose_matches(
            engine,
            adapters=adapters,
            embed=embed,
            embedding_model=settings.embedding_model,
            limit=args.limit,
        )
    except httpx.HTTPError as exc:
        print(f"llm-map-skills: embedding server unavailable ({exc})")
        return 1
    finally:
        http_client.close()
    print(
        f"llm-map-skills complete: checked={summary.checked} "
        f"applied={summary.applied} custom_created={summary.custom_created} "
        f"left_open={summary.left_open} failed={summary.failed} "
        f"cost~${_usd(summary.input_tokens, summary.output_tokens):.2f}"
    )
    return 0


def _print_evaluation(report: EvalReport) -> int:
    """Print an `--evaluate` report and decide the exit status.

    Args:
        report: What `evaluate_against_resolved` measured.

    Returns:
        0 if high-confidence agreement meets `MIN_HIGH_AGREEMENT`, else 1.
    """
    print(
        f"llm-map-skills --evaluate: sampled={report.sampled} "
        f"answered={report.answered} high={report.high_matches} "
        f"high_agree={report.high_agree} low={report.low_matches} "
        f"no_equivalent={report.no_equivalent} unsure={report.unsure} "
        f"truth_in_candidates={report.truth_in_candidates} "
        f"cost~${_usd(report.input_tokens, report.output_tokens):.2f}"
    )
    if report.answered == 0 and report.sampled > 0:
        print(
            f"llm-map-skills: the model answered none of {report.sampled} "
            "sampled strings — check the API key / connectivity"
        )
        return 1
    if report.agreement is None:
        print("llm-map-skills: no high-confidence matches to judge")
        return 1
    print(
        f"llm-map-skills: high-confidence agreement {report.agreement:.0%} "
        f"(need >= {MIN_HIGH_AGREEMENT:.0%})"
    )
    return 0 if report.agreement >= MIN_HIGH_AGREEMENT else 1


def _cmd_extract_job_skills(args: argparse.Namespace) -> int:
    """Run the `extract-job-skills` subcommand.

    Args:
        args: Parsed CLI arguments — optional `limit`, `source`, `category`
            and `country`.

    Returns:
        0 on success (including a partial failure), 1 if every attempted job
        failed.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    pending = count_pending_jobs(
        engine,
        sources=args.source,
        categories=args.category,
        countries=args.country,
    )
    scope = " ".join(
        part
        for part in (
            f"sources={','.join(args.source)}" if args.source else "",
            f"categories={','.join(args.category)}" if args.category else "",
            f"countries={','.join(args.country)}" if args.country else "",
            f"limit={args.limit}" if args.limit is not None else "",
        )
        if part
    )
    print(
        f"extract-job-skills: {pending} pending job(s) match"
        + (f" ({scope})" if scope else "")
    )
    # A local 8B model generating on CPU routinely takes far longer than the
    # 30s used elsewhere here (see apps/api/app/dependencies.py's
    # get_ollama_http_client for the measured numbers).
    http_client = httpx.Client(timeout=2000.0)
    try:
        adapters = _build_llm_adapters(http_client)
        summary = write_job_skills(
            engine,
            adapters=adapters,
            limit=args.limit,
            sources=args.source,
            categories=args.category,
            countries=args.country,
        )
    finally:
        http_client.close()
    print(
        f"extract-job-skills complete: extracted_jobs={summary.extracted_jobs} "
        f"skill_rows={summary.skill_rows} failed_jobs={summary.failed_jobs}"
    )
    return _extraction_exit_code(summary)


def _cmd_map_cv_skills(args: argparse.Namespace) -> int:
    """Run the `map-cv-skills` subcommand.

    Args:
        args: Parsed CLI arguments — `user_id` and `refresh`.

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
            refresh=args.refresh,
        )
    except (LookupError, EmbeddingModelMismatch) as exc:
        print(f"map-cv-skills: {exc}")
        return 1
    finally:
        http_client.close()
    version = result.new_version if result.new_version is not None else "unchanged"
    print(
        f"map-cv-skills complete: mapped={result.mapped} "
        f"unmapped={result.unmapped} changed={result.changed} "
        f"truth_base_version={version}"
    )
    return 0


def _cmd_score_filter_jobs(args: argparse.Namespace) -> int:
    """Run the `score-filter-jobs` subcommand.

    Args:
        args: Parsed CLI arguments — `user_id` and `limit`.

    Returns:
        0 on success.
    """
    settings = get_settings()
    app_engine = build_engine(settings.app_database_url)
    n = run_hard_filters(app_engine, args.user_id, limit=args.limit)
    print(f"score-filter-jobs complete: considered={n}")
    return 0


def _cmd_chunk_embed_jobs(args: argparse.Namespace) -> int:
    """Run the `chunk-embed-jobs` subcommand.

    Args:
        args: Parsed CLI arguments — `limit`.

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        n = chunk_and_embed_jobs(
            engine,
            embed=_build_embedder(http_client, settings),
            embedding_model=settings.embedding_model,
            limit=args.limit,
        )
    finally:
        http_client.close()
    print(f"chunk-embed-jobs complete: jobs_chunked={n}")
    return 0


def _cmd_chunk_embed_cv(args: argparse.Namespace) -> int:
    """Run the `chunk-embed-cv` subcommand.

    Args:
        args: Parsed CLI arguments — `user_id` and `refresh`.

    Returns:
        0 on success, 1 if the user has no CV truth base.
    """
    settings = get_settings()
    app_engine = build_engine(settings.app_database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        n = chunk_and_embed_cv(
            app_engine,
            args.user_id,
            embed=_build_embedder(http_client, settings),
            embedding_model=settings.embedding_model,
            refresh=args.refresh,
        )
    except LookupError as exc:
        print(f"chunk-embed-cv: {exc}")
        return 1
    finally:
        http_client.close()
    print(f"chunk-embed-cv complete: chunks_written={n}")
    return 0


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


def _cmd_score_similarity(args: argparse.Namespace) -> int:
    """Run the `score-similarity` subcommand.

    Args:
        args: Parsed CLI arguments — `user_id` and `top_n`.

    Returns:
        0 on success.
    """
    settings = get_settings()
    app_engine = build_engine(settings.app_database_url)
    summary = run_similarity(
        app_engine, args.user_id, rerank=_build_reranker(), top_n=args.top_n
    )
    print(
        f"score-similarity complete: jobs_scored={summary.jobs_scored} "
        f"mismatched={summary.mismatched}"
    )
    return 0


def _cmd_score_skill_coverage(args: argparse.Namespace) -> int:
    """Run the `score-skill-coverage` subcommand.

    Args:
        args: Parsed CLI arguments — `user_id`.

    Returns:
        0 on success, 1 if the user has no CV truth base.
    """
    settings = get_settings()
    app_engine = build_engine(settings.app_database_url)
    try:
        n = run_skill_coverage(app_engine, args.user_id)
    except LookupError as exc:
        print(f"score-skill-coverage: {exc}")
        return 1
    print(f"score-skill-coverage complete: jobs_scored={n}")
    return 0


def _cmd_score_llm_rerank(args: argparse.Namespace) -> int:
    """Run the `score-llm-rerank` subcommand.

    Args:
        args: Parsed CLI arguments — `user_id` and `top_n`.

    Returns:
        0 on success, 1 if there is no Anthropic key configured.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("score-llm-rerank: ANTHROPIC_API_KEY is not set")
        return 1
    app_engine = build_engine(settings.app_database_url)
    http_client = httpx.Client(timeout=120.0)
    try:
        adapters = _build_llm_adapters(http_client)
        summary = run_llm_rerank(
            app_engine, args.user_id, adapters=adapters, top_n=args.top_n
        )
    finally:
        http_client.close()
    print(
        f"score-llm-rerank complete: jobs_checked={summary.checked} "
        f"jobs_reranked={summary.reranked}"
    )
    return 0


def _cmd_score_blend(args: argparse.Namespace) -> int:
    """Run the `score-blend` subcommand.

    Args:
        args: Parsed CLI arguments — `user_id`.

    Returns:
        0 on success.
    """
    settings = get_settings()
    app_engine = build_engine(settings.app_database_url)
    n = compute_final_scores(app_engine, args.user_id)
    print(f"score-blend complete: jobs_blended={n}")
    return 0


def _usage_suffix(usage: object) -> str:
    """Describe a run's token use for the `tailor-cv` completion line.

    Args:
        usage: The run's `usage` (see `StoredRun.usage`); anything else
            (None, a malformed value) yields no text.

    Returns:
        ` tokens_in=… tokens_out=…` plus ` est_cost_usd=…` when every call
        was priced, or an empty string when there is nothing usable.
    """
    if not isinstance(usage, dict):
        return ""
    tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
    if not isinstance(tokens_in, int) or not isinstance(tokens_out, int):
        return ""
    text = f" tokens_in={tokens_in} tokens_out={tokens_out}"
    cost = usage.get("cost_usd")
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        text += f" est_cost_usd={cost:.4f}"
    return text


def _cmd_tailor_cv(args: argparse.Namespace) -> int:
    """Run the `tailor-cv` subcommand: tailor one user's CV to one job.

    On demand, not a batch stage, so it is deliberately absent from the
    pipeline dashboard (core.pipeline.registry).

    Args:
        args: Parsed CLI arguments — `user_id`, `job_group_id` and the
            optional `backend` (`claude`, `native` or `docker`). An
            unavailable backend is not pre-checked: a failing Ollama call
            just fails the run.

    Prints the run's error message when it failed, and the number of lines
    awaiting a decision when it needs review.

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
            app_engine,
            args.user_id,
            args.job_group_id,
            adapters=adapters,
            backend=args.backend,
        )
    except TailoringError as exc:
        print(f"tailor-cv: {exc}")
        return 1
    finally:
        http_client.close()
    run = read_run(app_engine, args.user_id, outcome.run_id)
    backends = resolve_backends()
    backend = backends.get(getattr(run, "tailor_backend", None) or args.backend or "")
    print(
        f"tailor-cv complete: run_id={outcome.run_id} status={outcome.status} "
        f"attempts={outcome.attempts}"
        + (f" backend={backend.label}" if backend is not None else "")
        + _usage_suffix(getattr(run, "usage", None))
    )
    if run is not None and outcome.status == "failed" and run.error_message:
        print(f"tailor-cv error: {run.error_message}")
    if run is not None and outcome.status == "needs_review":
        pending = sum(1 for orphan in run.orphans if orphan.status == "pending")
        print(f"tailor-cv needs_review: pending_orphans={pending}")
    return 1 if outcome.status == "failed" else 0


def _cmd_render_cv(args: argparse.Namespace) -> int:
    """Run the `render-cv` subcommand: write the ATS .docx, .txt and designed
    PDF for one approved tailored CV (Step 18).

    Renders the newest *approved* run for the job, so a newer cancelled or
    failed run does not hide an approved one.

    On demand, not a batch stage, so it is deliberately absent from the
    pipeline dashboard (core.pipeline.registry).

    Args:
        args: Parsed CLI arguments — `user_id`, `job_group_id`, `out_dir` and
            `ats_only` (skip the designed PDF).

    Returns:
        0 when both files were written and verified; 1 when there is no
        approved run for the job, the title is blank, or the .docx failed
        verification.
    """
    settings = get_settings()
    engine = build_engine(settings.app_database_url)
    run_id = latest_run_id(
        engine, args.user_id, args.job_group_id, status="approved"
    ) or latest_run_id(engine, args.user_id, args.job_group_id)
    run = read_run(engine, args.user_id, run_id) if run_id is not None else None
    if run is None:
        print("render-cv: no tailored CV for this job; run tailor-cv first")
        return 1
    if run.status != "approved" or run.document is None:
        print(
            f"render-cv: the latest run is {run.status}; "
            "only an approved run can be rendered"
        )
        return 1
    job = load_job_context(engine, args.job_group_id)
    company = job.company if job else None
    out_dir = cv_directory(
        Path(args.out_dir),
        run.document.identity,
        run.created_at,
        run.document.target_title,
        company,
    )
    try:
        files = render_cv_files(
            run.document, company, out_dir, with_pdf=not args.ats_only
        )
    except (RenderError, ValueError) as exc:
        print(f"render-cv: {exc}")
        return 1
    spec_path = None
    if job is not None:
        ui_base = os.environ.get("UI_BASE_URL", "http://localhost:8501")
        spec_path = out_dir / JOB_SPEC_FILENAME
        spec_path.write_text(
            render_job_spec(
                job,
                load_job_source(engine, args.job_group_id),
                job_ui_link(ui_base, args.job_group_id),
                intel_ui_link(ui_base, args.job_group_id),
            ),
            encoding="utf-8",
        )
    print(
        f"render-cv complete: docx={files.docx_path} txt={files.txt_path}"
        + (f" pdf={files.pdf_path}" if files.pdf_path else "")
        + (f" job_spec={spec_path}" if spec_path else "")
    )
    for warning in files.warnings:
        print(f"render-cv warning: {warning}")
    return 0


def _cmd_generate_artefact(args: argparse.Namespace, kind: str) -> int:
    """Run `generate-cover-letter` / `generate-pitch` (Step 19).

    On demand, not a batch stage, so absent from the pipeline dashboard.

    Args:
        args: Parsed CLI arguments: `user_id` and `job_group_id`.
        kind: `cover_letter` or `pitch`.

    Returns:
        0 when the result is approved or needs review; 1 when the job is
        unknown, the CV or title is missing, or the run failed.
    """
    command = f"generate-{kind.replace('_', '-')}"
    settings = get_settings()
    engine = build_engine(settings.app_database_url)
    job = load_job_context(engine, args.job_group_id)
    if job is None:
        print(f"{command}: no such job {args.job_group_id}")
        return 1
    http_client = httpx.Client(timeout=300.0)
    try:
        stored = generate_artefact(
            engine, args.user_id, kind, job, adapters=_build_llm_adapters(http_client)  # type: ignore[arg-type]
        )
    except (NoCvError, NoTargetTitleError) as exc:
        print(f"{command}: {exc}")
        return 1
    finally:
        http_client.close()
    pending = sum(1 for s in stored.open_sentences if s.status == "pending")
    print(
        f"{command} {kind} {stored.status}: {stored.target_title} — "
        f"{pending} sentence(s) awaiting your decision"
    )
    if stored.status == "failed":
        print(f"error: {stored.error_message}")
        return 1
    return 0


def _cmd_render_artefacts(args: argparse.Namespace) -> int:
    """Run `render-artefacts`: write the approved letter and pitch beside the CV.

    Args:
        args: Parsed CLI arguments: `user_id`, `job_group_id`, `out_dir`,
            `ats_only`.

    Returns:
        0 when at least one file was written; 1 when there was nothing to
        render or a file failed verification.
    """
    settings = get_settings()
    engine = build_engine(settings.app_database_url)
    job = load_job_context(engine, args.job_group_id)
    if job is None:
        print(f"render-artefacts: no such job {args.job_group_id}")
        return 1
    renderable = []
    for kind in ("cover_letter", "pitch"):
        stored = read_latest_artefact(engine, args.user_id, kind, args.job_group_id)  # type: ignore[arg-type]
        if stored is None:
            continue
        if stored.status != "approved" or stored.content is None:
            print(
                f"render-artefacts: the {kind} is {stored.status}; "
                "only an approved one is rendered"
            )
            continue
        renderable.append((kind, stored))
    if not renderable:
        print("render-artefacts: nothing to render; generate and approve one first")
        return 1
    run_id = latest_run_id(engine, args.user_id, args.job_group_id, status="approved")
    run = read_run(engine, args.user_id, run_id) if run_id is not None else None
    first_kind, first = renderable[0]
    created = run.created_at if run is not None else first.created_at
    model = CoverLetter if first_kind == "cover_letter" else Pitch
    out_dir = cv_directory(
        Path(args.out_dir),
        model.model_validate(first.content).sender,
        created,
        first.target_title,
        job.company,
    )
    written: list[Path] = []
    try:
        for kind, stored in renderable:
            if kind == "cover_letter":
                files = render_cover_letter_files(
                    CoverLetter.model_validate(stored.content),
                    out_dir,
                    with_pdf=not args.ats_only,
                )
                written += [
                    p for p in (files.docx_path, files.txt_path, files.pdf_path) if p
                ]
            else:
                written.append(
                    render_pitch_file(Pitch.model_validate(stored.content), out_dir)
                )
    except ArtefactRenderError as exc:
        print(f"render-artefacts: {exc}")
        return 1
    print("render-artefacts complete: " + " ".join(str(p) for p in written))
    return 0


def _cmd_generate_question_bank(args: argparse.Namespace) -> int:
    """Run `generate-question-bank` (Step 20): fill the shared interview bank.

    On demand, not a batch stage. It prints the work and a cost estimate and
    asks before spending anything; topics that already have their questions
    are skipped, so it is safe to run again.

    Args:
        args: Parsed CLI arguments: `category`, `topic`, `force_topic`, `yes`.

    Returns:
        0 when everything asked for was generated or nothing was needed; 1
        when a topic stopped with an error, the answer was no, or the
        arguments are inconsistent.
    """
    settings = get_settings()
    engine = build_engine(settings.app_database_url)
    if args.force_topic:
        if not (args.category and args.topic):
            print("generate-question-bank: --force-topic needs --category and --topic")
            return 1
        rejected = interview_store.reject_topic(engine, args.category, args.topic)
        print(
            f"generate-question-bank: marked {rejected} existing question(s) rejected"
        )
    work, unconfigured = interview_service.pending_work(
        engine, category=args.category, topic=args.topic
    )
    for category in unconfigured:
        print(f"generate-question-bank: no topics configured for {category}; skipped")
    if not work:
        print("generate-question-bank: nothing to generate")
        return 0
    for item in work:
        print(f"  {item.category} / {item.topic}: {item.missing} to generate")
    cost = interview_service.estimate_cost(work)
    print(f"generate-question-bank: estimated cost about ${cost:.2f} (Claude)")
    if not args.yes:
        try:
            answer = input("Continue? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("generate-question-bank: cancelled, nothing spent or changed")
            return 1
    if args.force_topic:
        rejected = interview_store.reject_topic(engine, args.category, args.topic)
        print(
            f"generate-question-bank: marked {rejected} existing question(s) rejected"
        )
    http_client = httpx.Client(timeout=300.0)
    try:
        results = interview_service.generate_work(
            engine, work, adapters=_build_llm_adapters(http_client)
        )
    finally:
        http_client.close()
    failed = False
    for r in results:
        print(
            f"  {r.category} / {r.topic}: {r.approved} approved, "
            f"{r.needs_review} need review, {r.rounds} round(s)"
            + (f" - {r.error}" if r.error else "")
        )
        failed = failed or bool(r.error)
    return 1 if failed else 0


def _cmd_score_question_gaps(args: argparse.Namespace) -> int:
    """Run `score-question-gaps` (Step 20): score a user's CV gaps.

    Args:
        args: Parsed CLI arguments: `user_id` and `category`.

    Returns:
        0 on success; 1 when the user has no CV.
    """
    settings = get_settings()
    engine = build_engine(settings.app_database_url)
    try:
        scored = interview_practice.refresh_gaps(engine, args.user_id, args.category)
    except interview_practice.NoCvError as exc:
        print(f"score-question-gaps: {exc}")
        return 1
    print(f"score-question-gaps complete: {scored} question(s) scored")
    return 0


def _cmd_export_anki(args: argparse.Namespace) -> int:
    """Run `export-anki` (Step 20): write an Anki import file.

    Args:
        args: Parsed CLI arguments: `user_id`, `category` and `out`.

    Returns:
        0 when the file was written; 1 when there are no approved questions.
    """
    settings = get_settings()
    engine = build_engine(settings.app_database_url)
    questions = interview_store.list_questions(engine, args.category)
    if not questions:
        print("export-anki: no approved questions; run generate-question-bank first")
        return 1
    content = export_anki(questions, list_interview_progress(engine, args.user_id))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(f"export-anki complete: {len(questions)} note(s) written to {out}")
    return 0


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
    if args.website and normalise_website_url(args.website) is None:
        print(
            f"research-company: the website {args.website!r} is not a usable "
            "address, e.g. acme.com"
        )
        return 1
    override = None
    if args.end_client or args.website:
        override = ManualOverride(end_client=args.end_client, website_url=args.website)
    config = load_company_intel_config()
    http_client = httpx.Client(timeout=300.0)
    try:
        adapters = _build_llm_adapters(http_client)
        fetcher = CachedFetcher(
            Crawl4AIFetcher(
                settings.crawl4ai_url, api_token=settings.crawl4ai_api_token
            ),
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


# Tasks with an eval configured — extend as future steps (15-17,
# 19, 20) add their own eval_metric entry to config/llm_tasks.yml.
_EVAL_TASKS = ["job_categorisation", "cv_extraction", "skill_extraction"]


def _report_eval_result(result: EvalRunResult) -> None:
    """Print one EvalRunResult in a human-readable line.

    Args:
        result: The result to report.
    """
    if result.status == "insufficient_data":
        print(
            f"{result.task} ({result.provider}): insufficient_data "
            f"({result.case_count} cases, need {20})"
        )
        return
    if result.status == "provider_not_configured":
        print(f"{result.task} ({result.provider}): provider_not_configured")
        return
    delta_str = f", delta={result.delta:+.3f}" if result.delta is not None else ""
    regressed_str = " REGRESSED" if result.regressed else ""
    print(
        f"{result.task} ({result.provider}): score={result.score:.3f} "
        f"(n={result.case_count}){delta_str}{regressed_str}"
    )


def _cmd_run_evals(args: argparse.Namespace) -> int:
    """Run the `run-evals` subcommand.

    Args:
        args: Parsed CLI arguments — `task` (or `all`) and `provider`.

    Returns:
        0 if every run completed with no regression, 1 if any run
        regressed or errored (e.g. no adapter configured for the
        resolved provider) — `run-evals` is the harness's designated
        CI entry point, so any incomplete run must fail the command
        rather than silently exiting 0.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    # skill_extraction evals a local 8B model generating on CPU, which
    # routinely outlasts 30s (same reasoning as extract-job-skills above).
    http_client = httpx.Client(timeout=2000.0)
    try:
        adapters = _build_llm_adapters(http_client)
        tasks = _EVAL_TASKS if args.all else [args.task]
        provider_labels = (
            ["target", "local"] if args.provider == "both" else [args.provider]
        )

        any_regressed = False
        any_errored = False
        for task in tasks:
            for provider_label in provider_labels:
                try:
                    result = run_eval(
                        task, provider_label, engine=engine, adapters=adapters
                    )
                except KeyError as exc:
                    any_errored = True
                    print(
                        f"{task} ({provider_label}): no adapter configured for "
                        f"the resolved provider ({exc}) — check "
                        "ANTHROPIC_API_KEY/OLLAMA_BASE_URL"
                    )
                    continue
                _report_eval_result(result)
                any_regressed = any_regressed or result.regressed
        return 1 if (any_regressed or any_errored) else 0
    finally:
        http_client.close()


def main(argv: list[str] | None = None) -> int:
    """Run the pipeline CLI.

    Args:
        argv: Command-line arguments, excluding the program name. Defaults
            to `sys.argv[1:]` when `None`.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(prog="pipeline")
    subparsers = parser.add_subparsers(dest="command")

    ingest_parser = subparsers.add_parser(
        "ingest", help="Run one connector through the shared runner"
    )
    ingest_parser.add_argument("--source", required=True)
    ingest_parser.add_argument("--query", required=True)
    ingest_parser.add_argument(
        "--since",
        default=None,
        help="ISO-8601 datetime, e.g. 2026-09-01 or 2026-09-01T00:00:00+00:00",
    )
    ingest_parser.add_argument("--region", default=None)
    ingest_parser.add_argument(
        "--collection-channel",
        default="targeted",
        choices=["targeted", "discovery"],
        help=(
            "'targeted' (default, frozen keyword matrix) or 'discovery' "
            "(wide/shallow, PLAN.md Step 4a). Run discovery sweeps at most "
            "weekly by hand — no scheduler exists yet to enforce this."
        ),
    )

    subparsers.add_parser(
        "enrich-engagement-terms",
        help="Extract and write engagement/IR35/rate terms for every " "unioned job",
    )

    subparsers.add_parser(
        "compute-blocking-keys",
        help="Compute and write blocking keys for every silver posting",
    )

    subparsers.add_parser(
        "compute-similarity-features",
        help="Compute and write per-job similarity features (SimHash, salary)",
    )

    subparsers.add_parser(
        "compute-title-similarity-scores",
        help="Compute and write pair-level title similarity scores",
    )

    subparsers.add_parser(
        "cluster-jobs",
        help="Assign job_group_id to every unclustered silver posting",
    )

    subparsers.add_parser(
        "compute-survivorship",
        help="Resolve field-level survivorship for every job_group_id",
    )

    subparsers.add_parser(
        "classify-jobs",
        help="Classify every unclassified job_group_id (category, seniority_band)",
    )

    run_evals_parser = subparsers.add_parser(
        "run-evals",
        help="Run a task's golden set against target/local/both providers",
    )
    run_evals_group = run_evals_parser.add_mutually_exclusive_group(required=True)
    run_evals_group.add_argument("--task", choices=_EVAL_TASKS)
    run_evals_group.add_argument("--all", action="store_true")
    run_evals_parser.add_argument(
        "--provider", required=True, choices=["target", "local", "both"]
    )

    load_esco_parser = subparsers.add_parser(
        "load-esco",
        help="Load an ESCO English CSV release directory into the esco schema",
    )
    load_esco_parser.add_argument("directory")

    subparsers.add_parser(
        "embed-esco",
        help="Embed every ESCO skill's preferred label (resumable; ~14k Ollama calls)",
    )

    map_skills_parser = subparsers.add_parser(
        "map-skills",
        help="Sync seed aliases, then map every extracted JD skill string",
    )
    remap_group = map_skills_parser.add_mutually_exclusive_group()
    remap_group.add_argument(
        "--remap-unresolved",
        action="store_true",
        help="First clear auto-made embedding / open mappings so they re-map",
    )
    remap_group.add_argument(
        "--remap-all-auto",
        action="store_true",
        help=(
            "First clear EVERY auto-made mapping (alias, label, embedding, open) "
            "so a new alias or a fixed seed entry re-applies; never touches "
            "resolved / rejected / dismissed rows or CV skill ids"
        ),
    )

    llm_parser = subparsers.add_parser(
        "llm-map-skills",
        help="Have Claude pre-review open unmapped skill strings",
    )
    llm_parser.add_argument(
        "--limit", type=int, default=None, help="Send at most this many strings"
    )
    llm_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print how many strings would be sent and the estimated cost; "
            "no API calls"
        ),
    )
    llm_parser.add_argument(
        "--evaluate",
        action="store_true",
        help=(
            "Judge the model against strings a person already resolved; writes "
            "nothing. Exits 1 if high-confidence agreement is below 90%%"
        ),
    )
    llm_parser.add_argument(
        "--sample",
        type=int,
        default=200,
        help="Resolved strings to use with --evaluate",
    )

    extract_parser = subparsers.add_parser(
        "extract-job-skills",
        help="Extract skills + must/nice-to-have levels for every dedup survivor",
    )
    extract_parser.add_argument(
        "--limit", type=int, default=None, help="Process at most this many jobs"
    )
    extract_parser.add_argument(
        "--source",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "Only jobs whose winning source is NAME (repeatable), e.g. "
            "greenhouse. Snippet-only sources (adzuna, jooble, reed) and test "
            "rows are skipped by naming only the sources you want"
        ),
    )
    extract_parser.add_argument(
        "--category",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "Only jobs whose gold.dim_job category is NAME (repeatable), e.g. "
            "data_engineer; a job not yet categorised never matches"
        ),
    )
    extract_parser.add_argument(
        "--country",
        action="append",
        default=None,
        metavar="ISO",
        help=(
            "Only jobs whose gold.dim_job.country_iso is ISO (repeatable), e.g. "
            "GB; a job whose location did not resolve to a country never matches "
            "(core.normalisation.location) — check coverage before relying on it"
        ),
    )

    map_cv_parser = subparsers.add_parser(
        "map-cv-skills",
        help="Fill canonical_id on a user's CV skills (writes a new CV version)",
    )
    map_cv_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    map_cv_parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Recompute every skill's canonical_id from the current mapping, "
            "replacing stale ids and clearing ones that no longer resolve "
            "(default: only fill skills that have no id yet)"
        ),
    )

    score_filter_parser = subparsers.add_parser(
        "score-filter-jobs",
        help="Run stage-1 hard filters for one user (PLAN.md Step 15)",
    )
    score_filter_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    score_filter_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of jobs considered (tests only)",
    )

    chunk_embed_jobs_parser = subparsers.add_parser(
        "chunk-embed-jobs",
        help="Chunk and embed every job description with no chunks yet "
        "(PLAN.md Step 15 stage 2, shared across users)",
    )
    chunk_embed_jobs_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of jobs chunked (tests only)",
    )

    chunk_embed_cv_parser = subparsers.add_parser(
        "chunk-embed-cv",
        help="Chunk and embed one user's current CV truth base "
        "(PLAN.md Step 15 stage 2, CV side)",
    )
    chunk_embed_cv_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    chunk_embed_cv_parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Recompute even if this CV version already has chunks "
            "(default: skip if this version is already chunked)"
        ),
    )

    score_similarity_parser = subparsers.add_parser(
        "score-similarity",
        help="Vector similarity + cross-encoder rerank for one user "
        "(PLAN.md Step 15 stage 2b)",
    )
    score_similarity_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    score_similarity_parser.add_argument(
        "--top-n",
        type=int,
        default=200,
        help="How many top-scoring jobs get a reranker score",
    )

    score_skill_coverage_parser = subparsers.add_parser(
        "score-skill-coverage",
        help="Skill coverage with recency decay for one user "
        "(PLAN.md Step 15 stage 3)",
    )
    score_skill_coverage_parser.add_argument("--user-id", required=True, type=uuid.UUID)

    score_llm_rerank_parser = subparsers.add_parser(
        "score-llm-rerank",
        help="LLM re-rank of the top pre-LLM-scored jobs for one user "
        "(PLAN.md Step 15 stage 4)",
    )
    score_llm_rerank_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    score_llm_rerank_parser.add_argument(
        "--top-n",
        type=int,
        default=50,
        help="Send at most this many jobs to the LLM (plan's own cap: 50)",
    )

    score_blend_parser = subparsers.add_parser(
        "score-blend",
        help="Config-driven weighted blend of every present score component "
        "for one user (PLAN.md Step 15, final stage)",
    )
    score_blend_parser.add_argument("--user-id", required=True, type=uuid.UUID)

    tailor_cv_parser = subparsers.add_parser(
        "tailor-cv",
        help="Tailor one user's CV to one job, with the fabrication guard "
        "(PLAN.md Step 17); on demand, not a pipeline stage",
    )
    tailor_cv_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    tailor_cv_parser.add_argument("--job-group-id", required=True)
    tailor_cv_parser.add_argument(
        "--backend",
        choices=["claude", "native", "docker"],
        default=None,
        help="Where the Tailor runs: claude, native (Ollama on this Mac) or "
        "docker (the compose Ollama service); default follows config/llm_tasks.yml",
    )

    render_cv_parser = subparsers.add_parser(
        "render-cv",
        help="Write the ATS-safe .docx and .txt and the designed PDF for one "
        "approved tailored CV (PLAN.md Step 18); on demand, not a pipeline stage",
    )
    render_cv_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    render_cv_parser.add_argument("--job-group-id", required=True)
    render_cv_parser.add_argument(
        "--out-dir",
        default="output",
        help="Root folder; each CV goes to "
        "<root>/<first_last>/<YYYY-MM>/<job_title>/<company>/ "
        "(default: ./output, git-ignored)",
    )
    render_cv_parser.add_argument(
        "--ats-only",
        action="store_true",
        help="Write only the ATS .docx and .txt, not the designed PDF",
    )

    letter_parser = subparsers.add_parser(
        "generate-cover-letter",
        help="Write a checked cover letter for a job (PLAN.md Step 19); on "
        "demand, not a pipeline stage",
    )
    pitch_parser = subparsers.add_parser(
        "generate-pitch",
        help="Write a checked 30/60/90 second pitch for a job (PLAN.md Step 19); "
        "on demand, not a pipeline stage",
    )
    render_artefacts_parser = subparsers.add_parser(
        "render-artefacts",
        help="Write the approved cover letter and pitch beside the CV (PLAN.md "
        "Step 19); on demand, not a pipeline stage",
    )
    for artefact_parser in (letter_parser, pitch_parser, render_artefacts_parser):
        artefact_parser.add_argument("--user-id", required=True, type=uuid.UUID)
        artefact_parser.add_argument("--job-group-id", required=True)
    render_artefacts_parser.add_argument(
        "--out-dir",
        default="output",
        help="Root folder; files go to "
        "<root>/<first_last>/<YYYY-MM>/<job_title>/<company>/",
    )
    render_artefacts_parser.add_argument(
        "--ats-only",
        action="store_true",
        help="Write only the .docx and .txt, not the PDF",
    )

    bank_parser = subparsers.add_parser(
        "generate-question-bank",
        help="Generate the shared interview question bank, per category/topic "
        "(PLAN.md Step 20); on demand, not a pipeline stage",
    )
    bank_parser.add_argument("--category")
    bank_parser.add_argument("--topic")
    bank_parser.add_argument(
        "--force-topic",
        action="store_true",
        help="Reject a topic's existing questions and generate it again",
    )
    bank_parser.add_argument(
        "--yes", action="store_true", help="Skip the cost confirmation prompt"
    )
    gaps_parser = subparsers.add_parser(
        "score-question-gaps",
        help="Score a user's CV gaps against the question bank (PLAN.md Step 20); "
        "on demand, not a pipeline stage",
    )
    gaps_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    gaps_parser.add_argument("--category")
    anki_parser = subparsers.add_parser(
        "export-anki",
        help="Write an Anki import file of the bank and your answers (PLAN.md "
        "Step 20); on demand, not a pipeline stage",
    )
    anki_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    anki_parser.add_argument("--category")
    anki_parser.add_argument("--out", default="output/interview_bank.txt")

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

    args = parser.parse_args(argv)

    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command == "enrich-engagement-terms":
        return _cmd_enrich_engagement_terms(args)
    if args.command == "compute-blocking-keys":
        return _cmd_compute_blocking_keys(args)
    if args.command == "compute-similarity-features":
        return _cmd_compute_similarity_features(args)
    if args.command == "compute-title-similarity-scores":
        return _cmd_compute_title_similarity_scores(args)
    if args.command == "cluster-jobs":
        return _cmd_cluster_jobs(args)
    if args.command == "compute-survivorship":
        return _cmd_compute_survivorship(args)
    if args.command == "classify-jobs":
        return _cmd_classify_jobs(args)
    if args.command == "load-esco":
        return _cmd_load_esco(args)
    if args.command == "embed-esco":
        return _cmd_embed_esco(args)
    if args.command == "map-skills":
        return _cmd_map_skills(args)
    if args.command == "llm-map-skills":
        return _cmd_llm_map_skills(args)
    if args.command == "extract-job-skills":
        return _cmd_extract_job_skills(args)
    if args.command == "map-cv-skills":
        return _cmd_map_cv_skills(args)
    if args.command == "score-filter-jobs":
        return _cmd_score_filter_jobs(args)
    if args.command == "chunk-embed-jobs":
        return _cmd_chunk_embed_jobs(args)
    if args.command == "chunk-embed-cv":
        return _cmd_chunk_embed_cv(args)
    if args.command == "score-similarity":
        return _cmd_score_similarity(args)
    if args.command == "score-skill-coverage":
        return _cmd_score_skill_coverage(args)
    if args.command == "score-llm-rerank":
        return _cmd_score_llm_rerank(args)
    if args.command == "score-blend":
        return _cmd_score_blend(args)
    if args.command == "tailor-cv":
        return _cmd_tailor_cv(args)
    if args.command == "render-cv":
        return _cmd_render_cv(args)
    if args.command == "generate-cover-letter":
        return _cmd_generate_artefact(args, "cover_letter")
    if args.command == "generate-pitch":
        return _cmd_generate_artefact(args, "pitch")
    if args.command == "render-artefacts":
        return _cmd_render_artefacts(args)
    if args.command == "generate-question-bank":
        return _cmd_generate_question_bank(args)
    if args.command == "score-question-gaps":
        return _cmd_score_question_gaps(args)
    if args.command == "export-anki":
        return _cmd_export_anki(args)
    if args.command == "research-company":
        return _cmd_research_company(args)
    if args.command == "run-evals":
        return _cmd_run_evals(args)

    print("pipeline scaffold ready — run with `ingest --source X --query Y`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
