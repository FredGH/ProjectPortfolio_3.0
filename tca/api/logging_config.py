"""Configure structlog for JSON output compatible with CloudWatch Logs Insights.

Every log line is a single JSON object, making it queryable with:
  fields @timestamp, level, event, method, path, status, duration_ms
  | filter level = "error"
"""
from __future__ import annotations

import logging
import sys

import structlog


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_logger_name,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
