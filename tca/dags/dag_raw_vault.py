"""Raw Vault DAG — runs at 07:15 CET daily (after dag_ingest_batch completes).

Builds all DV2 Hubs, Links, and Satellites incrementally.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from urllib.parse import urlparse as _urlparse

from airflow import DAG
from airflow.models import DagRun
from airflow.operators.bash import BashOperator
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
    dag_id="tca_raw_vault",
    description="dbt raw_vault layer (Hubs + Links + Satellites)",
    schedule="15 6 * * 1-5",  # 07:15 CET = 06:15 UTC, Mon–Fri
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["tca", "raw_vault", "daily"],
) as dag:

    def _most_recent_ingest(dt):
        """Return execution dates of successful tca_ingest_batch runs in last 24 h.
        Handles both scheduled runs (different fixed offsets) and manual triggers."""
        runs = DagRun.find(dag_id="tca_ingest_batch", state=DagRunState.SUCCESS)
        cutoff = dt - timedelta(hours=24)
        dates = [r.execution_date for r in runs if r.execution_date >= cutoff]
        return dates or [dt - timedelta(minutes=30)]

    wait_for_ingest = ExternalTaskSensor(
        task_id="wait_for_ingest",
        external_dag_id="tca_ingest_batch",
        external_task_id="dbt_source_freshness",
        execution_date_fn=_most_recent_ingest,
        timeout=3600,
        poke_interval=60,
        mode="reschedule",
    )

    def _make_dbt_env() -> dict:
        env = {"HOME": "/home/airflow", "DBT_PROFILES_DIR": _DBT_DIR}
        db_url = os.environ.get("DATABASE_URL", "")
        if db_url:
            u = _urlparse(db_url)
            env.update({
                "POSTGRES_HOST": u.hostname or "",
                "POSTGRES_USER": u.username or "tca_user",
                "POSTGRES_PASSWORD": u.password or "",
                "POSTGRES_DB": (u.path or "").lstrip("/") or "tca_db",
            })
        return env

    _env = _make_dbt_env()
    _dbt_target = "prod" if os.environ.get("DATABASE_URL") else "docker"

    dbt_hubs = BashOperator(
        task_id="dbt_hubs",
        bash_command=f"cd {_DBT_DIR} && find dbt_packages -depth -delete 2>/dev/null; dbt deps && dbt run --select raw_vault.hubs --target {_dbt_target}",
        env=_env,
        append_env=True,
        on_success_callback=make_dbt_metrics_callback("raw_vault"),
    )

    dbt_links = BashOperator(
        task_id="dbt_links",
        bash_command=f"cd {_DBT_DIR} && dbt run --select raw_vault.links --target {_dbt_target}",
        env=_env,
        append_env=True,
    )

    dbt_satellites = BashOperator(
        task_id="dbt_satellites",
        bash_command=f"cd {_DBT_DIR} && dbt run --select raw_vault.satellites --target {_dbt_target}",
        env=_env,
        append_env=True,
    )

    dbt_test_raw_vault = BashOperator(
        task_id="dbt_test_raw_vault",
        bash_command=f"cd {_DBT_DIR} && dbt test --select raw_vault --store-failures --target {_dbt_target}",
        env=_env,
        append_env=True,
        on_success_callback=make_dbt_metrics_callback("raw_vault_tests"),
    )

    wait_for_ingest >> dbt_hubs >> dbt_links >> dbt_satellites >> dbt_test_raw_vault
