"""Per-dispatch MCP security consult hook (PRD-INFRA-SEC-001 FR-9).

Sprint-96 Carry-Forward (a): closes CRIT-2. The ``MCPSecurityMiddleware``
singleton is instantiated at startup in :mod:`trw_mcp.server._app` and
stored in the module-level ``_mcp_security`` attribute, but nothing in the
production dispatch path was consulting it. This module exposes
:func:`consult_mcp_security`, a tiny, fail-open helper called from the
``wrap_tool`` timing shim and from individual sprint-96 tools so that the
middleware's ``on_tool_call`` method fires on every real MCP tool
dispatch.

Observe-mode contract (NEVER violate):

* This helper NEVER blocks and NEVER raises. Decisions are observational —
  they feed telemetry side-effects (``MCPSecurityEvent`` emission and
  shadow-clock instrumentation) only.
* If the ``_mcp_security`` singleton is ``None`` (startup failure,
  fail-open path), the consult is a no-op. No warning is logged — this is
  the documented degraded mode.
* If the consult call itself raises, the exception is caught, a
  ``warning`` is logged, and the wrapped tool continues normally.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def consult_mcp_security(
    tool: str,
    args: dict[str, Any] | None = None,
    session_id: str = "",
    run_id: str | None = None,
    *,
    transport: str = "stdio",
) -> None:
    """Consult the ``MCPSecurityMiddleware`` singleton for one dispatch.

    Fail-open: no exception escapes this function. When the middleware is
    uninitialized, the call is a silent no-op (documented degraded mode,
    no warning). When the middleware call raises, a warning is logged and
    the caller proceeds unaffected.

    Args:
        tool: Short tool name (e.g. ``"trw_query_events"``). Already
            prefix-stripped — the middleware does additional normalization.
        args: Optional tool arguments dict. ``None`` is treated as empty.
        session_id: Session identifier when resolvable, else ``""``.
        run_id: Active run id when resolvable, else ``None``.
        transport: MCP transport in use. Defaults to ``"stdio"`` since
            stdio is the primary transport for claude-code; HTTP dispatch
            wrapping can override later.
    """
    try:
        # Deferred import: _app imports _tools which imports this module,
        # so a module-level import here would be circular at startup.
        from trw_mcp.server import _app as _app_mod

        middleware = getattr(_app_mod, "_mcp_security", None)
        if middleware is None:
            return
        if getattr(middleware, "mounted_in_chain", False):
            return
        on_tool_call = getattr(middleware, "on_tool_call", None)
        if on_tool_call is None:
            return
        server_name = getattr(middleware, "default_server_name", "trw")
        on_tool_call(
            transport=transport,
            server=server_name,
            tool=tool,
            args=args or {},
            session_id=session_id,
            run_id=run_id,
        )
        logger.debug(
            "mcp_security_consult_ok",
            tool=tool,
            transport=transport,
            has_session=bool(session_id),
            has_run=run_id is not None,
        )
    except Exception:  # justified: fail-open, per-dispatch consult must never raise
        logger.warning("mcp_security_consult_failed", tool=tool, exc_info=True)


__all__ = ["consult_mcp_security"]
