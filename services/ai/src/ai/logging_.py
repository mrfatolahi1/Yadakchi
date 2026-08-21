"""Structured JSON logging to stdout. Never `print()`.

One rule specific to this service: **titles are business data**. A seller's
listing title, a prompt built from it, and a model's answer are never written
at INFO. Above DEBUG we log a length and a digest, which is enough to correlate
a slow call with a cache entry and useless to anyone reading the log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, UTC, with `extra=` fields merged in."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "ai",
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root handler. Idempotent — safe to call per app instance."""
    handler = logging.StreamHandler(stream=sys.stdout)
    if fmt.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s"))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own colourised handlers; route them through ours so
    # every line on stdout is one JSON object.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def text_preview(text: str) -> dict[str, Any]:
    """A safe stand-in for a title or a prompt: shape, not content."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return {"chars": len(text), "digest": digest}


def is_debug(logger: logging.Logger) -> bool:
    """Full prompt content is logged only when this is true."""
    return logger.isEnabledFor(logging.DEBUG)
