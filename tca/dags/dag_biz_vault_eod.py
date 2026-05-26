"""Business Vault EOD DAG — runs at 17:30 CET daily.

Ingests FI pricing + Eurex EDSP settlements, then builds the biz_vault layer.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

_DBT_DIR = os.environ.get("DBT_PROJECT_DIR", "/opt/airflow/tca")

default_args = {
    "owner": "tca-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}

with DAG(
    dag_id="tca_biz_vault_eod",
    description="FI + Eurex EL → dbt biz_vault → observability checks",
    schedule="30 16 * * 1-5",  # 17:30 CET = 16:30 UTC, Mon–Fri
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["tca", "biz_vault", "eod"],
) as dag:

    def _run_fi_and_eurex(**ctx: dict) -> None:
        import sys

        sys.path.insert(0, _DBT_DIR)
        import os as _os

        import dlt

        trade_date = ctx["ds"]
        pipeline = dlt.pipeline(
            pipeline_name="tca_eod",
            destination=dlt.destinations.postgres(
                credentials=_os.environ["DATABASE_URL"]
            ),
            dataset_name="stg_raw",
        )
        from datetime import date as _date

        from ingestion.sources.eurex_source import eurex_source
        from ingestion.sources.fi_pricing_source import fi_pricing_source

        pipeline.run(fi_pricing_source(trade_date=_date.fromisoformat(trade_date)))
        pipeline.run(eurex_source(trade_date=_date.fromisoformat(trade_date)))

    ingest_eod = PythonOperator(
        task_id="ingest_fi_eurex",
        python_callable=_run_fi_and_eurex,
    )

    _env = {"HOME": "/home/airflow", "DBT_PROFILES_DIR": _DBT_DIR}

    dbt_biz_vault = BashOperator(
        task_id="dbt_biz_vault",
        bash_command=f"cd {_DBT_DIR} && find dbt_packages -depth -delete 2>/dev/null; dbt deps && dbt run --select biz_vault --target docker",
        env=_env,
        append_env=True,
    )

    dbt_test_biz_vault = BashOperator(
        task_id="dbt_test_biz_vault",
        bash_command=f"cd {_DBT_DIR} && dbt test --select biz_vault --target docker",
        env=_env,
        append_env=True,
    )

    def _run_analytics(**ctx: dict) -> None:
        import sys

        sys.path.insert(0, _DBT_DIR)
        from datetime import date as _date

        from analytics.engine import AnalyticsEngine

        trade_date = _date.fromisoformat(ctx["ds"])
        AnalyticsEngine().run(trade_date=trade_date)

    run_analytics = PythonOperator(
        task_id="run_analytics_engine",
        python_callable=_run_analytics,
    )

    ingest_eod >> dbt_biz_vault >> dbt_test_biz_vault >> run_analytics
