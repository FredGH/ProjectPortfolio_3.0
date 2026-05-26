"""Launch the FastAPI app for local development.

Usage:
  python scripts/run_api.py
"""
from __future__ import annotations

from pathlib import Path

import uvicorn
import yaml

from agentic_triage.api.app import create_multi_domain_app
from agentic_triage.core.config import DomainConfig

_DOMAINS_DIR = Path("domains")


def load_configs() -> dict[str, DomainConfig]:
    configs: dict[str, DomainConfig] = {}
    for path in sorted(_DOMAINS_DIR.rglob("config.yaml")):
        domain = path.parent.name
        configs[domain] = DomainConfig.from_dict(yaml.safe_load(path.read_text()))
    return configs


if __name__ == "__main__":
    app = create_multi_domain_app(load_configs())
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
