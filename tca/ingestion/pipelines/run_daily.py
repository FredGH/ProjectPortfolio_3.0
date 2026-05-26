"""Run the daily dlt pipeline (OMS + market data + FI + Eurex) for one trade date.

Excludes ref_data — reference tables are stable and seeded once.
"""

from __future__ import annotations

import logging
import os
from datetime import date

import dlt

from ingestion.sources.eurex_source import eurex_source
from ingestion.sources.fi_pricing_source import fi_pricing_source
from ingestion.sources.market_data_source import market_data_source
from ingestion.sources.oms_source import oms_source

logger = logging.getLogger(__name__)


def _build_pipeline() -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name="tca_batch",
        destination=dlt.destinations.postgres(credentials=os.environ["DATABASE_URL"]),
        dataset_name="stg_raw",
    )


def run_daily(trade_date: date) -> dict[str, str]:
    pipeline = _build_pipeline()
    results: dict[str, str] = {}

    steps = [
        ("oms", oms_source(trade_date=trade_date)),
        ("market_data", market_data_source(trade_date=trade_date)),
        ("fi_pricing", fi_pricing_source(trade_date=trade_date)),
        ("eurex", eurex_source(trade_date=trade_date)),
    ]

    for name, source in steps:
        logger.info("  [%s] %s …", trade_date, name)
        info = pipeline.run(source)
        results[name] = str(info)

    return results
