"""Bootstrap the TCA platform with 400 synthetic orders and all reference data.

Run once after `docker compose up postgres` to populate stg_raw.
Requires DATABASE_URL to be set (loaded from .env if present).
"""
from __future__ import annotations

import logging
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

import sqlalchemy as sa
import bcrypt as _bcrypt

from ingestion.pipelines.run_all import run_all

logger = logging.getLogger(__name__)


def _seed_auth_clients() -> None:
    """Replace placeholder bcrypt hashes with real hashes from env vars."""
    db_url = os.environ["DATABASE_URL"]
    engine = sa.create_engine(db_url)

    client_secrets = {
        "trader_01":     os.environ.get("TRADER_01_SECRET", "changeme"),
        "compliance_01": os.environ.get("COMPLIANCE_01_SECRET", "changeme"),
        "head_trading":  os.environ.get("TRADER_01_SECRET", "changeme"),
        "client_cp_a":   os.environ.get("CLIENT_CP_A_SECRET", "changeme"),
        "client_cp_b":   os.environ.get("CLIENT_CP_A_SECRET", "changeme"),
        "admin_01":      os.environ.get("TRADER_01_SECRET", "changeme"),
    }

    with engine.begin() as conn:
        for client_id, secret in client_secrets.items():
            hashed = _bcrypt.hashpw(secret.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
            conn.execute(
                sa.text(
                    "UPDATE auth.api_clients SET client_secret_hash = :h WHERE client_id = :cid"
                ),
                {"h": hashed, "cid": client_id},
            )
    logger.info("Auth client secrets hashed and stored.")


def seed_all(trade_date: date | None = None) -> dict[str, str]:
    if trade_date is None:
        trade_date = date(2025, 1, 15)

    logger.info("Seeding auth clients …")
    _seed_auth_clients()

    logger.info("Running dlt pipelines for trade_date=%s …", trade_date)
    results = run_all(trade_date=trade_date)

    logger.info("Seed complete.")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results = seed_all()
    for name, info in results.items():
        print(f"  {name}: {info}")
    print("Done — 400 synthetic orders + reference data loaded into stg_raw.")
