import sys
from pathlib import Path

import yaml

from agentic_triage.core.config import DomainConfig


def _validate(path: Path) -> DomainConfig:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    return DomainConfig.from_dict(data)


if __name__ == "__main__":
    configs = sorted(Path("domains").glob("*/config.yaml"))
    if not configs:
        print("No domain configs found under domains/*/config.yaml", file=sys.stderr)
        sys.exit(1)
    errors = []
    for cfg in configs:
        try:
            domain = _validate(cfg)
            print(f"  OK  {cfg}  ({domain.domain_name})")
        except Exception as exc:
            errors.append(f"  FAIL {cfg}: {exc}")
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
