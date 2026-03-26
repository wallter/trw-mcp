"""Structured logging configuration for trw-mcp.

Follows TRW FRAMEWORK.md spec: JSONL with ts, level, component, op, outcome.
Provides a single ``configure_logging()`` entry point used by the CLI and
server startup. All other modules use ``structlog.get_logger(__name__)``.

Environment variables (checked in order):
    TRW_LOG_LEVEL   — explicit level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    LOG_LEVEL        — fallback for generic deployments
    TRW_LOG_FORMAT   — "json" (default) or "console" for dev-friendly output
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import MutableMapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

# Sensitive key patterns for redaction
_SENSITIVE_PATTERNS: frozenset[str] = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "private_key",
    "access_key",
    "session_id",
})

_SENSITIVE_VALUE_RE = re.compile(
    r"((?:Bearer|Basic|Token)\s+)\S+",
    re.IGNORECASE,
)

# Noisy third-party loggers to suppress below WARNING
_NOISY_LOGGERS: tuple[str, ...] = (
    "fastmcp",
    "redis",
    "redis.asyncio",
    "redis.connection",
    "httpcore",
    "httpx",
    "asyncio",
    "urllib3",
    "uvicorn.access",
    "watchfiles",
)


def _redact_secrets(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Redact values of keys that match sensitive patterns."""
    for key in list(event_dict):
        key_lower = key.lower()
        if any(pat in key_lower for pat in _SENSITIVE_PATTERNS):
            event_dict[key] = "***REDACTED***"
        elif isinstance(event_dict[key], str):
            event_dict[key] = _SENSITIVE_VALUE_RE.sub(
                r"\1***REDACTED***", event_dict[key]
            )
    return event_dict


def _add_component(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Add ``component`` field from the logger name for FRAMEWORK.md compliance."""
    logger_name = event_dict.get("_logger_name") or event_dict.get("logger")
    if logger_name and "component" not in event_dict:
        # Extract short component from module path: trw_mcp.tools.learning -> tools.learning
        parts = str(logger_name).split(".")
        if len(parts) > 1 and parts[0] in ("trw_mcp", "trw_memory", "trw_eval", "app"):
            event_dict["component"] = ".".join(parts[1:])
        else:
            event_dict["component"] = str(logger_name)
    return event_dict


def _verbosity_to_level(verbosity: int) -> int:
    """Map CLI verbosity count to logging level.

    -q / no flags  -> WARNING (30)
    default (0)    -> INFO (20)
    -v             -> DEBUG (10)
    -vv / -vvv     -> DEBUG (10, same — Python has no TRACE)
    """
    if verbosity < 0:
        return logging.WARNING
    return {0: logging.INFO, 1: logging.DEBUG}.get(verbosity, logging.DEBUG)


def _resolve_log_level(
    *,
    verbosity: int = 0,
    debug: bool = False,
    explicit_level: str | None = None,
) -> int:
    """Resolve the effective log level from multiple sources.

    Priority: explicit_level > env TRW_LOG_LEVEL > env LOG_LEVEL > debug flag > verbosity.
    """
    if explicit_level:
        return getattr(logging, explicit_level.upper(), logging.INFO)

    env_level = os.environ.get("TRW_LOG_LEVEL") or os.environ.get("LOG_LEVEL")
    if env_level:
        return getattr(logging, env_level.upper(), logging.INFO)

    if debug:
        return logging.DEBUG

    return _verbosity_to_level(verbosity)


def configure_logging(
    *,
    debug: bool = False,
    verbosity: int = 0,
    log_level: str | None = None,
    json_output: bool | None = None,
    log_file: Path | None = None,
    log_dir: Path | None = None,
    package_name: str = "trw-mcp",
    suppress_noisy: bool = True,
) -> None:
    """Configure structlog processors and stdlib logging for the entire process.

    This is the single source of truth for logging configuration. Call it once
    at application startup (CLI main, server init, test fixtures).

    Args:
        debug: Legacy flag — equivalent to verbosity=1 + file logging.
        verbosity: CLI verbosity level (0=INFO, 1=DEBUG, 2+=DEBUG).
        log_level: Explicit level override (e.g. "WARNING"). Takes precedence
            over debug/verbosity and environment variables.
        json_output: Force JSON (True) or console (False) output. None=auto
            (JSON if stderr is not a TTY).
        log_file: Explicit log file path. Mutually exclusive with log_dir.
        log_dir: Directory for auto-named log files (``{package}-YYYY-MM-DD.jsonl``).
            Created if it doesn't exist.
        package_name: Package identifier for log file naming.
        suppress_noisy: Suppress noisy third-party loggers below WARNING.
    """
    level = _resolve_log_level(debug=debug, verbosity=verbosity, explicit_level=log_level)

    # Auto-detect output format
    if json_output is None:
        use_json = not sys.stderr.isatty()
    else:
        use_json = json_output

    # Build processor pipeline
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_component,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_secrets,
    ]

    # Choose renderer
    if use_json:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(
            colors=sys.stderr.isatty(),
        )

    # Build stdlib handlers
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

    # File logging
    effective_log_file = log_file
    if effective_log_file is None and (log_dir or debug):
        if log_dir is None and debug:
            log_dir = Path.cwd() / ".trw" / "logs"
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            effective_log_file = log_dir / f"{package_name}-{today}.jsonl"

    if effective_log_file is not None:
        effective_log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(effective_log_file), encoding="utf-8")

        # File handler always gets JSON, regardless of console format
        class _JsonOnlyFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                msg = str(record.getMessage())
                return msg.startswith(("{", "["))

        file_handler.addFilter(_JsonOnlyFilter())
        handlers.append(file_handler)

    # Suppress noisy third-party loggers
    if suppress_noisy:
        for logger_name in _NOISY_LOGGERS:
            logging.getLogger(logger_name).setLevel(logging.WARNING)

    # Configure stdlib logging
    logging.basicConfig(
        format="%(message)s",
        level=level,
        handlers=handlers,
        force=True,
    )

    # Configure structlog
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    # Bind service version to all log records for incident triage
    try:
        from importlib.metadata import version as _get_version

        structlog.contextvars.bind_contextvars(
            service_version=_get_version("trw-mcp"),
        )
    except Exception:  # justified: best-effort — version binding is non-critical
        pass
