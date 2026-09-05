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
remapping the packs) BOTH the list path and the CALL path emit
``notifications/tools/list_changed`` via the shared
``_phase_transitions.emit_list_changed`` helper so a capable client re-fetches
(P2a + PRD-CORE-246-FR07). The call-path emission is what a real client needs:
it lists ONCE at connect and never re-asks on its own, so a widening caused BY a
tool call (``trw_init``, ``trw_adopt_run``) was previously invisible to it
forever. The re-resolution happens AFTER ``call_next`` returns — before it the
run is not yet pinned and the surface has not yet widened, so resolving first
would compare the old surface with itself and never notify. Every denial is
logged as a structured warning (P2d).

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
from trw_mcp.models.surface_packs import REVIEWER_TOOLS
from trw_mcp.state._surface_role import _env_marks_reviewer as _surface_role_env_marks_reviewer
from trw_mcp.state._surface_role import reset_surface_role_state, reviewer_role_active

logger = structlog.get_logger(__name__)

#: The resolution mode reported for a reviewer-role session (PRD-SEC-015-FR03).
#: Not a ``tool_resolution_mode`` value — the role is a session-IDENTITY axis
#: that is resolved BEFORE the mode and replaces it.
_REVIEWER_MODE = "reviewer"

#: Bootstrap-critical tools that must reach EVERY session regardless of the
#: resolved surface — same never-hide rationale as ``RIGID_TOOLS``. NOT added to
#: ``KERNEL_TOOLS`` (the kernel digest is version-pinned) and NOT to
#: ``RIGID_TOOLS`` (that is the phase-gate never-hide set, and joining it would
#: also change ``PhaseToolPolicy``). Pack membership is unchanged, so the FR01
#: manifest bijection still holds and no new tool is registered.
#:   * ``trw_init`` — a fresh session with no run must be able to CREATE its
#:     first run; ``trw_init`` lives in the ``run_maintenance`` pack, so a
#:     kernel-only surface would otherwise strand a brand-new session (round-1
#:     audit finding P2b).
#:   * ``trw_submit_feedback`` — the tooling-gap report channel. It is the sole
#:     member of the ``feedback`` pack, which NO entry of ``STANDARD_TASK_PACKS``
#:     names, so before PRD-CORE-246-FR06 it was masked on every resolved
#:     surface: an agent that hit a tooling gap could not report the gap it had
#:     hit. It IS in the phase-exposure Safe Set, but this middleware runs first
#:     in the chain and masked it before phase exposure was ever consulted.
#:   * ``trw_prd_validate`` — the read-only, no-side-effect requirement-quality
#:     check. It is a member of the ``requirements`` pack, which only the
#:     ``docs``/``planning`` entries of ``STANDARD_TASK_PACKS`` name, so a
#:     session pinned ``task_type=coding`` (or any other unnamed type) masked
#:     it on every resolved surface — and a ``trw-prd-groomer`` /
#:     ``trw-requirement-reviewer`` sub-agent dispatched from that session
#:     shares the SAME stdio connection and therefore the SAME masked surface
#:     (session id comes from ``fastmcp_context.session_id`` — there is no
#:     separate connection per dispatched agent), so it inherited the mask too
#:     and could not call the validator it is grafted to (2026-09-04
#:     wiring-defect report, ``docs/documentation/wiring-defect-patterns.md``
#:     P12). Read-only and side-effect-free, so never-hiding it carries none of
#:     the risk a write tool would.
_BOOTSTRAP_TOOLS: frozenset[str] = frozenset({"trw_init", "trw_submit_feedback", "trw_prd_validate"})

#: The never-hide set unioned into every bounded surface.
_ALWAYS_EXPOSED: frozenset[str] = RIGID_TOOLS | _BOOTSTRAP_TOOLS

#: session_id -> last resolved base surface (P2a change detection). Process-local.
_last_surface: dict[str, frozenset[str]] = {}


def reset_surface_authority_state() -> None:
    """Clear the per-session surface-change ledger — for testing only."""
    _last_surface.clear()
    reset_surface_role_state()


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


# PRD-SEC-015: the reviewer-role identity lives in ONE place —
# ``state/_surface_role.py`` — and is consulted by every side-effecting layer
# (telemetry, recall, code index, skill tracking, boot). The names below are kept
# for the call sites and tests that import them from this module.
_is_reviewer_role = reviewer_role_active
_env_marks_reviewer = _surface_role_env_marks_reviewer


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
        # PRD-SEC-015-FR03: the session-IDENTITY axis is answered first. The
        # reviewer branch REPLACES the resolved surface — it does not subtract
        # from `_ALWAYS_EXPOSED`, because that set grows by ordinary maintenance
        # (`trw_prd_validate` joined it on 2026-09-04) and a subtractive design
        # would silently re-widen every reviewer surface on the next addition.
        # It also precedes the `mode == "all"` escape: "all" widens an OPERATOR'S
        # OWN session, while the role CONTAINS a subordinate process, so honouring
        # it would let an audited project's config un-bound the lane auditing it.
        # Returning here is also the NFR01 property: no `resolve_task_type` call,
        # so a reviewer call reads strictly less from disk than an agent call.
        if _is_reviewer_role():
            return _Resolved(mode=_REVIEWER_MODE, task_type=None, tools=REVIEWER_TOOLS)
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
            # FR04: a reviewer surface is never widened by a grant — the bound
            # must be unreachable from inside the bounded lane (US-002).
            visible = (
                resolved.tools
                if resolved.mode == _REVIEWER_MODE
                else resolved.tools | _active_override_tools(session_id)
            )
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
        """Deny a call to a tool outside the resolved surface (FR03/FR04).

        An executed call is followed by a surface re-resolution and, when the
        surface moved, a ``list_changed`` push (PRD-CORE-246-FR07).

        The fail-open ``call_next`` fallback below covers ONLY the gating
        resolution (session/mode/task-type lookup) — it must never wrap a
        branch that has already invoked ``call_next``. A tool raising is not
        a resolution failure; letting it propagate here (rather than being
        caught and retried) is what keeps a raising tool executing exactly
        once (audit finding: a raising tool previously ran twice — once
        inside ``call_next`` and once more from this handler's own fail-open
        retry).
        """
        tool_name = context.message.name
        ctx = context.fastmcp_context
        try:
            session_id = safe_session_id_from_context(ctx)
            resolved = self._resolve(session_id=session_id, fastmcp_context=ctx)
        except Exception:
            # NFR02 (see on_list_tools): reviewer-marked processes DENY on a
            # resolution fault. The PRD accepts "the reviewer returns only
            # denials" as the safe degradation for a subordinate lane.
            if _env_marks_reviewer():
                logger.warning(
                    "surface_authority_call_failed", outcome="fail_closed_reviewer", tool=tool_name, exc_info=True
                )
                return self._deny(tool_name=tool_name, mode=_REVIEWER_MODE, task_type=None, reviewer=True)
            logger.warning("surface_authority_call_failed", outcome="fail_open", tool=tool_name, exc_info=True)
            return await call_next(context)
        if resolved is None:  # mode="all" → strict no-op (operator escape)
            return await call_next(context)
        if tool_name in resolved.tools:
            return await self._call_then_push(context, call_next, session_id=session_id, fastmcp_context=ctx)
        if resolved.mode == _REVIEWER_MODE:
            # FR04: the grant store is not consulted AT ALL under the reviewer
            # role, so a grant planted by any path cannot unmask a write tool —
            # and a reviewer denial never burns the parent session's single-use
            # grant either.
            return self._deny(tool_name=tool_name, mode=resolved.mode, task_type=None, reviewer=True)
        # Outside the surface: an active single-use grant permits exactly one call.
        if _consume_override(session_id, tool_name):
            logger.info(
                "surface_authority_override_call_allowed",
                component="surface_authority",
                op="call_tool",
                session_id=session_id,
                tool=tool_name,
            )
            return await self._call_then_push(context, call_next, session_id=session_id, fastmcp_context=ctx)
        return self._deny(tool_name=tool_name, mode=resolved.mode, task_type=resolved.task_type)

    async def _call_then_push(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
        *,
        session_id: str,
        fastmcp_context: object | None,
    ) -> ToolResult:
        """Execute the call, then push the surface if the call changed it (FR07).

        The re-resolution is deliberately AFTER ``call_next``: a call like
        ``trw_init`` creates and pins the run whose ``task_type`` selects the
        packs, so resolving beforehand would compare the pre-call surface with
        itself. The push is wrapped in its own handler — never the caller's —
        because the caller's fail-open branch re-invokes ``call_next``, so a
        notification fault leaking upward would execute the tool a second time.
        """
        result = await call_next(context)
        try:
            resolved = self._resolve(session_id=session_id, fastmcp_context=fastmcp_context)
            if resolved is not None:
                await self._maybe_notify_surface_change(session_id, resolved.tools, fastmcp_context)
        except Exception:  # justified: fail-open — a push fault must never fail the tool call
            logger.warning("surface_authority_call_push_failed", outcome="fail_open", exc_info=True)
        return result

    @staticmethod
    def _deny(*, tool_name: str, mode: str, task_type: str | None, reviewer: bool = False) -> ToolResult:
        """Return a structured denial (discoverability contract).

        The agent payload names ``trw_request_tool_access`` (the remediation) and
        the pack(s) that contain the tool so the caller knows how to reach it.
        The REVIEWER payload (PRD-SEC-015-FR04) deliberately names NEITHER: a
        denial that tells the bounded lane how to widen itself is not a control
        (US-002). Every denial is logged as a structured WARNING (P2d) so
        containment is observable rather than inferred.
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
