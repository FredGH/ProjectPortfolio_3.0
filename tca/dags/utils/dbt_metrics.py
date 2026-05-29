"""Read dbt target/run_results.json and emit CloudWatch custom metrics.

Usage — attach to any dbt BashOperator as on_success_callback:

    from dags.utils.dbt_metrics import make_dbt_metrics_callback

    dbt_staging = BashOperator(
        task_id="dbt_staging",
        bash_command="...",
        on_success_callback=make_dbt_metrics_callback("staging"),
    )

Emits to namespace TCA/dbt with dimensions DagId + Layer:
  - ModelsPassed   (Count)
  - ModelsFailed   (Count)
  - ExecutionSeconds (Seconds)
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path


def make_dbt_metrics_callback(layer: str) -> Callable:
    """Return an Airflow on_success_callback bound to *layer* (e.g. 'staging')."""

    def _callback(context: dict) -> None:
        dbt_dir = os.environ.get("DBT_PROJECT_DIR", "/opt/airflow/tca")
        run_results_path = Path(dbt_dir) / "target" / "run_results.json"

        if not run_results_path.exists():
            print(
                json.dumps(
                    {
                        "level": "warning",
                        "event": "dbt_run_results_missing",
                        "path": str(run_results_path),
                        "layer": layer,
                    }
                )
            )
            return

        with run_results_path.open() as fh:
            data = json.load(fh)

        results = data.get("results", [])
        passed = sum(1 for r in results if r.get("status") in {"success", "pass", "warn"})
        failed = sum(1 for r in results if r.get("status") in {"error", "fail"})
        total_seconds = sum(r.get("execution_time", 0.0) for r in results)

        dag_id = context["dag"].dag_id

        print(
            json.dumps(
                {
                    "level": "info",
                    "event": "dbt_run_metrics",
                    "dag_id": dag_id,
                    "layer": layer,
                    "models_passed": passed,
                    "models_failed": failed,
                    "total_execution_seconds": round(total_seconds, 2),
                }
            )
        )

        try:
            import boto3

            boto3.client(
                "cloudwatch",
                region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-west-1"),
            ).put_metric_data(
                Namespace="TCA/dbt",
                MetricData=[
                    {
                        "MetricName": "ModelsPassed",
                        "Dimensions": [
                            {"Name": "DagId", "Value": dag_id},
                            {"Name": "Layer", "Value": layer},
                        ],
                        "Value": float(passed),
                        "Unit": "Count",
                    },
                    {
                        "MetricName": "ModelsFailed",
                        "Dimensions": [
                            {"Name": "DagId", "Value": dag_id},
                            {"Name": "Layer", "Value": layer},
                        ],
                        "Value": float(failed),
                        "Unit": "Count",
                    },
                    {
                        "MetricName": "ExecutionSeconds",
                        "Dimensions": [
                            {"Name": "DagId", "Value": dag_id},
                            {"Name": "Layer", "Value": layer},
                        ],
                        "Value": total_seconds,
                        "Unit": "Seconds",
                    },
                ],
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "level": "warning",
                        "event": "cloudwatch_put_failed",
                        "error": str(exc),
                    }
                )
            )

    return _callback
