"""Observability, correlation tracking, and structured logging utilities.
"""

from __future__ import annotations

from contextvars import ContextVar
import logging
import sys
from typing import Any, Optional

# Context variable to hold the correlation ID for the current request / task
correlation_id_ctx: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
trace_id_ctx: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


class StructuredLogFormatter(logging.Formatter):
    """Formats log records with correlation ID, trace ID, and standard ISO timestamps."""

    def format(self, record: logging.LogRecord) -> str:
        record.correlation_id = correlation_id_ctx.get() or "-"
        record.trace_id = trace_id_ctx.get() or "-"
        return super().format(record)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure standard root logger with correlation formatting."""
    handler = logging.StreamHandler(sys.stdout)
    formatter = StructuredLogFormatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] [corr=%(correlation_id)s trace=%(trace_id)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Remove existing handlers to avoid duplicate logging
    root.handlers = [handler]


def get_logger(name: str) -> logging.Logger:
    """Retrieve logger instance for a given module."""
    return logging.getLogger(name)

