from __future__ import annotations

import json
import logging
from datetime import date
from typing import TYPE_CHECKING

import sqlalchemy as sa

if TYPE_CHECKING:
    from analytics.observability.anomaly_detector import AnomalyWarning

logger = logging.getLogger(__name__)

_INSERT_WARNING = sa.text(
    """
    INSERT INTO obs.obs_warnings
        (check_name, affected_table, affected_rows, warn_value, warn_time)
    VALUES
        (:check_name, :affected_table, :affected_rows, :warn_value, :warn_time)
"""
)

_INSERT_QUARANTINE = sa.text(
    """
    INSERT INTO obs.quarantine_queue
        (record_id, source_table, failed_check, severity, original_payload, quarantine_reason)
    VALUES
        (:record_id, :source_table, :failed_check, :severity, CAST(:original_payload AS jsonb), :quarantine_reason)
"""
)


class Quarantine:
    """Routes anomalies and failed records to obs.quarantine_queue."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def write_warnings(self, warnings: list[AnomalyWarning], trade_date: date) -> None:
        if not warnings:
            return
        rows = [
            {
                "check_name": w.check_name,
                "affected_table": w.affected_table,
                "affected_rows": w.affected_rows,
                "warn_value": w.warn_value,
                "warn_time": w.warn_time,
            }
            for w in warnings
        ]
        try:
            with self._engine.begin() as conn:
                conn.execute(_INSERT_WARNING, rows)
            logger.info("Wrote %d anomaly warnings for %s", len(warnings), trade_date)
        except Exception as exc:
            logger.error("Failed to write warnings: %s", exc)

    def quarantine_record(
        self,
        record_id: str,
        source_table: str,
        failed_check: str,
        severity: str,
        payload: dict,
        reason: str,
    ) -> None:
        assert severity in ("hard", "soft"), f"Invalid severity: {severity}"
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    _INSERT_QUARANTINE,
                    {
                        "record_id": record_id,
                        "source_table": source_table,
                        "failed_check": failed_check,
                        "severity": severity,
                        "original_payload": json.dumps(payload, default=str),
                        "quarantine_reason": reason,
                    },
                )
        except Exception as exc:
            logger.error("Failed to quarantine record %s: %s", record_id, exc)
