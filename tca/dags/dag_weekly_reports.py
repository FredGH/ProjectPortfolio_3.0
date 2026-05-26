"""Weekly reports DAG — Monday 07:00 CET.

Generates: algo performance digest, trader attribution report, venue scorecard.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

_DBT_DIR = os.environ.get("DBT_PROJECT_DIR", "/opt/airflow/tca")

default_args = {
    "owner": "tca-platform",
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "email_on_failure": False,
}

with DAG(
    dag_id="tca_weekly_reports",
    description="Weekly: algo digest + trader attribution + venue scorecard",
    schedule="0 6 * * 1",  # Monday 07:00 CET = 06:00 UTC
    start_date=datetime(2025, 1, 6),
    catchup=False,
    default_args=default_args,
    tags=["tca", "reports", "weekly"],
) as dag:

    def _algo_digest(**ctx: dict) -> None:
        import sys

        sys.path.insert(0, _DBT_DIR)
        from datetime import date as _date

        from reports.algo_digest import generate_algo_digest

        generate_algo_digest(week_ending=_date.fromisoformat(ctx["ds"]))

    def _trader_digest(**ctx: dict) -> None:
        import sys

        sys.path.insert(0, _DBT_DIR)
        from datetime import date as _date

        from reports.trader_digest import generate_trader_digest

        generate_trader_digest(week_ending=_date.fromisoformat(ctx["ds"]))

    def _venue_scorecard(**ctx: dict) -> None:
        import sys

        sys.path.insert(0, _DBT_DIR)
        from datetime import date as _date

        from reports.order_tca_report import generate_venue_scorecard

        generate_venue_scorecard(week_ending=_date.fromisoformat(ctx["ds"]))

    algo_digest = PythonOperator(task_id="algo_digest", python_callable=_algo_digest)
    trader_digest = PythonOperator(
        task_id="trader_digest", python_callable=_trader_digest
    )
    venue_scorecard = PythonOperator(
        task_id="venue_scorecard", python_callable=_venue_scorecard
    )

    # Run report generators in parallel
    [algo_digest, trader_digest, venue_scorecard]
