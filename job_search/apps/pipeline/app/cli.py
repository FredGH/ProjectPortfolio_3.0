"""Pipeline batch entrypoint (PLAN.md Steps 1 and 3).

The `ingest` subcommand runs one connector through the shared runner —
adding a new connector means adding one entry to `_CONNECTOR_BUILDERS`
below plus one config/sources.yml block, never touching run_connector
itself. `_KNOWN_SOURCES` is derived from `_CONNECTOR_BUILDERS`, so there
is exactly one place to edit.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections.abc import Callable

import httpx

from core.ingestion.connector import Connector
from core.ingestion.manual_connector import ManualConnector, ManualJobQuery
from core.ingestion.rate_limiter import TokenBucket
from core.ingestion.runner import run_connector
from core.ingestion.sources_config import load_sources_config
from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.types import LLMAdapter
from core.settings import get_settings


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


def _build_manual_connector(
    http_client: httpx.Client, llm_adapters: dict[str, LLMAdapter]
) -> Connector:
    """Build the manual-entry connector.

    Args:
        http_client: Shared HTTP client for redirect resolution.
        llm_adapters: Every available LLM adapter, keyed by provider.

    Returns:
        A `ManualConnector` instance.
    """
    return ManualConnector(http_client=http_client, llm_adapters=llm_adapters)


_CONNECTOR_BUILDERS: dict[
    str, Callable[[httpx.Client, dict[str, LLMAdapter]], Connector]
] = {
    "manual": _build_manual_connector,
}

_KNOWN_SOURCES = frozenset(_CONNECTOR_BUILDERS)
"""Every `--source` name the CLI recognises — derived from
`_CONNECTOR_BUILDERS` so the two can never drift apart. Adding a real API
connector (Step 4+) is one new entry in `_CONNECTOR_BUILDERS`, nothing
else, and `_KNOWN_SOURCES` picks it up automatically."""


def _make_factory(
    builder: Callable[[httpx.Client, dict[str, LLMAdapter]], Connector],
    http_client: httpx.Client,
    llm_adapters: dict[str, LLMAdapter],
) -> Callable[[], Connector]:
    """Bind a connector builder's arguments into a zero-argument factory.

    Args:
        builder: One `_CONNECTOR_BUILDERS` entry.
        http_client: The shared HTTP client to bind in.
        llm_adapters: The LLM adapter registry to bind in.

    Returns:
        A zero-argument callable that builds the connector.
    """
    return lambda: builder(http_client, llm_adapters)


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
        `args.source in _KNOWN_SOURCES` — it constructs Settings-dependent
        LLM adapters eagerly and shouldn't run for an unknown source.
    """
    llm_adapters = _build_llm_adapters(http_client)
    return {
        name: _make_factory(builder, http_client, llm_adapters)
        for name, builder in _CONNECTOR_BUILDERS.items()
    }


def _build_manual_query(raw_query: str) -> ManualJobQuery:
    """Parse `--query`'s JSON string into a ManualJobQuery.

    Args:
        raw_query: The `--query` argument's raw string value.

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

    if args.source == "manual":
        try:
            query: object = _build_manual_query(args.query)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    else:
        query = args.query

    since = datetime.datetime.fromisoformat(args.since) if args.since else None

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
        result = run_connector(
            connector_key=args.source,
            connector=factories[args.source](),
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
    ingest_parser.add_argument("--since", default=None)
    ingest_parser.add_argument("--region", default=None)

    args = parser.parse_args(argv)

    if args.command == "ingest":
        return _cmd_ingest(args)

    print("pipeline scaffold ready — run with `ingest --source X --query Y`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
