"""Reusable Airflow callbacks.

Add to any DAG's default_args:
    from dags.utils.callbacks import on_task_failure
    default_args = { ..., "on_failure_callback": on_task_failure }
"""
from __future__ import annotations

import json
import os


def on_task_failure(context: dict) -> None:
    """Emit a CloudWatch metric and a structured JSON log on task failure.

    Emits TCA/Airflow :: TaskFailures (Count=1) with DagId + TaskId dimensions,
    making it directly alarmable in CloudWatch.
    """
    dag_id = context["dag"].dag_id
    task_id = context["task_instance"].task_id
    execution_date = str(context.get("execution_date", ""))
    exception = str(context.get("exception", ""))[:500]

    # Structured JSON — parseable by CloudWatch Logs Insights
    print(
        json.dumps(
            {
                "level": "error",
                "event": "airflow_task_failure",
                "dag_id": dag_id,
                "task_id": task_id,
                "execution_date": execution_date,
                "exception": exception,
            }
        )
    )

    try:
        import boto3

        boto3.client(
            "cloudwatch",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-west-1"),
        ).put_metric_data(
            Namespace="TCA/Airflow",
            MetricData=[
                {
                    "MetricName": "TaskFailures",
                    "Dimensions": [
                        {"Name": "DagId", "Value": dag_id},
                        {"Name": "TaskId", "Value": task_id},
                    ],
                    "Value": 1.0,
                    "Unit": "Count",
                }
            ],
        )
    except Exception as exc:  # never kill the task over a metrics call
        print(json.dumps({"level": "warning", "event": "cloudwatch_put_failed", "error": str(exc)}))
