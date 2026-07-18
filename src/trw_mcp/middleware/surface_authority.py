"""Surface-authority masking middleware — PRD-CORE-218 FR03/FR04 activation.

Makes the kernel/pack resolver
(``server/_surface_manifest_registry.resolve_tool_surface``) the PRODUCTION
tool-exposure authority by MASKING the advertised + callable tool surface at the
middleware layer, superseding the removed PRD-CORE-125 preset filter
(``server/_tools._apply_tool_exposure_filter``).

Masking (not boot-time deregistration) is deliberate: ``trw_request_tool_access``
(``tools/phase_overrides``) unmasks a pack tool per-session at this same middleware
layer, so every pack tool MUST stay registered and grantable — a deregistered
tool could never be granted.

Resolved surface = ``resolve_tool_surface(task_type, mode).tools`` where:
  * ``mode = get_config().tool_resolution_mode`` — ``"all"`` makes the middleware
    a strict no-op (the documented operator escape; an already-live config field),
    ``"standard"`` (the default) applies the bounded kernel + task packs;
  * ``task_type`` comes from the session's active run (``meta/run.yaml`` ``task_type``,
    resolved through the SAME pin path PhaseExposureMiddleware uses); no run or an
    unmapped task → ``None`` → kernel only.

Always additionally exposed so a bounded surface can never brick a session:
  * ``RIGID_TOOLS`` (``models/phase_policy`` — the never-hide ceremony gates:
    ``trw_build_check`` is not kernel, so without this a kernel-only surface would
    lock out validation),
  * bootstrap ``trw_init`` (a fresh no-run session must be able to CREATE its
    first run; ``trw_init`` is in the ``run_maintenance`` pack — audit finding
    P2b), and
  * active per-session ``trw_request_tool_access`` grants (``tools/phase_overrides``
    — the SAME in-memory store PhaseExposureMiddleware consults; reused, not forked).

When a session's resolved surface CHANGES (e.g. its run's ``task_type`` shifts,
remapping the packs) the LIST path emits ``notifications/tools/list_changed`` via
the shared ``_phase_transitions.emit_list_changed`` helper so a capable client
re-fetches (P2a). Every denial is logged as a structured warning (P2d).

Denial (``on_call_tool``) returns a structured error naming
``trw_request_tool_access`` and the pack(s) that contain the tool (from
``PACK_TOOLS``) — discoverability is the contract.

Fail-open contract (NFR02): ANY resolution error exposes the FULL catalogue
(``on_list_tools``) or executes the call (``on_call_tool``), exactly like
``PhaseExposureMiddleware`` — a broken gate must never brick a session; every
fail-open path logs a warning.

Composition: registered BEFORE ``PhaseExposureMiddleware`` so phase masking
composes WITHIN the CORE-218 surface (surface-authority narrows to the task
packs first, then phase exposure narrows to the phase subset).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import structlog
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools import Tool, ToolResult
from mcp.types import CallToolRequestParams, ListToolsRequest, TextContent

from trw_mcp.middleware._phase_session import (
    resolve_run_dir_for_session,
    safe_session_id_from_context,
)
from trw_mcp.middleware._phase_transitions import emit_list_changed
from trw_mcp.models.phase_policy import RIGID_TOOLS

logger = structlog.get_logger(__name__)

#: Bootstrap-critical tools that must reach EVERY session regardless of the
#: resolved surface — same never-hide rationale as ``RIGID_TOOLS``. A fresh
#: session with no run must be able to call ``trw_init`` to CREATE its first run;
#: ``trw_init`` lives in the ``run_maintenance`` pack, so a kernel-only surface
#: would otherwise strand a brand-new session (round-1 audit finding P2b). NOT
#: added to ``KERNEL_TOOLS`` — the kernel digest is version-pinned.
_BOOTSTRAP_TOOLS: frozenset[str] = frozenset({"trw_init"})

#: The never-hide set unioned into every bounded surface.
_ALWAYS_EXPOSED: frozenset[str] = RIGID_TOOLS | _BOOTSTRAP_TOOLS

#: session_id -> last resolved base surface (P2a change detection). Process-local.
_last_surface: dict[str, frozenset[str]] = {}


def reset_surface_authority_state() -> None:
    """Clear the per-session surface-change ledger — for testing only."""
    _last_surface.clear()


class _Resolved(NamedTuple):
    """A resolved bounded surface plus the inputs that produced it."""

    mode: str
    task_type: str | None
    tools: frozenset[str]


def resolve_task_type(
    *,
    session_id: str = "",
    fastmcp_context: object | None = None,
) -> str | None:
    """Return the active run's ``task_type`` (or ``None`` when there is no run).

    Same source as ``PhaseExposureMiddleware.resolve_active_phase`` — the pinned
    run's ``meta/run.yaml`` — so the surface authority reads a consistent task
    context across the middleware chain. ``None`` (no run / unreadable run.yaml)
    resolves to the kernel-only surface. Fail-open: any error returns ``None``.
    """
    try:
        run_dir = resolve_run_dir_for_session(session_id=session_id, fastmcp_context=fastmcp_context)
        if run_dir is None:
            return None
        from trw_mcp.models.run import RunState
        from trw_mcp.state.persistence import FileStateReader

        run_yaml = Path(run_dir) / "meta" / "run.yaml"
        if not run_yaml.exists():
            return None
        data = FileStateReader().read_yaml(run_yaml)
        state = RunState.model_validate(data)
        task_type = str(state.task_type).strip()
        return task_type or None
    except Exception:  # justified: fail-open — no run context, resolve to kernel-only
        logger.warning("surface_authority_task_type_failed", outcome="kernel_only", exc_info=True)
        return None


def _resolve_mode() -> str:
    """Return the configured ``tool_resolution_mode`` (``standard`` default).

    Not wrapped in fail-open here: a config-read failure propagates to the
    caller's outer ``except`` so the middleware fails OPEN (full surface), never
    silently masking under an unknown mode.
    """
    from trw_mcp.models.config import get_config

    return str(getattr(get_config(), "tool_resolution_mode", "standard"))


def _active_override_tools(session_id: str) -> frozenset[str]:
    """Session-scoped active override grants (the phase_overrides store).

    Reused, NOT forked: reads the SAME ``tools.phase_overrides._overrides`` ledger
    ``PhaseExposureMiddleware`` consults, so a grant unmasks the tool for both
    gates uniformly.
    """
    try:
        from trw_mcp.tools.phase_overrides import _overrides, has_active_override

        return frozenset(
            tool for (sid, tool) in list(_overrides) if sid == session_id and has_active_override(sid, tool)
        )
    except Exception:  # justified: fail-open — no overrides on lookup error
        logger.warning("surface_authority_override_scan_failed", exc_info=True)
        return frozenset()


def _consume_override(session_id: str, tool_name: str) -> bool:
    """Consume (single-use) an active override for the pair (phase_overrides store)."""
    try:
        from trw_mcp.tools.phase_overrides import consume_override

        return consume_override(session_id, tool_name)
    except Exception:  # justified: fail-open — no override consumed on error
        logger.warning("surface_authority_override_consume_failed", exc_info=True)
        return False


def _packs_for_tool(tool_name: str) -> list[str]:
    """Return the capability pack(s) that contain ``tool_name`` (discoverability)."""
    try:
        from trw_mcp.models.surface_packs import PACK_TOOLS

        return [pack for pack, tools in PACK_TOOLS.items() if tool_name in tools]
    except Exception:  # justified: fail-open — empty pack list still yields a usable hint
        logger.warning("surface_authority_pack_lookup_failed", exc_info=True)
        return []


class SurfaceAuthorityMiddleware(Middleware):
    """Mask the tool catalogue + deny calls to tools outside the resolved surface."""

    mounted_in_chain = True

    def _resolve(self, *, session_id: str, fastmcp_context: object | None) -> _Resolved | None:
        """Resolve the bounded surface (WITHOUT single-use override grants).

        Returns ``None`` when ``tool_resolution_mode == "all"`` — the caller then
        treats the middleware as a strict no-op (full exposure). Otherwise the
        bounded set = ``resolve_tool_surface(task_type, "standard").tools`` ∪
        :data:`_ALWAYS_EXPOSED` (RIGID + bootstrap ``trw_init``). Override grants
        are layered in by the LIST path only (see :meth:`on_list_tools`); the CALL
        path consumes them explicitly so the single-use invariant holds. ``mode``
        and ``task_type`` ride along so a denial can be logged observably (P2d).
        """
        mode = _resolve_mode()
        if mode == "all":
            return None
        from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

        task_type = resolve_task_type(session_id=session_id, fastmcp_context=fastmcp_context)
        surface = set(resolve_tool_surface(task_type, "standard").tools)
        surface |= _ALWAYS_EXPOSED
        return _Resolved(mode=mode, task_type=task_type, tools=frozenset(surface))

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
            resolved = self._resolve(session_id=session_id, fastmcp_context=ctx)
            if resolved is None:  # mode="all" → strict no-op (operator escape)
                return tools
            await self._maybe_notify_surface_change(session_id, resolved.tools, ctx)
            visible = resolved.tools | _active_override_tools(session_id)
            filtered = [t for t in tools if t.name in visible]
            logger.debug(
                "surface_authority_filtered",
                component="surface_authority",
                op="list_tools",
                task_type=resolved.task_type,
                total=len(tools),
                visible=len(filtered),
            )
            return filtered
        except Exception:  # justified: fail-open — expose the full catalogue
            logger.warning("surface_authority_list_failed", outcome="fail_open", exc_info=True)
            return tools

    @staticmethod
    async def _maybe_notify_surface_change(
        session_id: str, surface: frozenset[str], fastmcp_context: object | None
    ) -> None:
        """Emit ``notifications/tools/list_changed`` when a session's resolved
        surface CHANGES (P2a) — e.g. its run's ``task_type`` shifted, remapping the
        packs. Mirrors PhaseExposureMiddleware's ``emit_list_changed`` path so a
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
        """Deny a call to a tool outside the resolved surface (FR03/FR04)."""
        tool_name = context.message.name
        try:
            ctx = context.fastmcp_context
            session_id = safe_session_id_from_context(ctx)
            resolved = self._resolve(session_id=session_id, fastmcp_context=ctx)
            if resolved is None:  # mode="all" → strict no-op (operator escape)
                return await call_next(context)
            if tool_name in resolved.tools:
                return await call_next(context)
            # Outside the surface: an active single-use grant permits exactly one call.
            if _consume_override(session_id, tool_name):
                logger.info(
                    "surface_authority_override_call_allowed",
                    component="surface_authority",
                    op="call_tool",
                    session_id=session_id,
                    tool=tool_name,
                )
                return await call_next(context)
            return self._deny(tool_name=tool_name, mode=resolved.mode, task_type=resolved.task_type)
        except Exception:  # justified: fail-open — execute rather than wrongly block
            logger.warning("surface_authority_call_failed", outcome="fail_open", tool=tool_name, exc_info=True)
            return await call_next(context)

    @staticmethod
    def _deny(*, tool_name: str, mode: str, task_type: str | None) -> ToolResult:
        """Return a structured ``tool_not_in_surface`` denial (discoverability contract).

        Names ``trw_request_tool_access`` (the remediation) and the pack(s) that
        contain the tool so the caller knows exactly how to reach it. Every denial
        is also logged as a structured WARNING (P2d) so masking is observable.
        """
        packs = _packs_for_tool(tool_name)
        pack_txt = ", ".join(packs) if packs else "operator-only / unmapped"
        payload: dict[str, Any] = {
            "error_type": "tool_not_in_surface",
            "tool_name": tool_name,
            "packs": packs,
            "override_hint": (
                f"call trw_request_tool_access(tool_name='{tool_name}', reason='...') to grant one call, "
                "or set tool_resolution_mode='all' for the full surface"
            ),
        }
        # P2d: denials are observable — one structured event per masked call.
        logger.warning(
            "surface_authority_call_denied",
            component="surface_authority",
            op="call_tool",
            tool=tool_name,
            task_type=task_type,
            mode=mode,
            packs=packs,
        )
        message = (
            f"{tool_name} is outside the resolved tool surface (pack(s): {pack_txt}). "
            f"Request one-time access with trw_request_tool_access, or set tool_resolution_mode='all'."
        )
        return ToolResult(
            content=[TextContent(type="text", text=message)],
            structured_content=payload,
        )


__all__ = ["SurfaceAuthorityMiddleware", "reset_surface_authority_state", "resolve_task_type"]
