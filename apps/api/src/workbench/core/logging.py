"""Structured logging.

JSON lines in every environment, so an air-gapped operator can grep the logs
with the same commands they would use against a log aggregator they do not
have. Request, run and actor identifiers are bound into a context variable and
attached to every event automatically.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars

from workbench.core.redaction import redact


def _redact_event(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Apply the same redaction rules to logs as to the audit trail."""
    for key, value in list(event_dict.items()):
        if key in {"event", "level", "timestamp", "logger"}:
            continue
        event_dict[key] = redact(value)
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Install the logging configuration. Call once, at startup."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    # Uvicorn installs its own noisy handlers; route them through structlog.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    processors: list[structlog.types.Processor] = [
        merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_event,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to the current request context."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def bind_request_context(**values: Any) -> None:
    """Attach identifiers to every subsequent log line in this task."""
    bind_contextvars(**values)


def clear_request_context() -> None:
    clear_contextvars()
