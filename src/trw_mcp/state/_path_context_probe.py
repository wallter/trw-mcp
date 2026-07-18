"""Defensive FastMCP context probing used by per-connection run pinning."""

from __future__ import annotations

from typing import Any

import structlog

_FASTMCP_CTX_PROBES: tuple[tuple[str, ...], ...] = (
    ("session_id",),
    ("request_context", "meta", "session_id"),
    ("request_id",),
)


def _runtime_logger() -> Any:
    """Return a fresh logger so structlog test capture sees late-bound events."""
    return structlog.get_logger("trw_mcp.state._paths")


class _ProbeOutcome:
    """Internal sentinel for a defensive context attribute walk."""

    __slots__ = ("broken", "value")

    def __init__(self, value: object | None, broken: bool) -> None:
        self.value = value
        self.broken = broken


def _walk_ctx_attrs(ctx: object, path: tuple[str, ...]) -> _ProbeOutcome:
    """Walk an attribute path and distinguish missing shapes from null values."""
    current: object = ctx
    try:
        for name in path:
            current = getattr(current, name)
    except (AttributeError, RuntimeError, TypeError):
        return _ProbeOutcome(value=None, broken=True)
    return _ProbeOutcome(value=current, broken=False)


def _extract_fastmcp_session_id(ctx: object) -> str | None:
    """Resolve a FastMCP session identifier from supported context shapes."""
    broken_paths: list[str] = []
    for path in _FASTMCP_CTX_PROBES:
        path_str = ".".join(path)
        outcome = _walk_ctx_attrs(ctx, path)
        if outcome.broken:
            broken_paths.append(path_str)
            _runtime_logger().info("fastmcp_context_probe_skipped", ctx_attr_path=path_str)
            continue
        value = outcome.value
        if isinstance(value, str) and value:
            _runtime_logger().info(
                "fastmcp_context_probe_hit",
                ctx_attr_path=path_str,
                has_value=True,
            )
            return value
        _runtime_logger().info(
            "fastmcp_context_probe_miss",
            ctx_attr_path=path_str,
            has_value=False,
        )

    if broken_paths and len(broken_paths) == len(_FASTMCP_CTX_PROBES):
        _runtime_logger().warning(
            "fastmcp_context_probe_error",
            broken_paths=broken_paths,
            ctx_type=type(ctx).__name__,
        )
    return None
