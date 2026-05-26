"""Real-time consumer DAG — continuous, 30-second sensor.

Polls Redis stream pb:fills via custom sensor. When new fills arrive,
runs a lightweight dbt micro-refresh of the biz_vault RT satellites.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.sensors.base import BaseSensorOperator
from airflow.utils.context import Context

_DBT_DIR = os.environ.get("DBT_PROJECT_DIR", "/opt/airflow/tca")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

default_args = {
    "owner": "tca-platform",
    "retries": 0,
    "email_on_failure": False,
}


class RedisStreamSensor(BaseSensorOperator):
    """Pokes the Redis stream pb:fills for new entries since last run."""

    def __init__(self, stream_key: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.stream_key = stream_key

    def poke(self, context: Context) -> bool:
        import redis as _redis

        r = _redis.from_url(_REDIS_URL)
        try:
            result = r.xlen(self.stream_key)
            return result > 0
        except Exception:
            return False


with DAG(
    dag_id="tca_rt_consumer",
    description="Redis stream sensor → biz_vault micro-refresh (30s polling)",
    schedule=timedelta(seconds=30),
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    max_active_runs=1,
    tags=["tca", "realtime", "continuous"],
) as dag:

    wait_for_fills = RedisStreamSensor(
        task_id="wait_for_redis_fills",
        stream_key="pb:fills",
        poke_interval=30,
        timeout=60,
        mode="reschedule",
        soft_fail=True,
    )

    def _process_rt_fills(**_ctx: dict) -> None:
        import sys

        sys.path.insert(0, _DBT_DIR)
        import subprocess

        subprocess.run(
            [
                "dbt",
                "run",
                "--select",
                "biz_vault.bv_order_enriched",
                "--target",
                "docker",
            ],
            cwd=_DBT_DIR,
            env={**os.environ, "HOME": "/root", "DBT_PROFILES_DIR": _DBT_DIR},
            check=True,
        )

    process_fills = PythonOperator(
        task_id="process_rt_fills",
        python_callable=_process_rt_fills,
    )

    wait_for_fills >> process_fills
