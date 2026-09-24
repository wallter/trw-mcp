"""The local side of a tool call: the run-log row, the trace ids and the OTEL span.

Belongs to the ``tool_call_timing`` facade, whose wrapper is the one per-call producer
(PRD-FIX-150). The row lands in the run's ``meta/events.jsonl`` (or ``.trw/context/
session-events.jsonl`` when no run is pinned) as ``{"event": "tool_call", "tool_name", "success",
...}``; ceremony scoring, tier scoring, the deferred learning step and the stop hooks read it there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import structlog
import structlog.contextvars

logger = structlog.get_logger("trw_mcp.telemetry.tool_call_timing")


@dataclass(frozen=True, slots=True)
class TraceBinding:
    """What ``bind_trace_ids`` changed, so ``unbind_trace_ids`` restores exactly that."""

    owns_call_id: bool
    parent_event_id: str | None


def bind_trace_ids(trace_event_id: str) -> TraceBinding:
    """Bind ``tool_call_id`` (outermost call only) and ``tool_trace_event_id`` for the call's logs.

    Code inside a tool reads ``tool_call_id`` to correlate its own async work with the call
    (build/_registration.py). A nested tool call keeps its parent's id.
    """
    existing = structlog.contextvars.get_contextvars()
    owns_call_id = "tool_call_id" not in existing
    parent = existing.get("tool_trace_event_id")
    # One bind for both ids, so a failure cannot leave a half-bound call id for later calls to inherit.
    ids = {"tool_trace_event_id": trace_event_id, **({"tool_call_id": uuid4().hex[:8]} if owns_call_id else {})}
    structlog.contextvars.bind_contextvars(**ids)
    return TraceBinding(owns_call_id=owns_call_id, parent_event_id=parent if isinstance(parent, str) else None)


def unbind_trace_ids(binding: TraceBinding) -> None:
    if binding.parent_event_id is not None:
        structlog.contextvars.bind_contextvars(tool_trace_event_id=binding.parent_event_id)
    else:
        structlog.contextvars.unbind_contextvars("tool_trace_event_id")
    if binding.owns_call_id:
        structlog.contextvars.unbind_contextvars("tool_call_id")


def _write_tool_event(
    tool_name: str,
    *,
    duration_ms: float,
    error_type: str | None,
    learn_stage_ms: dict[str, float] | None,
    run_dir: Path | None,
    fallback_dir: Path | None,
    trace: dict[str, str],
) -> None:
    """Append the ``tool_call`` row to the run log (the delivery IO tracer allowlists this name)."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._surface_role import reviewer_role_active
    from trw_mcp.state.persistence import FileStateWriter
    from trw_mcp.telemetry.constants import Status

    # A reviewer lane writes nothing to disk (PRD-SEC-015); telemetry off means no row either.
    if not get_config().telemetry_enabled or reviewer_role_active():
        return
    success = error_type is None
    row: dict[str, object] = {
        "tool_name": tool_name,
        "duration_ms": duration_ms,
        "success": success,
        "status": Status.SUCCESS if success else Status.ERROR,
        "agent_id": os.environ.get("TRW_AGENT_ID", "default"),
        **trace,
        **({"error_type": error_type} if error_type else {}),
        **({"learn_stage_ms": learn_stage_ms} if learn_stage_ms is not None else {}),
    }
    if run_dir is not None and (run_dir / "meta").is_dir():
        path = run_dir / "meta" / "events.jsonl"
    elif fallback_dir is not None:
        path = fallback_dir / "session-events.jsonl"
    else:
        return
    writer = FileStateWriter()
    writer.ensure_dir(path.parent)
    # Appended directly, not through FileEventLogger.log_event: that also mirrors the row into the unified
    # log as a tool_call event, and the wrapper has already emitted this call's ToolCallEvent there.
    writer.append_jsonl(path, {"ts": datetime.now(timezone.utc).isoformat(), "event": "tool_call", **row})


def record_local(
    tool_name: str,
    *,
    duration_ms: float,
    error_type: str | None,
    learn_stage_ms: dict[str, float] | None,
    run_dir: Path | None,
    fallback_dir: Path | None,
    trace: dict[str, str] | None = None,
) -> None:
    """Write the run-log row and emit the OTEL span; neither can fail the tool call."""
    try:
        _write_tool_event(
            tool_name,
            duration_ms=duration_ms,
            error_type=error_type,
            learn_stage_ms=learn_stage_ms,
            run_dir=run_dir,
            fallback_dir=fallback_dir,
            # event_id / tool_call_id join this row to its unified ToolCallEvent.
            trace=trace or {},
        )
    except Exception:  # justified: fail-open, the run-log row must never fail the tool call
        logger.debug("tool_call_row_write_failed", tool=tool_name, exc_info=True)
    try:
        from trw_mcp.state.otel_wrapper import emit_tool_span

        emit_tool_span(
            tool_name, duration_ms, {"agent_id": os.environ.get("TRW_AGENT_ID", "default")}, error_type=error_type
        )
    except Exception:  # justified: fail-open, the OTEL span must never fail the tool call
        logger.debug("tool_call_span_failed", tool=tool_name, exc_info=True)
