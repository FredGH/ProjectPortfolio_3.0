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
