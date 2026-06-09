"""Emission helper for :mod:`trw_mcp.telemetry.tool_call_timing`."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

from trw_mcp.telemetry.event_base import ToolCallEvent

logger = structlog.get_logger("trw_mcp.telemetry.tool_call_timing")

SecurityConsult = Callable[[str, dict[str, object] | None, str, str | None], None]


@dataclass(frozen=True, slots=True)
class ToolCallEmitContext:
    recorded_name: str
    fn: Callable[..., object]
    args: tuple[object, ...]
    kwargs: dict[str, object]
    start: float
    start_ts: datetime
    end_ts: datetime
    outcome: str
    error_class: str | None
    session_id_resolver: Callable[[], str] | None
    run_dir_resolver: Callable[[], Path | None] | None
    fallback_dir_resolver: Callable[[], Path | None] | None
    security_consult: SecurityConsult | None
    bind_call_args: Callable[..., dict[str, object]]
    build_tool_call_event: Callable[..., ToolCallEvent]
    enqueue_to_pipeline: Callable[[dict[str, object]], None]
    pipeline_projection: Callable[[ToolCallEvent], dict[str, object]]
    resolve_fallback_dir: Callable[[], Path | None]
    resolve_run_dir: Callable[..., Path | None]
    resolve_session_id: Callable[..., str]
    resolve_surface_snapshot_id: Callable[[Path | None], str]


def emit_tool_call_event(ctx: ToolCallEmitContext) -> None:
    """Resolve call metadata, emit the unified event, and enqueue its projection."""
    session_id = _resolve_session_id(ctx)
    resolved_run_dir = _resolve_run_dir(ctx)
    run_id = resolved_run_dir.name if resolved_run_dir is not None else None
    surface_snapshot_id = ctx.resolve_surface_snapshot_id(resolved_run_dir)
    try:
        event = ctx.build_tool_call_event(
            tool=ctx.recorded_name,
            start_ts=ctx.start_ts,
            end_ts=ctx.end_ts,
            session_id=session_id,
            run_id=run_id,
            surface_snapshot_id=surface_snapshot_id,
            outcome=ctx.outcome,
            error_class=ctx.error_class,
        )
        _consult_security(ctx, session_id=session_id, run_id=run_id)
        _emit_unified(ctx, event=event, run_dir=resolved_run_dir)
        _enqueue_pipeline(ctx, event=event)
        logger.debug(
            "tool_call_event_constructed",
            tool=ctx.recorded_name,
            wall_ms=max(0, int((ctx.end_ts - ctx.start_ts).total_seconds() * 1000)),
            outcome=ctx.outcome,
            error_class=ctx.error_class or "",
            elapsed_monotonic=round(time.monotonic() - ctx.start, 6),
        )
    except Exception:  # justified: fail-open, event construction must not leak
        logger.warning("tool_call_timing_failed", tool=ctx.recorded_name, exc_info=True)


def _resolve_session_id(ctx: ToolCallEmitContext) -> str:
    if ctx.session_id_resolver is None:
        return ctx.resolve_session_id(ctx.fn, *ctx.args, **ctx.kwargs)
    try:
        return ctx.session_id_resolver()
    except Exception:  # justified: fail-open, session_id lookup must not re-raise
        return ""


def _resolve_run_dir(ctx: ToolCallEmitContext) -> Path | None:
    if ctx.run_dir_resolver is None:
        return ctx.resolve_run_dir(ctx.fn, *ctx.args, **ctx.kwargs)
    try:
        return ctx.run_dir_resolver()
    except Exception:  # justified: fail-open
        return None


def _consult_security(ctx: ToolCallEmitContext, *, session_id: str, run_id: str | None) -> None:
    if ctx.security_consult is None:
        return
    try:
        ctx.security_consult(
            ctx.recorded_name,
            ctx.bind_call_args(ctx.fn, *ctx.args, **ctx.kwargs),
            session_id,
            run_id,
        )
    except Exception:  # justified: fail-open, consult must not block emit
        logger.debug("tool_call_security_consult_failed", tool=ctx.recorded_name, exc_info=True)


def _emit_unified(ctx: ToolCallEmitContext, *, event: ToolCallEvent, run_dir: Path | None) -> None:
    try:
        from trw_mcp.telemetry.unified_events import emit as _emit_unified_event

        fallback_dir = _resolve_fallback_dir(ctx)
        _emit_unified_event(event, run_dir=run_dir, fallback_dir=fallback_dir)
    except Exception:  # justified: fail-open, emit path must not block tool
        logger.debug("tool_call_event_emit_failed", tool=ctx.recorded_name, exc_info=True)


def _resolve_fallback_dir(ctx: ToolCallEmitContext) -> Path | None:
    if ctx.fallback_dir_resolver is None:
        return ctx.resolve_fallback_dir()
    try:
        return ctx.fallback_dir_resolver()
    except Exception:  # justified: fail-open
        return None


def _enqueue_pipeline(ctx: ToolCallEmitContext, *, event: ToolCallEvent) -> None:
    try:
        ctx.enqueue_to_pipeline(ctx.pipeline_projection(event))
    except Exception:  # justified: fail-open, enqueue must not block tool
        logger.debug("tool_call_pipeline_enqueue_failed", tool=ctx.recorded_name, exc_info=True)
