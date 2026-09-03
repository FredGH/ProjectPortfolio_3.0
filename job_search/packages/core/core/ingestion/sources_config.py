"""config/sources.yml schema and loader (PLAN.md Step 3).

This is what "adding a connector requires one new file plus one
sources.yml block" means concretely — the runner reads this file to build
a TokenBucket and retry policy per connector; a connector with no entry
here simply runs unrated/unlimited.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "sources.yml"


@dataclass(frozen=True)
class SourceConfig:
    """One connector's entry in config/sources.yml.

    Attributes:
        enabled: Whether this connector should be used. `auth` values
            themselves live in `.env`, referenced here only via
            `${VAR_NAME}` placeholders for documentation — this loader
            does not resolve them; that's the connector's own job at
            construction time via `core.settings`.
        calls_per_hour: Token-bucket capacity/refill-period-per-hour, or
            `None` for no rate limiting.
        concurrency: Maximum concurrent requests, or `None` for no cap
            (informational for now — Step 3's runner processes one
            connector at a time; a future step may use this for
            parallelism).
        backoff_base: Base delay in seconds for `retry_with_backoff`, or
            `None` to use the runner's default.
        backoff_max_retries: Max retries for `retry_with_backoff`, or
            `None` to use the runner's default.
        regions: Region codes this connector should be queried across, or
            `None` if not region-scoped.
    """

    enabled: bool
    calls_per_hour: int | None
    concurrency: int | None
    backoff_base: float | None
    backoff_max_retries: int | None
    regions: list[str] | None


def load_sources_config(path: Path | None = None) -> dict[str, SourceConfig]:
    """Load and parse config/sources.yml.

    Args:
        path: Path to the sources YAML file. Defaults to
            `config/sources.yml` at the repository root.

    Returns:
        A mapping of connector key to its `SourceConfig`. Returns an
        empty mapping if the file doesn't exist or has no `sources` key —
        both are valid states (no rate-limited connectors configured yet).
    """
    target = path or _DEFAULT_CONFIG_PATH
    if not target.exists():
        return {}

    raw = yaml.safe_load(target.read_text())
    sources = raw.get("sources", {}) if raw else {}

    result: dict[str, SourceConfig] = {}
    for key, entry in sources.items():
        entry = entry or {}
        backoff = entry.get("backoff") or {}
        result[key] = SourceConfig(
            enabled=bool(entry.get("enabled", False)),
            calls_per_hour=entry.get("calls_per_hour"),
            concurrency=entry.get("concurrency"),
            backoff_base=(float(backoff["base"]) if "base" in backoff else None),
            backoff_max_retries=backoff.get("max_retries"),
            regions=entry.get("regions"),
        )
    return result
