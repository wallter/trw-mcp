"""Tool invocation telemetry decorator — PRD-CORE-031-FR02.

Provides @log_tool_call that times MCP tool calls, writes
tool_invocation events to events.jsonl, and optionally writes
detailed records to .trw/logs/tool-telemetry.jsonl.

Import safety: This module MUST only import from state/, models/,
and stdlib (RISK-004 circular import prevention).
"""

from __future__ import annotations

import functools
import hashlib
import time
from pathlib import Path
from typing import Callable, ParamSpec, TypeVar

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import find_active_run, resolve_trw_dir
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

logger = structlog.get_logger()

_config = get_config()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)

P = ParamSpec("P")
T = TypeVar("T")

# --- Run directory cache (RISK-002: avoid N+1 disk reads) ---

_cached_run_dir: tuple[float, Path | None] = (0.0, None)
_RUN_DIR_CACHE_TTL: float = 5.0


def _get_cached_run_dir() -> Path | None:
    """Return cached active run directory, refreshing if TTL expired."""
    global _cached_run_dir  # noqa: PLW0603
    now = time.monotonic()
    ts, run_dir = _cached_run_dir
    if now - ts < _RUN_DIR_CACHE_TTL:
        return run_dir
    run_dir = find_active_run()
    _cached_run_dir = (now, run_dir)
    return run_dir


def log_tool_call(func: Callable[P, T]) -> Callable[P, T]:
    """Wrap an MCP tool function to emit tool_invocation events.

    - Times the call using time.monotonic()
    - Writes tool_invocation event to active run's events.jsonl
    - Falls back to .trw/context/session-events.jsonl when no run
    - Gated by config.telemetry_enabled (zero overhead when False)
    - Fail-open: exceptions in event writing are caught silently

    Args:
        func: The MCP tool function to wrap.

    Returns:
        Wrapped function with identical signature.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        if not _config.telemetry_enabled:
            return func(*args, **kwargs)

        start = time.monotonic()
        success = True
        error_msg: str | None = None
        result_val: object = None

        try:
            result_val = func(*args, **kwargs)
            return result_val
        except Exception as exc:
            success = False
            error_msg = str(exc)[:200]
            raise
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            try:
                _write_tool_event(
                    func.__name__, duration_ms, success, error_msg,
                )
            except Exception:
                logger.debug("telemetry_write_failed", tool=func.__name__)

            # FR04: Session-level telemetry to tool-telemetry.jsonl
            if _config.telemetry:
                try:
                    _write_telemetry_record(
                        func.__name__, args, kwargs, duration_ms,
                        result_val if success else None, success,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("telemetry_write_failed", exc_type=type(exc).__name__)

    return wrapper


def _write_tool_event(
    tool_name: str,
    duration_ms: float,
    success: bool,
    error: str | None,
) -> None:
    """Write a tool_invocation event to events.jsonl or fallback."""
    event_data: dict[str, object] = {
        "tool_name": tool_name,
        "duration_ms": duration_ms,
        "success": success,
    }
    if error is not None:
        event_data["error"] = error

    run_dir = _get_cached_run_dir()
    if run_dir is not None:
        events_path = run_dir / "meta" / "events.jsonl"
        if events_path.parent.exists():
            _events.log_event(events_path, "tool_invocation", event_data)
            return

    # Fallback: session-level events
    try:
        trw_dir = resolve_trw_dir()
        context_dir = trw_dir / _config.context_dir
        _writer.ensure_dir(context_dir)
        fallback = context_dir / "session-events.jsonl"
        _events.log_event(fallback, "tool_invocation", event_data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("telemetry_write_failed", exc_type=type(exc).__name__)


def _write_telemetry_record(
    tool_name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    duration_ms: float,
    result: object,
    success: bool,
) -> None:
    """Write detailed telemetry record to .trw/logs/tool-telemetry.jsonl (FR04)."""
    args_repr = repr(args) + repr(kwargs)
    args_hash = hashlib.sha256(args_repr.encode()).hexdigest()[:8]
    result_summary = repr(result)[:100] if result is not None else ""

    record: dict[str, object] = {
        "tool": tool_name,
        "args_hash": args_hash,
        "duration_ms": duration_ms,
        "result_summary": result_summary,
        "success": success,
    }

    trw_dir = resolve_trw_dir()
    logs_dir = trw_dir / _config.logs_dir
    _writer.ensure_dir(logs_dir)
    telemetry_path = logs_dir / _config.telemetry_file
    _events.log_event(telemetry_path, "tool_call", record)
