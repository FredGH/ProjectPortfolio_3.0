from __future__ import annotations

import logging
import os
from datetime import date

import pandas as pd
import sqlalchemy as sa

from analytics.modules.adverse_selection import AdverseSelection
from analytics.modules.cost_decomposition import CostDecomposition
from analytics.modules.eurex_derivatives import EurexDerivatives
from analytics.modules.fill_pattern import FillPattern
from analytics.modules.fixed_income_tca import FixedIncomeTCA
from analytics.modules.fx_derivatives_tca import FXDerivativesTCA
from analytics.modules.mifid_compliance import MifidCompliance
from analytics.modules.peer_benchmarking import PeerBenchmarking
from analytics.modules.pre_trade import PreTrade
from analytics.modules.venue_sor import VenueSOR
from analytics.observability.anomaly_detector import AnomalyDetector
from analytics.observability.quarantine import Quarantine

logger = logging.getLogger(__name__)


class AnalyticsEngine:
    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or os.environ["DATABASE_URL"]
        self._engine = sa.create_engine(self._db_url)
        self._quarantine = Quarantine(self._engine)
        self._anomaly = AnomalyDetector(self._engine, quarantine=self._quarantine)

        self._modules = {
            "cost_decomposition": CostDecomposition(),
            "adverse_selection": AdverseSelection(),
            "venue_sor": VenueSOR(),
            "fill_pattern": FillPattern(),
            "peer_benchmarking": PeerBenchmarking(),
            "eurex_derivatives": EurexDerivatives(),
            "fixed_income_tca": FixedIncomeTCA(),
            "fx_derivatives_tca": FXDerivativesTCA(),
            "mifid_compliance": MifidCompliance(),
            "pre_trade": PreTrade(),
        }

    def run(self, trade_date: date) -> dict[str, pd.DataFrame]:
        logger.info("Analytics engine running for trade_date=%s", trade_date)

        orders_df = self._load_orders(trade_date)
        fills_df = self._load_fills(trade_date)
        benchmarks_df = self._load_benchmarks(trade_date)

        logger.info("Loaded %d orders, %d fills", len(orders_df), len(fills_df))

        # Observability: anomaly detection on incoming data
        anomalies = self._anomaly.check(orders_df, fills_df)
        if anomalies:
            self._quarantine.write_warnings(anomalies, trade_date)

        results: dict[str, pd.DataFrame] = {}
        for name, module in self._modules.items():
            try:
                results[name] = module.run(orders_df, fills_df, benchmarks_df)
                logger.info("Module %s: %d rows", name, len(results[name]))
            except Exception as exc:
                logger.error("Module %s failed: %s", name, exc, exc_info=True)
                results[name] = pd.DataFrame()

        return results

    def _load_orders(self, trade_date: date) -> pd.DataFrame:
        sql = """
            SELECT * FROM biz_vault.bv_order_enriched
            WHERE trade_date = :trade_date
        """
        return pd.read_sql(
            sa.text(sql), self._engine, params={"trade_date": trade_date}
        )

    def _load_fills(self, trade_date: date) -> pd.DataFrame:
        sql = """
            SELECT * FROM raw_vault.sat_fill_execution
            WHERE trade_date = :trade_date
        """
        return pd.read_sql(
            sa.text(sql), self._engine, params={"trade_date": trade_date}
        )

    def _load_benchmarks(self, trade_date: date) -> pd.DataFrame:
        sql = """
            SELECT * FROM mart_market_data.fact_price_benchmark
            WHERE price_date = :trade_date
        """
        return pd.read_sql(
            sa.text(sql), self._engine, params={"trade_date": trade_date}
        )


if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2025-01-15")
    args = parser.parse_args()

    engine = AnalyticsEngine()
    results = engine.run(date.fromisoformat(args.date))
    for module, df in results.items():
        print(f"  {module}: {len(df)} rows")
