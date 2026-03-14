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
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar, cast
from uuid import uuid4

import structlog
import structlog.contextvars

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import TelemetryRecordDict, ToolEventDataDict
from trw_mcp.state._paths import find_active_run, resolve_trw_dir
from trw_mcp.state.otel_wrapper import emit_tool_span
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

logger = structlog.get_logger()


P = ParamSpec("P")
T = TypeVar("T")

# --- Run directory cache (RISK-002: avoid N+1 disk reads) ---

_cached_run_dir: tuple[float, Path | None] = (0.0, None)
_RUN_DIR_CACHE_TTL: float = 5.0


def _get_cached_run_dir() -> Path | None:
    """Return cached active run directory, refreshing if TTL expired."""
    global _cached_run_dir
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
        config = get_config()
        if not config.telemetry_enabled:
            return func(*args, **kwargs)

        # FR01 (PRD-CORE-082): Bind correlation ID for structured log tracing.
        # Preserve parent ID for nested tool calls (don't rebind).
        existing_ctx = structlog.contextvars.get_contextvars()
        is_nested = "tool_call_id" in existing_ctx
        if not is_nested:
            tool_call_id = uuid4().hex[:8]
            structlog.contextvars.bind_contextvars(tool_call_id=tool_call_id)

        start = time.monotonic()
        success = True
        error_msg: str | None = None
        result_val: object = None

        try:
            result_val = func(*args, **kwargs)
            return result_val
        except Exception as exc:  # justified: re-raise, telemetry metering decorator
            success = False
            error_msg = str(exc)[:200]
            raise
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            try:
                _write_tool_event(
                    func.__name__, duration_ms, success, error_msg,
                )
            except Exception:  # justified: fail-open telemetry, never blocks tool execution
                logger.debug("telemetry_write_failed", tool=func.__name__)

            # FR04: Session-level telemetry to tool-telemetry.jsonl
            # config.telemetry is a TelemetryConfig Pydantic model (always
            # truthy as an object).  Check its platform_telemetry_enabled
            # field for proper two-tier gating: telemetry_enabled gates
            # basic events (line 73), platform_telemetry_enabled gates
            # detailed records here.  The getattr fallback handles tests
            # that override config.telemetry to a plain bool.
            _tel = config.telemetry
            _detailed = (
                getattr(_tel, "platform_telemetry_enabled", False)
                if not isinstance(_tel, bool)
                else _tel
            )
            if _detailed:
                try:
                    _write_telemetry_record(
                        func.__name__, args, kwargs, duration_ms,
                        result_val if success else None, success,
                    )
                except Exception as exc:  # justified: fail-open telemetry, never blocks tool execution
                    logger.debug("telemetry_write_failed", exc_type=type(exc).__name__)

            # FR01: Clean up correlation ID (only if we bound it)
            if not is_nested:
                structlog.contextvars.unbind_contextvars("tool_call_id")

    return wrapper


def _write_tool_event(
    tool_name: str,
    duration_ms: float,
    success: bool,
    error: str | None,
) -> None:
    """Write a tool_invocation event to events.jsonl or fallback."""
    import os

    agent_id = os.environ.get("TRW_AGENT_ID", "default")
    agent_role = os.environ.get("TRW_AGENT_ROLE", "lead")
    event_data: ToolEventDataDict = {
        "tool_name": tool_name,
        "duration_ms": duration_ms,
        "success": success,
        "agent_id": agent_id,
        "agent_role": agent_role,
    }

    # Include phase from active run state if available
    run_dir = _get_cached_run_dir()
    phase = "unknown"
    if run_dir is not None:
        run_yaml = run_dir / "meta" / "run.yaml"
        if run_yaml.exists():
            try:
                from trw_mcp.state.persistence import FileStateReader

                reader = FileStateReader()
                run_data = reader.read_yaml(run_yaml)
                phase = str(run_data.get("phase", "unknown"))
            except Exception:  # justified: fail-open telemetry, phase read failure uses fallback
                logger.debug("telemetry_phase_read_failed", exc_info=True)
    event_data["phase"] = phase

    if error is not None:
        event_data["error"] = error

    # OTEL span emission (fail-open, gated by config.otel_enabled)
    emit_tool_span(tool_name, duration_ms, {
        "agent_id": agent_id,
        "phase": phase,
    })

    config = get_config()
    writer = FileStateWriter()
    events = FileEventLogger(writer)

    run_dir = _get_cached_run_dir()
    if run_dir is not None:
        events_path = run_dir / "meta" / "events.jsonl"
        if events_path.parent.exists():
            events.log_event(events_path, "tool_invocation", cast(dict[str, object], event_data))
            return

    # Fallback: session-level events
    try:
        trw_dir = resolve_trw_dir()
        context_dir = trw_dir / config.context_dir
        writer.ensure_dir(context_dir)
        fallback = context_dir / "session-events.jsonl"
        events.log_event(fallback, "tool_invocation", cast(dict[str, object], event_data))
    except Exception as exc:  # justified: fail-open telemetry, session-level fallback write
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

    record: TelemetryRecordDict = {
        "tool": tool_name,
        "args_hash": args_hash,
        "duration_ms": duration_ms,
        "result_summary": result_summary,
        "success": success,
    }

    config = get_config()
    writer = FileStateWriter()
    events = FileEventLogger(writer)
    trw_dir = resolve_trw_dir()
    logs_dir = trw_dir / config.logs_dir
    writer.ensure_dir(logs_dir)
    telemetry_path = logs_dir / config.telemetry_file
    events.log_event(telemetry_path, "tool_call", cast(dict[str, object], record))
