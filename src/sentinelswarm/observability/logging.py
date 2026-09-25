"""Structured logging with correlation-id propagation.

Emits one JSON object per line (production) or a readable console line
(development). A :class:`contextvars.ContextVar` carries the active correlation
id so every log line emitted while handling an event can be traced end-to-end
without threading the id through every function signature.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

# Standard LogRecord attributes we do not want to duplicate in the JSON "extra".
_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime", "taskName"}


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


@contextmanager
def correlation_context(value: str | None) -> Iterator[None]:
    """Bind ``value`` as the correlation id for the duration of the block."""

    token = _correlation_id.set(value)
    try:
        yield
    finally:
        _correlation_id.reset(token)


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        cid = getattr(record, "correlation_id", None)
        if cid:
            payload["correlation_id"] = cid
        # Merge structured "extra" fields.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "correlation_id":
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        cid = getattr(record, "correlation_id", None)
        prefix = f"[{cid}] " if cid else ""
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} "
            f"{record.name}: {prefix}{record.getMessage()}"
        )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Install a single stdout handler with the chosen formatter.

    Idempotent: re-configuring replaces existing handlers so repeated calls in
    tests do not stack duplicate log lines.
    """

    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_CorrelationFilter())
    handler.setFormatter(JsonFormatter() if json_output else ConsoleFormatter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
