"""Tool-call timing middleware (PRD-HPO-MEAS-001 FR-4).

Wraps every ``@server.tool()`` registration to emit one
:class:`ToolCallEvent` per invocation with ``wall_ms + input_tokens +
output_tokens + usd_cost_est + pricing_version + outcome + error_class``.

Design:

- Pure decorator wrapper. Can be applied at tool-registration time via
  :func:`wrap_tool` or later via :func:`instrument_server` which walks
  all tools on a :class:`FastMCP` instance.
- Pricing lookup is lazy + cached from ``trw_mcp.data/pricing.yaml`` so
  hot-path overhead is a single dict access.
- Fail-open: every instrumentation failure degrades to a WARN log and
  the wrapped tool still runs. Per NFR-1 (p99 ≤ 2ms per-event emission).
- Token counts are payload-attached when the wrapped tool's result
  carries them (future). For v1 we emit zeros and let FR-14's
  ``nullable_zero_by_design: true`` annotation cover the field.
"""

from __future__ import annotations

import functools
import inspect
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, cast

import structlog
import yaml

# PRD-CORE-215 FR04: re-export the ceremony-tool execution inventory public
# surface (facade). Kept as a plain grouped import + ``__all__`` membership so
# the file stays under the 350 effective-LOC gate; construction helpers
# (CeremonyToolSpec, build_ceremony_tool_manifest, DuplicateCeremonyToolError)
# live in ``_ceremony_tool_manifest`` and are imported from there directly.
from trw_mcp.telemetry._ceremony_tool_manifest import (
    CeremonyExecutionClass,
    RequestIdentityPolicy,
    UnknownCeremonyToolError,
    ceremony_tool_disposition,
    ceremony_tool_names,
    ceremony_tool_spec,
)
from trw_mcp.telemetry._tool_call_emit import ToolCallEmitContext, emit_tool_call_event
from trw_mcp.telemetry.constants import EventType, Status
from trw_mcp.telemetry.event_base import ToolCallEvent
from trw_mcp.telemetry.trace_context import build_tool_trace_fields, new_trace_event_id

logger = structlog.get_logger(__name__)


class _WrappedToolMarker(Protocol):
    __trw_tool_call_wrapped__: bool


def _mark_tool_call_wrapped(fn: Callable[..., object]) -> Callable[..., object]:
    marked = cast("_WrappedToolMarker", fn)
    marked.__trw_tool_call_wrapped__ = True
    return fn


_PRICING_CACHE: dict[str, object] | None = None
_PRICING_PATH_CACHE: Path | None = None


def _resolve_pricing_path() -> Path | None:
    """Resolve the active pricing table path from config or package defaults."""
    try:
        from trw_mcp.models.config import get_config

        configured = str(get_config().pricing_table_path).strip()
        if configured:
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute():
                try:
                    from trw_mcp.state._paths import resolve_project_root

                    candidate = (resolve_project_root() / candidate).resolve()
                except Exception:
                    candidate = candidate.resolve()
            return candidate
    except Exception:  # justified: fail-open, config resolution must not break pricing fallback
        logger.debug("pricing_path_config_resolution_failed", exc_info=True)

    try:
        from importlib.resources import as_file
        from importlib.resources import files as _pkg_files

        pricing_traversable = _pkg_files("trw_mcp.data").joinpath("pricing.yaml")
        if not pricing_traversable.is_file():
            return None
        with as_file(pricing_traversable) as candidate:
            return Path(candidate)
    except Exception:  # justified: fail-open, callers handle missing pricing path
        logger.debug("pricing_path_package_resolution_failed", exc_info=True)
        return None


def _load_pricing() -> dict[str, object]:
    """Resolve + cache ``pricing.yaml`` from the bundled data package.

    Uses ``importlib.resources`` traversable API + ``as_file`` to survive
    MultiplexedPath / namespace-package layouts (plain ``Path(str(...))``
    fails when files() returns a multiplexed traversable).
    """
    global _PRICING_CACHE, _PRICING_PATH_CACHE
    resolved_path = _resolve_pricing_path()
    if _PRICING_CACHE is not None and resolved_path == _PRICING_PATH_CACHE:
        return _PRICING_CACHE
    try:
        if resolved_path is None or not resolved_path.is_file():
            logger.warning("pricing_yaml_missing", path=str(resolved_path or ""))
            _PRICING_CACHE = {"version": "unresolved", "models": {}}
            _PRICING_PATH_CACHE = resolved_path
            return _PRICING_CACHE
        _PRICING_PATH_CACHE = resolved_path
        with resolved_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            logger.warning("pricing_yaml_malformed", path=str(_PRICING_PATH_CACHE))
            _PRICING_CACHE = {"version": "malformed", "models": {}}
            return _PRICING_CACHE
        _PRICING_CACHE = data
        return _PRICING_CACHE
    except Exception:  # justified: boundary, pricing lookup must never break tool calls
        logger.warning("pricing_yaml_load_failed", exc_info=True)
        _PRICING_CACHE = {"version": "error", "models": {}}
        return _PRICING_CACHE


def _pricing_version() -> str:
    return str(_load_pricing().get("version", "unknown"))


def _usd_cost_estimate(
    *,
    model_id: str | None,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Look up per-1K rates and return an estimated USD cost for the call."""
    if not model_id:
        return 0.0
    table = _load_pricing()
    models = table.get("models", {})
    if not isinstance(models, dict):
        return 0.0
    entry = models.get(model_id)
    if not isinstance(entry, dict):
        return 0.0
    in_rate = float(entry.get("input_per_1k", 0.0) or 0.0)
    out_rate = float(entry.get("output_per_1k", 0.0) or 0.0)
    return round(((input_tokens / 1000.0) * in_rate) + ((output_tokens / 1000.0) * out_rate), 8)


def clear_pricing_cache() -> None:
    """Drop the process-wide pricing cache. Test-only helper."""
    global _PRICING_CACHE, _PRICING_PATH_CACHE
    _PRICING_CACHE = None
    _PRICING_PATH_CACHE = None


def _pipeline_projection(event: ToolCallEvent) -> dict[str, object]:
    """Flatten a :class:`ToolCallEvent` into the pipeline/backend projection.

    Mirrors the flat ``event_data`` shape the older ``@log_tool_call`` path
    enqueues (``tools/telemetry.py::_write_tool_event``): top-level keys that
    line up with the backend's ``MAPPED_FIELDS`` (``tool_name``,
    ``duration_ms``, ``status``, ``event_type``, ``session_id``, ``run_id``,
    ``error_type``) so they land in ``telemetry_events`` columns rather than
    the catch-all payload JSON. ``outcome`` is carried alongside ``status``
    for parity with the ``wrap_tool`` native vocabulary; the pipeline's
    ``_scrub_pii``/``_enrich_*`` steps fill in installation_id, framework_version,
    phase, and ts.
    """
    payload = event.payload
    outcome = str(payload.get("outcome", "success"))
    status = Status.SUCCESS if outcome == "success" else Status.ERROR
    error_class = str(payload.get("error_class", "") or "")
    projection: dict[str, object] = {
        "tool_name": str(payload.get("tool", "")),
        "duration_ms": payload.get("wall_ms", 0),
        "outcome": outcome,
        "status": status,
        "success": outcome == "success",
        "event_type": EventType.TOOL_INVOCATION,
        "session_id": event.session_id,
        "run_id": event.run_id,
    }
    if error_class:
        projection["error_type"] = error_class
    return projection


def _enqueue_to_pipeline(event_data: dict[str, object]) -> None:
    """Enqueue a flat tool event to the telemetry pipeline. Fail-open.

    Mirrors ``tools/telemetry.py::_enqueue_to_pipeline`` so events emitted via
    the unified ``wrap_tool`` path reach the backend (the senders read the
    pipeline's ``pipeline-events.jsonl``, never the unified events file).
    """
    try:
        from trw_mcp.telemetry.pipeline import TelemetryPipeline

        pipeline = TelemetryPipeline.get_instance()
        pipeline.enqueue(dict(event_data))
    except Exception:  # justified: fail-open, telemetry pipeline enqueue must never block tool execution
        logger.debug("tool_call_pipeline_enqueue_skipped", exc_info=True)  # justified: fail-open


def _bind_call_args(fn: Callable[..., object], *args: object, **kwargs: object) -> dict[str, object]:
    """Best-effort binding of invocation args for security/audit metadata."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        return dict(kwargs)
    return {name: value for name, value in bound.arguments.items() if name not in {"self", "ctx", "context"}}


def _extract_ctx(fn: Callable[..., object], *args: object, **kwargs: object) -> object | None:
    """Return the bound FastMCP context-like arg when present."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        return kwargs.get("ctx") or kwargs.get("context")
    for name in ("ctx", "context"):
        if name in bound.arguments:
            return cast("object | None", bound.arguments[name])
    return None


def _build_call_context(ctx: object | None) -> object | None:
    """Best-effort TRWCallContext builder for ctx-aware run resolution."""
    if ctx is None:
        return None
    try:
        from trw_mcp.state._paths import TRWCallContext, resolve_pin_key

        raw_session = getattr(ctx, "session_id", None)
        return TRWCallContext(
            session_id=resolve_pin_key(ctx=ctx, explicit=None),
            client_hint=None,
            explicit=False,
            fastmcp_session=raw_session if isinstance(raw_session, str) else None,
        )
    except Exception:  # justified: fail-open
        logger.debug("tool_call_context_build_failed", exc_info=True)
        return None


def _resolve_session_id(fn: Callable[..., object], *args: object, **kwargs: object) -> str:
    """Resolve the effective session_id for a wrapped production tool call."""
    ctx = _extract_ctx(fn, *args, **kwargs)
    try:
        from trw_mcp.state._paths import resolve_pin_key

        return resolve_pin_key(ctx=ctx, explicit=os.environ.get("TRW_SESSION_ID"))
    except Exception:  # justified: fail-open
        logger.debug("tool_call_session_id_resolution_failed", exc_info=True)
        return str(os.environ.get("TRW_SESSION_ID", ""))


def _resolve_run_dir(fn: Callable[..., object], *args: object, **kwargs: object) -> Path | None:
    """Resolve the active run directory from explicit args, pin store, or ctx."""
    bound_args = _bind_call_args(fn, *args, **kwargs)
    explicit_run = bound_args.get("run_path")
    if isinstance(explicit_run, str) and explicit_run.strip():
        return Path(explicit_run).expanduser().resolve()

    try:
        from trw_mcp.state._paths import TRWCallContext, find_active_run, get_pinned_run

        call_ctx = cast("TRWCallContext | None", _build_call_context(_extract_ctx(fn, *args, **kwargs)))
        if call_ctx is not None:
            return get_pinned_run(context=call_ctx) or find_active_run(context=call_ctx)
        # PRD-FIX-083: no ctx available. Pin-only — never fall through to the
        # legacy mtime scan from a per-tool-call timing wrapper. Returning None
        # leaves the timing event unattributed, which is preferable to spending
        # ~25s on a YAML scan for every untyped call.
        return get_pinned_run()
    except Exception:  # justified: fail-open
        logger.debug("tool_call_run_dir_resolution_failed", exc_info=True)
        return None


def _resolve_fallback_dir() -> Path | None:
    """Resolve the fallback context directory for non-run-scoped writes."""
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state._paths import resolve_trw_dir

        cfg = get_config()
        return resolve_trw_dir() / cfg.context_dir
    except Exception:  # justified: fail-open
        logger.debug("tool_call_fallback_dir_resolution_failed", exc_info=True)
        return None


def _resolve_surface_snapshot_id(run_dir: Path | None) -> str:
    """Resolve the run's surface_snapshot_id from run.yaml or the stamped snapshot."""
    if run_dir is None:
        return ""
    meta_dir = run_dir / "meta"
    run_yaml = meta_dir / "run.yaml"
    if run_yaml.exists():
        try:
            from trw_mcp.state.persistence import FileStateReader

            run_data = FileStateReader().read_yaml(run_yaml)
            snapshot = run_data.get("surface_snapshot_id")
            if snapshot is not None:
                return str(snapshot)
        except Exception:  # justified: fail-open
            logger.debug("tool_call_surface_from_run_yaml_failed", exc_info=True)
    snapshot_yaml = meta_dir / "run_surface_snapshot.yaml"
    if snapshot_yaml.exists():
        try:
            from trw_mcp.state.persistence import FileStateReader

            snapshot_data = FileStateReader().read_yaml(snapshot_yaml)
            snapshot = snapshot_data.get("snapshot_id")
            if snapshot is not None:
                return str(snapshot)
        except Exception:  # justified: fail-open
            logger.debug("tool_call_surface_from_snapshot_failed", exc_info=True)
    return ""


def build_tool_call_event(
    *,
    tool: str,
    start_ts: datetime,
    end_ts: datetime,
    session_id: str,
    run_id: str | None = None,
    surface_snapshot_id: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    model_id: str | None = None,
    outcome: str = "success",
    error_class: str | None = None,
    parent_event_id: str | None = None,
    tool_call_id: str | None = None,
    input_data: object | None = None,
    output_data: object | None = None,
    task_profile_hash: str = "",
) -> ToolCallEvent:
    """Assemble a :class:`ToolCallEvent` for a completed tool invocation.

    Exposed as a pure function so (a) callers can emit manually when
    they have richer token/outcome data than the simple wrapper sees,
    and (b) tests can construct representative events without running
    the wrapped tool.
    """
    wall_ms = max(0, int((end_ts - start_ts).total_seconds() * 1000))
    usd = _usd_cost_estimate(
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    event_id = new_trace_event_id()
    trace_fields = build_tool_trace_fields(
        tool_name=tool,
        event_id=event_id,
        parent_event_id=parent_event_id,
        tool_call_id=tool_call_id,
        input_data=input_data,
        output_data=output_data,
        task_profile_hash=task_profile_hash,
    )
    return ToolCallEvent(
        event_id=event_id,
        session_id=session_id,
        run_id=run_id,
        surface_snapshot_id=surface_snapshot_id,
        parent_event_id=parent_event_id,
        payload={
            **trace_fields,
            "tool": tool,
            "start_ts": start_ts.isoformat(),
            "end_ts": end_ts.isoformat(),
            "wall_ms": wall_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model_id": model_id or "",
            "usd_cost_est": usd,
            "pricing_version": _pricing_version(),
            "outcome": outcome,
            "error_class": error_class or "",
        },
    )


def wrap_tool(
    fn: Callable[..., object],
    *,
    tool_name: str | None = None,
    session_id_resolver: Callable[[], str] | None = None,
    run_dir_resolver: Callable[[], Path | None] | None = None,
    fallback_dir_resolver: Callable[[], Path | None] | None = None,
    security_consult: Callable[[str, dict[str, object] | None, str, str | None], None] | None = None,
) -> Callable[..., object]:
    """Return a wrapped copy of ``fn`` that emits a :class:`ToolCallEvent` per call.

    Args:
        fn: The original ``@server.tool()``-registered callable.
        tool_name: Override the recorded tool name (defaults to ``fn.__name__``).
        session_id_resolver: Callable returning the current session_id at
            invocation time. When None, emissions carry ``session_id=""``.
        run_dir_resolver: Callable returning the active run directory
            (``<task>/<run_id>/``) so the event lands in that run's
            ``events-YYYY-MM-DD.jsonl``. When None or returns None, the
            fallback directory is used.
        fallback_dir_resolver: Callable returning a fallback directory
            (typically ``<trw_dir>/<context_dir>/``) when no run is
            pinned. When both resolvers return None, the event is still
            constructed for test observability but not written.
    """
    recorded_name: str = tool_name or str(getattr(fn, "__name__", "unknown_tool"))

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        start = time.monotonic()
        start_ts = datetime.now(tz=timezone.utc)
        outcome = "success"
        error_class: str | None = None
        emit_event = True
        try:
            if recorded_name == "trw_session_start":
                from trw_mcp.telemetry.boot_audit import run_boot_audit

                run_boot_audit()
            return fn(*args, **kwargs)
        except BaseException as exc:
            outcome = "error"
            error_class = exc.__class__.__name__
            if recorded_name == "trw_session_start" and error_class == "DefaultResolutionError":
                emit_event = False
            raise
        finally:
            if not emit_event:
                logger.debug("tool_call_event_suppressed", tool=recorded_name, reason=error_class or "")
            else:
                end_ts = datetime.now(tz=timezone.utc)
                emit_tool_call_event(
                    ToolCallEmitContext(
                        recorded_name=recorded_name,
                        fn=fn,
                        args=args,
                        kwargs=kwargs,
                        start=start,
                        start_ts=start_ts,
                        end_ts=end_ts,
                        outcome=outcome,
                        error_class=error_class,
                        session_id_resolver=session_id_resolver,
                        run_dir_resolver=run_dir_resolver,
                        fallback_dir_resolver=fallback_dir_resolver,
                        security_consult=security_consult,
                        bind_call_args=_bind_call_args,
                        build_tool_call_event=build_tool_call_event,
                        enqueue_to_pipeline=_enqueue_to_pipeline,
                        pipeline_projection=_pipeline_projection,
                        resolve_fallback_dir=_resolve_fallback_dir,
                        resolve_run_dir=_resolve_run_dir,
                        resolve_session_id=_resolve_session_id,
                        resolve_surface_snapshot_id=_resolve_surface_snapshot_id,
                    )
                )

    return _mark_tool_call_wrapped(wrapper)


__all__ = [
    "CeremonyExecutionClass",
    "RequestIdentityPolicy",
    "UnknownCeremonyToolError",
    "build_tool_call_event",
    "ceremony_tool_disposition",
    "ceremony_tool_names",
    "ceremony_tool_spec",
    "clear_pricing_cache",
    "wrap_tool",
]

# Internal helpers exposed for test monkeypatching of the pipeline boundary.
__test_internals__ = [
    "_enqueue_to_pipeline",
    "_pipeline_projection",
]
