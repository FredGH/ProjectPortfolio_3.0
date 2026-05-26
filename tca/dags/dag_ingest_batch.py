"""Batch ingestion DAG — runs at 06:45 CET daily.

Sequence: dlt EL sources → dbt staging views → obs freshness check → catalog update.
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
    dag_id="tca_ingest_batch",
    description="dlt EL → dbt staging → obs freshness check",
    schedule="45 5 * * 1-5",  # 06:45 CET = 05:45 UTC, Mon–Fri
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["tca", "ingestion", "daily"],
) as dag:

    def _run_dlt_pipelines(**ctx: dict) -> None:
        import sys
        from datetime import date
        sys.path.insert(0, _DBT_DIR)
        from ingestion.pipelines.run_all import run_all
        trade_date = date.fromisoformat(ctx["ds"])
        run_all(trade_date=trade_date)

    def _seed_auth(**_ctx: dict) -> None:
        import sys
        sys.path.insert(0, _DBT_DIR)
        from ingestion.seed import _seed_auth_clients
        _seed_auth_clients()

    seed_auth = PythonOperator(
        task_id="seed_auth_clients",
        python_callable=_seed_auth,
    )

    run_dlt = PythonOperator(
        task_id="run_dlt_pipelines",
        python_callable=_run_dlt_pipelines,
    )

    _dbt_env = {"HOME": "/home/airflow", "DBT_PROFILES_DIR": _DBT_DIR}

    dbt_staging = BashOperator(
        task_id="dbt_staging",
        bash_command=f"cd {_DBT_DIR} && find dbt_packages -depth -delete 2>/dev/null; dbt deps && dbt run --select staging --target docker",
        env=_dbt_env,
        append_env=True,
    )

    dbt_source_freshness = BashOperator(
        task_id="dbt_source_freshness",
        bash_command=f"cd {_DBT_DIR} && dbt source freshness --target docker",
        env=_dbt_env,
        append_env=True,
    )

    seed_auth >> run_dlt >> dbt_staging >> dbt_source_freshness
