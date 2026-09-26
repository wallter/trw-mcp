"""Surface-authority masking middleware — PRD-CORE-218 FR03/FR04, flattened by PRD-CORE-300 S11b.

The one layer that decides which TRW tools a session sees and may call. It
masks the advertised + callable surface to
``server/_surface_manifest_registry.resolve_tool_surface``: the kernel plus every
capability pack whose config flag is on (``comms_enabled``,
``dispatch_tools_exposed``, ``assess_enabled``). The surface does not depend on
the task or the run phase, and there is no grant path: a tool outside the
surface is reached by turning its flag on, and the denial says which flag.

``tool_resolution_mode = "all"`` exposes every registered tool except the
dispatch pack, which still needs ``dispatch_tools_exposed`` (PRD-CORE-300 FR09:
process launching is never on by default).

A reviewer-role session (PRD-SEC-015) is bounded to ``REVIEWER_TOOLS`` ahead of
the mode and the flags, and fails CLOSED on a resolution fault. Every other
session fails OPEN: ANY resolution error exposes the full catalogue
(``on_list_tools``) or executes the call (``on_call_tool``), and every fail-open
path logs a warning. When a session's surface changes (a config reload flipped a
flag) the list path emits ``notifications/tools/list_changed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

import structlog
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools import Tool, ToolResult
from mcp.types import CallToolRequestParams, ListToolsRequest, TextContent

from trw_mcp.middleware._mcp_security_list_tools import safe_session_id as safe_session_id_from_context
from trw_mcp.middleware._phase_transitions import emit_list_changed
from trw_mcp.models.surface_packs import REVIEWER_TOOLS
from trw_mcp.state._surface_role import _env_marks_reviewer as _surface_role_env_marks_reviewer
from trw_mcp.state._surface_role import reset_surface_role_state, reviewer_role_active

logger = structlog.get_logger(__name__)

#: The resolution mode reported for a reviewer-role session (PRD-SEC-015-FR03).
#: Not a ``tool_resolution_mode`` value — the role is a session-IDENTITY axis
#: that is resolved BEFORE the mode and replaces it.
_REVIEWER_MODE = "reviewer"

#: session_id -> last resolved base surface (P2a change detection). Process-local.
_last_surface: dict[str, frozenset[str]] = {}


def reset_surface_authority_state() -> None:
    """Clear the per-session surface-change ledger — for testing only."""
    _last_surface.clear()
    reset_surface_role_state()


class _Resolved(NamedTuple):
    """A resolved surface plus the mode that produced it."""

    mode: str
    tools: frozenset[str]


def _resolve_mode() -> str:
    """Return the configured ``tool_resolution_mode`` (``standard`` default).

    Not wrapped in fail-open here: a config-read failure propagates to the
    caller's outer ``except`` so the middleware fails OPEN (full surface), never
    silently masking under an unknown mode.
    """
    from trw_mcp.models.config import get_config

    return str(getattr(get_config(), "tool_resolution_mode", "standard"))


# PRD-SEC-015: the reviewer-role identity lives in ONE place —
# ``state/_surface_role.py`` — and is consulted by every side-effecting layer
# (telemetry, recall, code index, skill tracking, boot). The names below are kept
# for the call sites and tests that import them from this module.
_is_reviewer_role = reviewer_role_active
_env_marks_reviewer = _surface_role_env_marks_reviewer


def _gating_flag(tool_name: str) -> str:
    """Return the config flag that turns ``tool_name`` on, or ``""``."""
    from trw_mcp.models.surface_packs import FLAG_GATED_PACKS, PACK_TOOLS

    for pack, tools in PACK_TOOLS.items():
        if tool_name in tools:
            return FLAG_GATED_PACKS.get(pack, "")
    return ""


class SurfaceAuthorityMiddleware(Middleware):
    """Mask the tool catalogue + deny calls to tools outside the resolved surface."""

    mounted_in_chain = True

    def _resolve(self) -> _Resolved:
        """Resolve the session's surface: kernel plus every pack whose flag is on.

        The surface depends on the config flags and the session role only —
        never on the task or the run phase (PRD-CORE-300 S11a/S11b).
        """
        # PRD-SEC-015-FR03: the session-IDENTITY axis is answered first. The
        # reviewer branch REPLACES the resolved surface rather than subtracting
        # from the agent surface, which grows by ordinary maintenance; a
        # subtractive design would silently re-widen every reviewer surface. It
        # also precedes ``mode == "all"``: "all" widens an OPERATOR'S OWN
        # session, while the role CONTAINS a subordinate process.
        if _is_reviewer_role():
            return _Resolved(mode=_REVIEWER_MODE, tools=REVIEWER_TOOLS)
        from trw_mcp.models.config import get_config
        from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

        mode = _resolve_mode()
        config = get_config()
        surface = resolve_tool_surface(
            mode,
            comms_enabled=getattr(config, "comms_enabled", False) is True,
            dispatch_enabled=getattr(config, "dispatch_tools_exposed", False) is True,
            assess_enabled=getattr(config, "assess_enabled", False) is True,
        )
        return _Resolved(mode=surface.mode, tools=frozenset(surface.tools))

    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: CallNext[ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Filter the advertised tools to the resolved surface (FR03/FR04)."""
        tools = list(await call_next(context))
        try:
            ctx = context.fastmcp_context
            session_id = safe_session_id_from_context(ctx)
            resolved = self._resolve()
            await self._maybe_notify_surface_change(session_id, resolved.tools, ctx)
            filtered = [t for t in tools if t.name in resolved.tools]
            logger.debug(
                "surface_authority_filtered",
                component="surface_authority",
                op="list_tools",
                mode=resolved.mode,
                total=len(tools),
                visible=len(filtered),
            )
            return filtered
        except Exception:
            # NFR02: a REVIEWER-marked process fails CLOSED — a deliberate,
            # reviewer-scoped inversion of the PRD-CORE-218 fail-open contract.
            # A bricked reviewer is a lost second opinion; an un-bounded reviewer
            # is an authorization bypass, so availability loses to containment
            # for a subordinate process. Every other session keeps fail-open.
            if _env_marks_reviewer():
                logger.warning("surface_authority_list_failed", outcome="fail_closed_reviewer", exc_info=True)
                return [t for t in tools if t.name in REVIEWER_TOOLS]
            logger.warning("surface_authority_list_failed", outcome="fail_open", exc_info=True)
            return tools

    @staticmethod
    async def _maybe_notify_surface_change(
        session_id: str, surface: frozenset[str], fastmcp_context: object | None
    ) -> None:
        """Emit ``notifications/tools/list_changed`` when a session's resolved
        surface CHANGES (P2a) — e.g. a config reload flipped a pack flag. Uses the shared ``emit_list_changed`` path so a
        capable client re-fetches ``tools/list``. The FIRST observation seeds the
        ledger silently (no spurious notify on a client's initial listing).
        Fail-open: a refresh fault must never break ``list_tools``.
        """
        if not session_id:
            return
        try:
            previous = _last_surface.get(session_id)
            _last_surface[session_id] = surface
            if previous is not None and previous != surface:
                await emit_list_changed(fastmcp_context)
                logger.info(
                    "surface_authority_list_changed",
                    component="surface_authority",
                    op="list_tools",
                    session_id=session_id,
                )
        except Exception:  # justified: fail-open — notification is advisory
            logger.warning("surface_authority_notify_failed", exc_info=True)

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Deny a call to a tool outside the resolved surface (FR03/FR04).

        The fail-open ``call_next`` fallback covers ONLY the gating resolution;
        it never wraps a branch that has already invoked ``call_next``, so a
        raising tool executes exactly once.
        """
        tool_name = context.message.name
        try:
            resolved = self._resolve()
        except Exception:
            # NFR02 (see on_list_tools): reviewer-marked processes DENY on a
            # resolution fault. The PRD accepts "the reviewer returns only
            # denials" as the safe degradation for a subordinate lane.
            if _env_marks_reviewer():
                logger.warning(
                    "surface_authority_call_failed", outcome="fail_closed_reviewer", tool=tool_name, exc_info=True
                )
                return self._deny(tool_name=tool_name, mode=_REVIEWER_MODE, reviewer=True)
            logger.warning("surface_authority_call_failed", outcome="fail_open", tool=tool_name, exc_info=True)
            return await call_next(context)
        if tool_name in resolved.tools:
            return await call_next(context)
        return self._deny(tool_name=tool_name, mode=resolved.mode, reviewer=resolved.mode == _REVIEWER_MODE)

    @staticmethod
    def _deny(*, tool_name: str, mode: str, reviewer: bool = False) -> ToolResult:
        """Return a structured denial.

        The agent payload names the config flag that turns the tool on, when
        there is one. The REVIEWER payload (PRD-SEC-015-FR04) names no way to
        widen the lane: a denial that tells the bounded lane how to widen
        itself is not a control (US-002). Every denial is logged as a
        structured WARNING (P2d) so containment is observable.
        """
        if reviewer:
            logger.warning(
                "surface_authority_call_denied",
                component="surface_authority",
                op="call_tool",
                tool=tool_name,
                surface_role=_REVIEWER_MODE,
                mode=mode,
            )
            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=(
                            f"{tool_name} is outside the read-only reviewer tool surface. "
                            f"This lane may call only: {', '.join(sorted(REVIEWER_TOOLS))}. "
                            "Report the finding in your answer instead; the dispatching "
                            "orchestrator records anything durable."
                        ),
                    )
                ],
                structured_content={
                    "error_type": "tool_not_in_reviewer_surface",
                    "tool_name": tool_name,
                    "surface_role": _REVIEWER_MODE,
                    "allowed_tools": sorted(REVIEWER_TOOLS),
                },
            )
        flag = _gating_flag(tool_name)
        payload: dict[str, Any] = {"error_type": "tool_not_in_surface", "tool_name": tool_name}
        if flag:
            payload["enable_with"] = f"{flag}: true in .trw/config.yaml"
        logger.warning(
            "surface_authority_call_denied",
            component="surface_authority",
            op="call_tool",
            tool=tool_name,
            mode=mode,
            flag=flag,
        )
        message = (
            f"{tool_name} is off in this project. Set {flag}: true in .trw/config.yaml to turn it on."
            if flag
            else f"{tool_name} is not on this server's tool surface."
        )
        return ToolResult(
            content=[TextContent(type="text", text=message)],
            structured_content=payload,
        )


__all__ = ["SurfaceAuthorityMiddleware", "reset_surface_authority_state"]
