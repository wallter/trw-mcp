"""One tool call's telemetry, begun before the tool runs and emitted after it finishes (PRD-FIX-150).

Belongs to the ``tool_call_timing.py`` facade, which owns ``wrap_tool``. Names tests monkeypatch on the
facade (``bind_trace_ids``, ``emit_tool_call_event``, the pipeline boundary, ...) are looked up there at
call time, so a patch on the facade reaches this module.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.telemetry import tool_call_timing as _t
from trw_mcp.telemetry._owned_awaitable import OwnedAwaitable
from trw_mcp.telemetry._tool_call_measures import CallMeasures, call_measures

if TYPE_CHECKING:
    from trw_mcp.telemetry._tool_call_emit import ToolCallEmitContext
    from trw_mcp.telemetry._tool_call_local import TraceBinding

logger = structlog.get_logger(__name__)


def _begin_telemetry(tool: str) -> tuple[str | None, TraceBinding | None, object | None]:
    """Trace ids and learn-stage timing for one call; on any fault, run the tool without them."""
    try:
        trace_event_id = _t.new_trace_event_id()
        binding = _t.bind_trace_ids(trace_event_id)
    except Exception:  # justified: fail-open, NFR02 - tracing must never stop a tool call
        logger.debug("tool_call_trace_bind_failed", tool=tool, exc_info=True)
        return None, None, None
    try:
        return trace_event_id, binding, _t._learn_stage_timing.begin(tool)
    except Exception:  # justified: fail-open, NFR02 - timing must never stop a tool call
        logger.debug("tool_call_learn_timing_begin_failed", tool=tool, exc_info=True)
        return trace_event_id, binding, None


def _finish_learn_timing() -> dict[str, float] | None:
    try:
        return _t._learn_stage_timing.finish()
    except Exception:  # justified: fail-open, NFR02
        logger.debug("tool_call_learn_timing_finish_failed", reason="fail_open", exc_info=True)
        return None


def _end_telemetry(binding: TraceBinding | None, stage_token: object | None) -> None:
    """Undo _begin_telemetry; each step independently, and never raising into the tool's result."""
    if binding is not None:
        try:
            _t.unbind_trace_ids(binding)
        except Exception:  # justified: fail-open, NFR02
            logger.debug("tool_call_trace_unbind_failed", exc_info=True)
    if stage_token is not None:
        try:
            _t._learn_stage_timing.restore(stage_token)  # type: ignore[arg-type]
        except Exception:  # justified: fail-open, NFR02
            logger.debug("tool_call_learn_timing_restore_failed", exc_info=True)


@dataclass(frozen=True)
class _Resolvers:
    session_id: Callable[[], str] | None
    run_dir: Callable[[], Path | None] | None
    fallback_dir: Callable[[], Path | None] | None
    security_consult: Callable[[str, dict[str, object] | None, str, str | None], None] | None


class _CallTelemetry:
    """One tool call's telemetry: begun before the tool runs, emitted once, after it has finished.

    Every step is fail-open (NFR02): a fault here must never stop or change the tool call.
    """

    def __init__(
        self, name: str, fn: Callable[..., object], args: tuple[object, ...], kwargs: dict[str, object], r: _Resolvers
    ) -> None:
        self.name, self.fn, self.args, self.kwargs, self.resolvers = name, fn, args, kwargs, r
        self.start, self.start_ts = time.monotonic(), datetime.now(tz=timezone.utc)
        self.outcome, self.emit = "success", True
        self.error_class: str | None = None
        self.output_data: object = None
        self.response_bytes: int | None = None
        self.measures = CallMeasures()
        self.marker: Path | None = None
        self.trace_event_id, self.binding, self.stage_token = _begin_telemetry(name)

    def before_run(self) -> None:
        if self.name == "trw_session_start":
            from trw_mcp.telemetry.boot_audit import run_boot_audit

            run_boot_audit()
        self._begin_formation_call()

    def _begin_formation_call(self) -> None:
        """Mark this call in flight for the formation stall scan, from its first step until it ends."""
        try:
            from trw_mcp.formation import begin_call

            ctx = _t._extract_ctx(self.fn, *self.args, **self.kwargs)
            self.marker = begin_call(ctx, datetime.now(tz=timezone.utc).timestamp())
        except Exception:  # trw-fail-silent-allow: instrumentation failure is logged; the tool must still run
            logger.warning("formation_call_start_failed", tool=self.name, exc_info=True)

    def _clear_formation_call(self) -> None:
        marker, self.marker = self.marker, None
        if marker is None:
            return
        try:
            from trw_mcp.formation import clear_call

            clear_call(marker)
        except OSError:  # trw-fail-silent-allow: leave durable marker visible on failed clear
            logger.warning("formation_call_clear_failed", tool=self.name, exc_info=True)

    def succeeded(self, result: object) -> None:
        self.output_data = result
        self.response_bytes = _t._serialized_bytes(result)
        try:
            self.measures = call_measures(self.name, self.kwargs, self.response_bytes)
        except Exception:  # trw-fail-silent-allow: an unmeasured call keeps the zero placeholders and no token_method, which the report reads as not measured
            logger.warning("tool_call_measures_failed", tool=self.name, exc_info=True)

    def failed(self, exc: BaseException) -> None:
        self.outcome, self.error_class = "error", exc.__class__.__name__
        try:
            message = str(exc)[:200]
        except Exception:  # justified: fail-open, an exception whose __str__ raises is still recorded
            message = f"<{self.error_class}: unprintable>"
        self.output_data = {"error": message, "error_type": self.error_class}
        if self.name == "trw_session_start" and self.error_class == "DefaultResolutionError":
            self.emit = False

    def finish(self) -> None:
        self._clear_formation_call()
        learn_stage_ms = _finish_learn_timing()
        try:
            self._emit(learn_stage_ms)
        finally:
            _end_telemetry(self.binding, self.stage_token)

    def finish_after(self, awaitable: Awaitable[object]) -> Awaitable[object]:
        """A sync callable handed back an awaitable: restore this call's context now (in the caller's
        context, before returning), and record the outcome and output once the awaitable resolves.

        The formation marker moves to the awaitable's first step, and an awaitable cancelled before
        that step is closed rather than leaked (PRD-CORE-296-FR05)."""
        self._clear_formation_call()
        learn_stage_ms = _finish_learn_timing()
        _end_telemetry(self.binding, self.stage_token)
        return OwnedAwaitable(self._record_when_resolved(awaitable, learn_stage_ms), awaitable)

    async def _record_when_resolved(
        self, awaitable: Awaitable[object], learn_stage_ms: dict[str, float] | None
    ) -> object:
        try:
            self._begin_formation_call()
            value = await awaitable
        except BaseException as exc:
            self.failed(exc)
            raise
        else:
            self.succeeded(value)
            return value
        finally:
            self._clear_formation_call()
            self._emit(learn_stage_ms)

    def _emit(self, learn_stage_ms: dict[str, float] | None) -> None:
        try:
            if not self.emit:
                logger.debug("tool_call_event_suppressed", tool=self.name, reason=self.error_class or "")
            else:
                _t.emit_tool_call_event(self._context(learn_stage_ms))
        except Exception:  # justified: fail-open, a telemetry emit must never change the tool's outcome
            logger.debug("tool_call_emit_failed", tool=self.name, exc_info=True)

    def _context(self, learn_stage_ms: dict[str, float] | None) -> ToolCallEmitContext:
        r = self.resolvers
        return _t.ToolCallEmitContext(
            recorded_name=self.name,
            fn=self.fn,
            args=self.args,
            kwargs=self.kwargs,
            start=self.start,
            start_ts=self.start_ts,
            end_ts=datetime.now(tz=timezone.utc),
            outcome=self.outcome,
            error_class=self.error_class,
            session_id_resolver=r.session_id,
            run_dir_resolver=r.run_dir,
            fallback_dir_resolver=r.fallback_dir,
            security_consult=r.security_consult,
            bind_call_args=_t._bind_call_args,
            build_tool_call_event=_t.build_tool_call_event,
            enqueue_to_pipeline=_t._enqueue_to_pipeline,
            pipeline_projection=_t._pipeline_projection,
            resolve_fallback_dir=_t._resolve_fallback_dir,
            resolve_run_dir=_t._resolve_run_dir,
            resolve_session_id=_t._resolve_session_id,
            resolve_surface_snapshot_id=_t._resolve_surface_snapshot_id,
            trace_event_id=self.trace_event_id,
            parent_event_id=self.binding.parent_event_id if self.binding else None,
            learn_stage_ms=learn_stage_ms,
            response_bytes=self.response_bytes,
            output_data=self.output_data,
            measures=self.measures,
        )
