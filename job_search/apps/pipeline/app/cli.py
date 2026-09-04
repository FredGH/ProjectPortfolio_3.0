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
import sys
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from core.ingestion.adzuna_connector import AdzunaConnector, AdzunaQuery
from core.ingestion.connector import Connector
from core.ingestion.greenhouse_connector import GreenhouseConnector, GreenhouseQuery
from core.ingestion.jooble_connector import JoobleConnector, JoobleQuery
from core.ingestion.manual_connector import ManualConnector, ManualJobQuery
from core.ingestion.rate_limiter import TokenBucket
from core.ingestion.reed_connector import ReedConnector, ReedQuery
from core.ingestion.runner import run_connector
from core.ingestion.sources_config import load_sources_config
from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.types import LLMAdapter
from core.settings import Settings, get_settings


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


def _build_manual_query(raw_query: str, region: str | None) -> ManualJobQuery:
    """Parse `--query`'s JSON string into a ManualJobQuery.

    Args:
        raw_query: The `--query` argument's raw string value.
        region: Unused — manual entry has no region concept. Present only
            so this builder's signature matches every `_QUERY_BUILDERS`
            entry's uniform `(raw_query, region)` shape.

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


def _build_adzuna_query(raw_query: str, region: str | None) -> AdzunaQuery:
    """Build an AdzunaQuery from --query and --region.

    Args:
        raw_query: The `--query` argument's raw string value (keywords).
        region: The `--region` argument's raw string value.

    Returns:
        The `AdzunaQuery`.

    Raises:
        ValueError: If `region` is not given — Adzuna's search endpoint is
            country-scoped, unlike manual entry or Greenhouse.
    """
    if not region:
        raise ValueError("--region is required for source=adzuna")
    return AdzunaQuery(keywords=raw_query, country=region)


def _build_reed_query(raw_query: str, region: str | None) -> ReedQuery:
    """Build a ReedQuery from --query and --region.

    Args:
        raw_query: The `--query` argument's raw string value (keywords).
        region: The `--region` argument's raw string value, used as an
            optional UK location filter — unlike Adzuna, Reed doesn't
            require one (it's UK-only already).

    Returns:
        The `ReedQuery`.
    """
    return ReedQuery(keywords=raw_query, location=region)


def _build_greenhouse_query(raw_query: str, region: str | None) -> GreenhouseQuery:
    """Build a GreenhouseQuery from --query.

    Args:
        raw_query: The `--query` argument's raw string value — a
            comma-separated list of board slugs, or empty to use the
            active target_company registry.
        region: Unused — Greenhouse boards aren't region-scoped. Present
            only so this builder's signature matches every
            `_QUERY_BUILDERS` entry's uniform `(raw_query, region)` shape.

    Returns:
        The `GreenhouseQuery`.
    """
    slugs = [s.strip() for s in raw_query.split(",") if s.strip()]
    return GreenhouseQuery(board_slugs=slugs or None)


def _build_jooble_query(raw_query: str, region: str | None) -> JoobleQuery:
    """Build a JoobleQuery from --query and --region.

    Args:
        raw_query: The `--query` argument's raw string value (keywords).
        region: The `--region` argument's raw string value, used as an
            optional location filter.

    Returns:
        The `JoobleQuery`.
    """
    return JoobleQuery(keywords=raw_query, location=region)


_QUERY_BUILDERS: dict[str, Callable[[str, str | None], object]] = {
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
        args: Parsed CLI arguments — `source`, `query`, `since`, `region`.

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

    try:
        query = _QUERY_BUILDERS[args.source](args.query, args.region)
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
            landing_uri=settings.landing_uri,
            database_url=settings.database_url,
            rate_limiter=rate_limiter,
        )
        print(
            f"ingest complete: source={args.source} "
            f"records={result.run_metadata.records} "
            f"run_id={result.run_metadata.run_id}"
        )
        return 0
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

    args = parser.parse_args(argv)

    if args.command == "ingest":
        return _cmd_ingest(args)

    print("pipeline scaffold ready — run with `ingest --source X --query Y`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
