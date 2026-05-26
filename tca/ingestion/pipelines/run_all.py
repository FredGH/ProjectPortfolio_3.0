from __future__ import annotations

import logging
import os
from datetime import date

import dlt

from ingestion.sources.eurex_source import eurex_source
from ingestion.sources.fi_pricing_source import fi_pricing_source
from ingestion.sources.market_data_source import market_data_source
from ingestion.sources.oms_source import oms_source
from ingestion.sources.ref_data_source import ref_data_source

logger = logging.getLogger(__name__)


def _build_pipeline() -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name="tca_batch",
        destination=dlt.destinations.postgres(credentials=os.environ["DATABASE_URL"]),
        dataset_name="stg_raw",
    )


def run_all(trade_date: date | None = None) -> dict[str, str]:
    if trade_date is None:
        trade_date = date(2025, 1, 15)

    pipeline = _build_pipeline()
    results: dict[str, str] = {}

    steps = [
        ("ref_data", ref_data_source()),
        ("oms", oms_source(trade_date=trade_date)),
        ("market_data", market_data_source(trade_date=trade_date)),
        ("fi_pricing", fi_pricing_source(trade_date=trade_date)),
        ("eurex", eurex_source(trade_date=trade_date)),
    ]

    for name, source in steps:
        logger.info("Running dlt source: %s", name)
        info = pipeline.run(source)
        results[name] = str(info)
        logger.info("Finished %s: %s", name, info)

    return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    results = run_all()
    for name, info in results.items():
        print(f"{name}: {info}")
