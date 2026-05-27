"""One-off bootstrap: initialise DB schemas + seed 400 synthetic orders.

Run as a one-off ECS Fargate task by the deploy workflow:
  aws ecs run-task ... --overrides '{"containerOverrides":[
    {"name":"tca-api","command":["python","ingestion/bootstrap.py"]}]}'

Idempotent: safe to re-run (CREATE IF NOT EXISTS throughout init.sql).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import psycopg2.errors

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

INIT_SQL = Path(__file__).parent.parent / "init.sql"


def _init_db() -> None:
    db_url = os.environ["DATABASE_URL"]
    parsed = urlparse(db_url)

    conn = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=parsed.path.lstrip("/"),
        user=parsed.username,
        password=parsed.password,
        connect_timeout=30,
    )
    conn.autocommit = True  # DDL and CREATE DATABASE require autocommit

    sql = INIT_SQL.read_text()

    with conn.cursor() as cur:
        for raw in sql.split(";"):
            stmt = raw.strip()
            if not stmt or stmt.startswith("--"):
                continue
            try:
                cur.execute(stmt)
            except psycopg2.errors.DuplicateDatabase:
                log.info("skip (already exists): %s", stmt[:80])
            except psycopg2.errors.DuplicateObject:
                log.info("skip (already exists): %s", stmt[:80])
            except psycopg2.errors.DuplicateTable:
                log.info("skip (already exists): %s", stmt[:80])
            except Exception as exc:
                # Non-fatal: log and continue; IF NOT EXISTS guards the rest
                log.warning("statement warning: %s | %s", exc, stmt[:80])

    conn.close()
    log.info("DB init complete.")


def _seed() -> None:
    log.info("Running seed pipeline …")
    result = subprocess.run(
        [sys.executable, "ingestion/seed.py"],
        cwd=Path(__file__).parent.parent,
        check=False,
    )
    if result.returncode != 0:
        log.error("Seed failed with exit code %d", result.returncode)
        sys.exit(result.returncode)
    log.info("Seed complete.")


if __name__ == "__main__":
    _init_db()
    _seed()
    log.info("Bootstrap finished.")
