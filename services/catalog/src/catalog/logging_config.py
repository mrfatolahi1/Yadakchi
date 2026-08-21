"""Structured JSON logging to stdout. Never print()."""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "taskName", "thread", "threadName",
    }
)  # fmt: skip


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Anything passed through ``extra=`` rides along at the top level, which is
    how ``trace_id`` reaches the log line for every event we handle.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, tz=dt.UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "service": "catalog",
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)
