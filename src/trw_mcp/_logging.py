"""Structured logging configuration for trw-mcp.

Follows TRW FRAMEWORK.md spec: JSONL with ts, level, component, op, outcome.
Provides a single ``configure_logging()`` entry point used by the CLI and
server startup. All other modules use ``structlog.get_logger(__name__)``.

Verbosity precedence (first match wins):
    1. ``log_level=`` argument (the ``--log-level`` CLI flag)
    2. ``TRW_LOG_LEVEL`` env var, then ``LOG_LEVEL``
    3. ``debug=`` argument (the ``--debug`` CLI flag)
    4. ``verbosity=`` argument (``-v`` / ``-q``)
    5. ``.trw/config.yaml`` ``debug: true`` — the operator-facing toggle
    6. INFO

Client bootstrap profiles never bake ``--debug`` into a generated MCP server
entry (see ``bootstrap/_utils.py``); ``.trw/config.yaml::debug`` is the single
portable way to turn verbose logging on for every client alike.

Other environment variables:
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
_SENSITIVE_PATTERNS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "client_secret",
        "token",
        "refresh_token",
        "id_token",
        "jwt",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "private_key",
        "access_key",
        "session_id",
    }
)

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
    "sentence_transformers",
    "huggingface_hub",
    "torch",
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
            event_dict[key] = _SENSITIVE_VALUE_RE.sub(r"\1***REDACTED***", event_dict[key])
    return event_dict


def _add_otel_context(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Inject OpenTelemetry trace/span IDs into log events when available.

    Fail-open: if opentelemetry is not installed or the current span is not
    recording, the event dict is returned unchanged.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    except (ImportError, AttributeError):
        pass
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


def _env_log_level() -> str | None:
    """Return the level name requested by env vars, or None."""
    return os.environ.get("TRW_LOG_LEVEL") or os.environ.get("LOG_LEVEL")


def _resolve_log_level(
    *,
    verbosity: int = 0,
    debug: bool = False,
    explicit_level: str | None = None,
    config_debug: bool = False,
) -> int:
    """Resolve the effective log level from multiple sources.

    Priority: explicit_level > env TRW_LOG_LEVEL > env LOG_LEVEL > debug flag >
    explicit verbosity (``-v``/``-q``) > config_debug > INFO.

    *config_debug* carries ``.trw/config.yaml``'s ``debug`` key. It sits below
    every explicit caller-supplied signal so a ``--log-level``/``--quiet``
    invocation is never silently overridden by an on-disk default, and above
    the bare INFO default so an operator who sets ``debug: true`` gets DEBUG
    without editing any client's MCP config.
    """
    if explicit_level:
        return getattr(logging, explicit_level.upper(), logging.INFO)

    env_level = _env_log_level()
    if env_level:
        return getattr(logging, env_level.upper(), logging.INFO)

    if debug:
        return logging.DEBUG

    # verbosity 0 means "caller said nothing" — fall through to config.
    if verbosity != 0:
        return _verbosity_to_level(verbosity)

    if config_debug:
        return logging.DEBUG

    return _verbosity_to_level(0)


def _config_debug_requested(
    *,
    verbosity: int,
    debug: bool,
    explicit_level: str | None,
) -> bool:
    """Return ``.trw/config.yaml``'s ``debug`` value, or False if it can't decide.

    Reads config ONLY when no more-explicit source has already fixed the level.
    Two reasons this is gated rather than unconditional:

    1. Precedence — an explicit ``--log-level`` / ``TRW_LOG_LEVEL`` / ``--debug``
       / ``-v`` / ``-q`` must win, so the config value would be discarded anyway.
    2. Import safety — ``trw_mcp.server.__init__`` calls ``configure_logging``
       at package-import time (with an explicit level), long before config load
       is safe. That call short-circuits here and never touches ``get_config``.

    Fail-open: any failure to load config yields False (INFO), never a crash in
    the logging bootstrap.
    """
    if explicit_level or debug or verbosity != 0 or _env_log_level():
        return False
    try:
        from trw_mcp.models.config import get_config

        return bool(get_config().debug)
    except Exception:  # justified: logging bootstrap must never fail on config load
        return False


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
        debug: The ``--debug`` CLI flag — DEBUG level + file logging. When it
            is False and nothing else fixes the level, ``.trw/config.yaml``'s
            ``debug`` key is consulted and has the identical effect.
        verbosity: CLI verbosity level (0=unset, 1=DEBUG, 2+=DEBUG, -1=WARNING).
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
    config_debug = _config_debug_requested(verbosity=verbosity, debug=debug, explicit_level=log_level)
    level = _resolve_log_level(
        debug=debug,
        verbosity=verbosity,
        explicit_level=log_level,
        config_debug=config_debug,
    )
    # ``.trw/config.yaml::debug`` is fully equivalent to ``--debug``: it must
    # also open the file sink, or the events it enables would exist only on a
    # stderr stream the MCP client discards.
    debug = debug or config_debug

    # Auto-detect output format
    if json_output is None:
        use_json = not sys.stderr.isatty()
    else:
        use_json = json_output

    # Build processor pipeline
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_otel_context,
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
    except Exception:  # justified: fail-open, version binding is non-critical logging metadata
        structlog.get_logger(__name__).debug("logging_service_version_bind_failed", exc_info=True)
