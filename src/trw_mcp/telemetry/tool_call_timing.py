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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import structlog
import yaml

from trw_mcp.telemetry.event_base import ToolCallEvent

logger = structlog.get_logger(__name__)

_PRICING_CACHE: dict[str, Any] | None = None
_PRICING_PATH_CACHE: Path | None = None


def _load_pricing() -> dict[str, Any]:
    """Resolve + cache ``pricing.yaml`` from the bundled data package.

    Uses ``importlib.resources`` traversable API + ``as_file`` to survive
    MultiplexedPath / namespace-package layouts (plain ``Path(str(...))``
    fails when files() returns a multiplexed traversable).
    """
    global _PRICING_CACHE, _PRICING_PATH_CACHE
    if _PRICING_CACHE is not None:
        return _PRICING_CACHE
    try:
        from importlib.resources import as_file
        from importlib.resources import files as _pkg_files

        root_traversable = _pkg_files("trw_mcp.data")
        pricing_traversable = root_traversable.joinpath("pricing.yaml")
        if not pricing_traversable.is_file():
            logger.warning("pricing_yaml_missing", path=str(pricing_traversable))
            _PRICING_CACHE = {"version": "unresolved", "models": {}}
            return _PRICING_CACHE
        with as_file(pricing_traversable) as candidate:
            _PRICING_PATH_CACHE = Path(candidate)
            with Path(candidate).open("r", encoding="utf-8") as fh:
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
    return ToolCallEvent(
        session_id=session_id,
        run_id=run_id,
        surface_snapshot_id=surface_snapshot_id,
        payload={
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
    fn: Callable[..., Any],
    *,
    tool_name: str | None = None,
    session_id_resolver: Callable[[], str] | None = None,
    run_dir_resolver: Callable[[], Path | None] | None = None,
    fallback_dir_resolver: Callable[[], Path | None] | None = None,
) -> Callable[..., Any]:
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
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        start_ts = datetime.now(tz=timezone.utc)
        outcome = "success"
        error_class: str | None = None
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:
            outcome = "error"
            error_class = exc.__class__.__name__
            raise
        finally:
            end_ts = datetime.now(tz=timezone.utc)
            session_id = ""
            if session_id_resolver is not None:
                try:
                    session_id = session_id_resolver()
                except Exception:  # justified: fail-open, session_id lookup must not re-raise
                    session_id = ""
            try:
                event = build_tool_call_event(
                    tool=recorded_name,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    session_id=session_id,
                    outcome=outcome,
                    error_class=error_class,
                )
                # FR-4 dispatch: emit to the unified events file under
                # the active run (or the fallback dir). Fail-open.
                try:
                    from trw_mcp.telemetry.unified_events import emit as _emit_unified

                    run_dir: Path | None = None
                    fallback_dir: Path | None = None
                    if run_dir_resolver is not None:
                        try:
                            run_dir = run_dir_resolver()
                        except Exception:  # justified: fail-open
                            run_dir = None
                    if fallback_dir_resolver is not None:
                        try:
                            fallback_dir = fallback_dir_resolver()
                        except Exception:  # justified: fail-open
                            fallback_dir = None
                    _emit_unified(event, run_dir=run_dir, fallback_dir=fallback_dir)
                except Exception:  # justified: fail-open, emit path must not block tool
                    logger.debug("tool_call_event_emit_failed", tool=recorded_name, exc_info=True)

                logger.debug(
                    "tool_call_event_constructed",
                    tool=recorded_name,
                    wall_ms=max(0, int((end_ts - start_ts).total_seconds() * 1000)),
                    outcome=outcome,
                    error_class=error_class or "",
                    elapsed_monotonic=round(time.monotonic() - start, 6),
                )
            except Exception:  # justified: fail-open, event construction must not leak
                logger.warning("tool_call_timing_failed", tool=recorded_name, exc_info=True)

    return wrapper


__all__ = [
    "build_tool_call_event",
    "clear_pricing_cache",
    "wrap_tool",
]
