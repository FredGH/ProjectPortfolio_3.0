"""Apply all pending Qdrant collection schema migrations.

Usage:
  python scripts/run_migrations.py

Discovers every ``migrations/NNNN_*.py`` module in lexicographic order and
calls its ``up(client)`` function. Each migration is idempotent — already-applied
migrations are tracked in the ``_migrations`` Qdrant collection and skipped.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def _ensure_migrations_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if "_migrations" not in existing:
        client.create_collection(
            "_migrations",
            vectors_config=VectorParams(size=1, distance=Distance.COSINE),
        )
        log.info("Created _migrations collection")


def _qdrant_url() -> str:
    settings_path = Path(__file__).parent.parent / "agentic_triage" / "settings.py"
    spec = importlib.util.spec_from_file_location("settings", settings_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.QDRANT_HOST


def main() -> None:
    url = _qdrant_url()
    client = QdrantClient(url=url)
    log.info("Connected to Qdrant at %s", url)

    _ensure_migrations_collection(client)

    migration_files = sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.py"))
    if not migration_files:
        log.info("No migration files found in %s", _MIGRATIONS_DIR)
        return

    for path in migration_files:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        log.info("Applying %s ...", path.stem)
        module.up(client)
        log.info("  done")

    log.info("All migrations applied.")


if __name__ == "__main__":
    main()
