"""Structured logging shared by every module under ``server``.

Nothing in this package calls the bare ``print``. Every log line goes
through ``structlog.get_logger()``, so it carries the same processors,
levels, and bound context (request id, elapsed time, ...) regardless of
which module emits it.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(log_level: str) -> None:
    """Configure structlog once, at process startup.

    Renders JSON lines when standard error is not a terminal — the case in
    Docker, systemd, or any CI runner — and a colourised human-readable
    format when it is, e.g. running ``uvicorn --reload`` in a dev shell.

    Args:
        log_level: Standard library level name, e.g. ``"INFO"``, ``"DEBUG"``.
            Falls back to ``INFO`` if the name is not recognised, since a
            typo in this one setting should not prevent the server from
            starting.
    """
    numeric_level = logging.getLevelName(log_level.upper())
    level = numeric_level if isinstance(numeric_level, int) else logging.INFO

    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if sys.stderr.isatty()
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
