import sys
from pathlib import Path

import yaml

from agentic_triage.core.config import (  # noqa: F401 — verifies import chain
    DomainConfig,
)


def _validate(path: Path) -> None:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "domain_name" not in data:
        raise ValueError(f"{path}: missing required field 'domain_name'")


if __name__ == "__main__":
    configs = sorted(Path("domains").glob("*/config.yaml"))
    errors = []
    for cfg in configs:
        try:
            _validate(cfg)
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
