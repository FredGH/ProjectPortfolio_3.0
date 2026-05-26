"""Seed 60 business days of synthetic TCA data.

Generates unique orders per date using date-derived RNG seeds.
Run once inside the app container after the initial seed.py has already
populated reference data.

Usage (from project root inside Docker):
    docker exec tca-app-1 python -m ingestion.seed_history

Or locally with DATABASE_URL set:
    python -m ingestion.seed_history
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from ingestion.pipelines.run_all import _build_pipeline  # noqa: E402
from ingestion.pipelines.run_daily import run_daily  # noqa: E402
from ingestion.sources.ref_data_source import ref_data_source  # noqa: E402

logger = logging.getLogger(__name__)


def _business_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon–Fri
            days.append(d)
        d += timedelta(days=1)
    return days


def seed_history(
    start: date = date(2026, 1, 5),
    end: date = date(2026, 4, 23),
) -> None:
    days = _business_days(start, end)
    logger.info("Seeding %d business days (%s → %s) …", len(days), start, end)

    # Ref data only once — instruments, venues, algos, clients don't change daily
    logger.info("Loading reference data …")
    pipeline = _build_pipeline()
    pipeline.run(ref_data_source())
    logger.info("Reference data done.")

    for i, trade_date in enumerate(days, 1):
        logger.info("[%d/%d] %s", i, len(days), trade_date)
        try:
            run_daily(trade_date)
        except Exception:
            logger.exception("Failed on %s — skipping", trade_date)

    logger.info("History seed complete — %d days loaded.", len(days))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    seed_history()
