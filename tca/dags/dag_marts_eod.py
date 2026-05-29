"""Marts EOD DAG — runs at 18:15 CET daily.

Builds information mart star schemas, updates catalog, triggers MiFID export.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import DagRun
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from airflow.sensors.external_task import ExternalTaskSensor
from airflow.utils.state import DagRunState

from dags.utils.callbacks import on_task_failure
from dags.utils.dbt_metrics import make_dbt_metrics_callback

_DBT_DIR = os.environ.get("DBT_PROJECT_DIR", "/opt/airflow/tca")

default_args = {
    "owner": "tca-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
    "on_failure_callback": on_task_failure,
}

with DAG(
    dag_id="tca_marts_eod",
    description="dbt marts → catalog → MiFID II export generation",
    schedule="15 17 * * 1-5",  # 18:15 CET = 17:15 UTC, Mon–Fri
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["tca", "marts", "eod", "mifid"],
) as dag:

    def _most_recent_biz_vault(dt):
        """Return execution dates of successful tca_biz_vault_eod runs in last 24 h."""
        runs = DagRun.find(dag_id="tca_biz_vault_eod", state=DagRunState.SUCCESS)
        cutoff = dt - timedelta(hours=24)
        dates = [r.execution_date for r in runs if r.execution_date >= cutoff]
        return dates or [dt - timedelta(minutes=45)]

    wait_for_biz_vault = ExternalTaskSensor(
        task_id="wait_for_biz_vault",
        external_dag_id="tca_biz_vault_eod",
        external_task_id="run_analytics_engine",
        execution_date_fn=_most_recent_biz_vault,
        timeout=7200,
        poke_interval=120,
        mode="reschedule",
    )

    _env = {"HOME": "/home/airflow", "DBT_PROFILES_DIR": _DBT_DIR}

    dbt_marts = BashOperator(
        task_id="dbt_marts",
        bash_command=f"cd {_DBT_DIR} && find dbt_packages -depth -delete 2>/dev/null; dbt deps && dbt run --select marts --target docker",
        env=_env,
        append_env=True,
        on_success_callback=make_dbt_metrics_callback("marts"),
    )

    dbt_test_marts = BashOperator(
        task_id="dbt_test_marts",
        bash_command=f"cd {_DBT_DIR} && dbt test --select marts --store-failures --target docker",
        env=_env,
        append_env=True,
        on_success_callback=make_dbt_metrics_callback("marts_tests"),
    )

    def _generate_mifid_export(**ctx: dict) -> None:
        import sys

        sys.path.insert(0, _DBT_DIR)
        from datetime import date as _date

        from reports.mifid_export import generate_mifid_rts27

        generate_mifid_rts27(trade_date=_date.fromisoformat(ctx["ds"]))

    mifid_export = PythonOperator(
        task_id="generate_mifid_export",
        python_callable=_generate_mifid_export,
    )

    def _update_catalog(**ctx: dict) -> None:
        import sys

        import sqlalchemy as sa

        sys.path.insert(0, _DBT_DIR)
        from db import engine

        datasets = [
            ("fact_order_execution", "mart_trading_risk"),
            ("dim_algo", "mart_trading_risk"),
            ("dim_venue", "mart_trading_risk"),
            ("dim_mifid", "mart_trading_risk"),
            ("dim_instrument", "mart_market_data"),
            ("fact_price_benchmark", "mart_market_data"),
            ("dim_client", "mart_corporate"),
            ("fact_client_activity", "mart_corporate"),
        ]
        with engine.begin() as conn:
            for table, schema in datasets:
                conn.execute(
                    sa.text(
                        "INSERT INTO catalog.datasets (table_name, schema_name, last_dbt_run, updated_at) "
                        "VALUES (:t, :s, NOW(), NOW()) "
                        "ON CONFLICT (table_name, schema_name) DO UPDATE "
                        "SET last_dbt_run = excluded.last_dbt_run, updated_at = excluded.updated_at"
                    ),
                    {"t": table, "s": schema},
                )

    update_catalog = PythonOperator(
        task_id="update_catalog",
        python_callable=_update_catalog,
    )

    # Elementary reads test + anomaly history from the elementary schema and
    # writes a report. The || true makes it non-fatal: a missing edr binary or
    # misconfiguration does not block the MiFID export or catalog update.
    elementary_monitor = BashOperator(
        task_id="elementary_monitor",
        bash_command=(
            f"cd {_DBT_DIR} && "
            f"edr monitor --days-back 1 "
            f"--profiles-dir {_DBT_DIR} --project-dir {_DBT_DIR} "
            f"|| true"
        ),
        env=_env,
        append_env=True,
    )

    (
        wait_for_biz_vault
        >> dbt_marts
        >> dbt_test_marts
        >> elementary_monitor
        >> mifid_export
        >> update_catalog
    )
