"""Phase-aware tool exposure middleware — PRD-INTENT-002.

``PhaseExposureMiddleware`` filters the ``list_tools`` catalogue to the active
phase's allowed subset ∪ the Safe Set ∪ any active session overrides (FR03),
and denies masked tool calls with a structured ``tool_not_in_phase`` error
(FR05). The per-phase policy is the RESOLVED profile's ``allowed_tools_by_phase``
(PROF-001's single-source policy surface) — the middleware NEVER defines its own
table; it consumes :func:`phase_policy.from_resolved_allowlist`.

Fail-open contract (NFR02): ANY policy-resolution error exposes the FULL preset
(``list_tools``) or executes the call (``on_call_tool``) rather than locking
tools away — a broken gate must never brick a session. Every fail-open path logs
a warning.

Composition: this middleware sits AFTER ``MCPSecurityMiddleware`` (so the
public allowlist filter already applied — we compose, not bypass) and AFTER
``CeremonyMiddleware`` (session state resolved first), BEFORE
``ContextBudgetMiddleware`` (FR08 relative ordering).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

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
from trw_mcp.middleware._phase_transitions import (
    client_supports_list_changed,
    detect_transition,
    emit_list_changed,
    resolve_transition_action,
    set_list_changed_capability,
)
from trw_mcp.models.phase_policy import (
    DEFAULT_PHASE_POLICY,
    RIGID_TOOLS,
    PhaseToolPolicy,
    from_resolved_allowlist,
)

logger = structlog.get_logger(__name__)

_DEFAULT_PHASE = "RESEARCH"


def _advertises_list_changed(message: object) -> bool:
    """Best-effort: did the client advertise ``tools.listChanged`` support?

    The MCP client capability surface does not expose a first-class tools
    capability today, so we probe the ``experimental`` capability bag for a
    ``tools.listChanged`` opt-in. Absence → False (the safe default; the
    profile's ``on_transition`` policy then governs the refresh path).
    """
    try:
        capabilities = getattr(message, "capabilities", None)
        experimental = getattr(capabilities, "experimental", None)
        if isinstance(experimental, dict):
            tools_cap = experimental.get("tools")
            if isinstance(tools_cap, dict):
                return bool(tools_cap.get("listChanged"))
            return bool(experimental.get("tools.listChanged"))
        return False
    except Exception:  # justified: fail-open — unknown capability shape → unsupported
        return False


def resolve_active_phase(
    *,
    session_id: str = "",
    fastmcp_context: object | None = None,
) -> str:
    """Return the active phase (uppercase) from the session's run.yaml (FR02).

    Same source as ``CeremonyMiddleware``: ``run_dir/meta/run.yaml`` ``phase``.
    Defaults to ``RESEARCH`` when no active run exists or run.yaml is
    unreadable (the maximally-restricted but functional default, per the PRD
    Failure Mode table). Fail-open: any error returns ``RESEARCH``, never raises.
    """
    try:
        run_dir = resolve_run_dir_for_session(session_id=session_id, fastmcp_context=fastmcp_context)
        if run_dir is None:
            return _DEFAULT_PHASE
        from trw_mcp.models.run import RunState
        from trw_mcp.state.persistence import FileStateReader

        run_yaml = Path(run_dir) / "meta" / "run.yaml"
        if not run_yaml.exists():
            return _DEFAULT_PHASE
        data = FileStateReader().read_yaml(run_yaml)
        state = RunState.model_validate(data)
        # use_enum_values=True → state.phase is the lowercase string value.
        return str(state.phase).strip().upper() or _DEFAULT_PHASE
    except Exception:  # justified: fail-open — default to the safe phase, never raise
        logger.warning("phase_resolution_failed", outcome="default_research", exc_info=True)
        return _DEFAULT_PHASE


def _resolve_policy(
    *,
    session_id: str = "",
    fastmcp_context: object | None = None,
) -> PhaseToolPolicy:
    """Resolve the active policy from the resolved profile (single source).

    Reads ``ResolvedProfile.profile.allowed_tools_by_phase`` via the PROF-001
    session-resolve path. Threads the session's run dir (and its ``.trw`` dir)
    into :func:`resolve_session_profile` so the FULL layer chain — org / domain
    / task / session overlays under ``.trw/profiles/`` — is consulted, honoring
    PROF-001's single-source claim (FR-14). Without the run dir, only the
    defaults + client layers reach the policy and an ``org.yaml``
    ``allowed_tools_by_phase`` would silently never apply. Fail-open to
    :data:`DEFAULT_PHASE_POLICY` when the profile system is unavailable.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.profile import resolve_session_profile

        run_dir = resolve_run_dir_for_session(session_id=session_id, fastmcp_context=fastmcp_context)
        trw_dir = _resolve_trw_dir_for_run(run_dir)
        resolved = resolve_session_profile(get_config(), run_dir=run_dir, trw_dir=trw_dir)
        allow = getattr(resolved.profile, "allowed_tools_by_phase", None)
        return from_resolved_allowlist(allow)
    except Exception:  # justified: fail-open — fall back to the default seed policy
        logger.warning("phase_policy_resolution_failed", exc_info=True)
        return DEFAULT_PHASE_POLICY


def _resolve_trw_dir_for_run(run_dir: Path | None) -> Path | None:
    """Resolve the ``.trw`` dir that hosts the persistent profile layers.

    The persistent profile layers (``org.yaml`` etc.) live under
    ``<trw_dir>/profiles/``. Prefer the ``.trw`` ancestor of the active run
    dir; fall back to the process-resolved ``.trw`` dir. Fail-open: ``None``
    skips persistent-layer discovery (defaults + session + client only).
    """
    try:
        from trw_mcp.state._paths import resolve_trw_dir

        return resolve_trw_dir()
    except Exception:  # justified: fail-open — skip persistent layers on error
        logger.warning("phase_policy_trw_dir_resolve_failed", exc_info=True)
        return None


class PhaseExposureMiddleware(Middleware):
    """Filter the tool catalogue + deny masked calls per the active phase."""

    mounted_in_chain = True

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        policy: PhaseToolPolicy | None = None,
    ) -> None:
        """Build the middleware.

        Args:
            enabled: Feature flag. ``None`` resolves ``phase_exposure_enabled``
                from config at construction (default false for v1 rollout).
            policy: Explicit policy override (tests). ``None`` resolves the
                policy from the resolved profile at request time.
        """
        self._enabled = self._resolve_enabled(enabled)
        self._policy_override = policy

    @staticmethod
    def _resolve_enabled(enabled: bool | None) -> bool:
        if enabled is not None:
            return enabled
        try:
            from trw_mcp.models.config import get_config

            return bool(getattr(get_config(), "phase_exposure_enabled", False))
        except Exception:  # justified: fail-open config read — default disabled
            logger.warning("phase_exposure_enabled_resolve_failed", exc_info=True)
            return False

    def _policy(
        self,
        *,
        session_id: str = "",
        fastmcp_context: object | None = None,
    ) -> PhaseToolPolicy:
        if self._policy_override is not None:
            return self._policy_override
        return _resolve_policy(session_id=session_id, fastmcp_context=fastmcp_context)

    async def on_initialize(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """Persist the client's advertised tools.listChanged capability (FR05b).

        Best-effort: clients rarely advertise this explicitly, so absence
        defaults to unsupported and the per-profile ``on_transition`` policy
        decides the refresh path on a transition.
        """
        result = await call_next(context)
        try:
            ctx = context.fastmcp_context
            session_id = safe_session_id_from_context(ctx)
            if session_id:
                set_list_changed_capability(session_id, advertised=_advertises_list_changed(context.message))
        except Exception:  # justified: fail-open — capability capture is advisory
            logger.warning("phase_capability_capture_failed", exc_info=True)
        return result

    async def _on_phase_transition(self, *, session_id: str, phase: str, ctx: object | None) -> None:
        """Refresh the client tool view on a phase transition (FR04/FR05b).

        Resolves the (advertised capability × profile on_transition) action and
        either emits ``notifications/tools/list_changed`` (notify), records the
        ``X-Phase-Changed`` reconnect signal (require_reconnect), or no-ops
        (silent). Fail-open throughout.
        """
        try:
            if not detect_transition(session_id, phase):
                return
            advertised = client_supports_list_changed(session_id)
            action = resolve_transition_action(advertised=advertised, on_transition=self._on_transition_policy())
            if action == "notify":
                # FR04/FR05b notify path: emit notifications/tools/list_changed
                # (session capability key: tools_list_changed) so capable
                # clients re-fetch tools/list after the transition.
                await emit_list_changed(ctx)
            elif action == "require_reconnect":
                # The transport layer surfaces ``X-Phase-Changed`` so the client
                # reconnects and re-fetches tools/list (FR05b reconnect path).
                logger.info(
                    "phase_transition_require_reconnect",
                    component="phase_exposure",
                    op="on_transition",
                    session_id=session_id,
                    phase=phase,
                    header="X-Phase-Changed",
                )
            # silent → no notification; stale cache until next connect.
        except Exception:  # justified: fail-open — refresh must not break list_tools
            logger.warning("phase_transition_refresh_failed", exc_info=True)

    @staticmethod
    def _on_transition_policy() -> str:
        try:
            from trw_mcp.models.config import get_config

            return str(getattr(get_config().client_profile, "on_transition", "require_reconnect"))
        except Exception:  # justified: fail-open — default to the safest policy
            logger.warning("phase_on_transition_resolve_failed", exc_info=True)
            return "require_reconnect"

    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: CallNext[ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Filter the advertised tools to the active phase subset (FR03)."""
        tools = list(await call_next(context))
        if not self._enabled:
            return tools
        try:
            ctx = context.fastmcp_context
            session_id = safe_session_id_from_context(ctx)
            phase = resolve_active_phase(session_id=session_id, fastmcp_context=ctx)
            # FR04/FR05b: a phase transition refreshes the client tool view.
            await self._on_phase_transition(session_id=session_id, phase=phase, ctx=ctx)
            visible = self._policy(session_id=session_id, fastmcp_context=ctx).list_for(phase) | RIGID_TOOLS
            visible |= self._active_override_tools(session_id)
            filtered = [t for t in tools if t.name in visible]
            logger.debug(
                "phase_exposure_filtered",
                component="phase_exposure",
                op="list_tools",
                phase=phase,
                total=len(tools),
                visible=len(filtered),
            )
            return filtered
        except Exception:  # justified: fail-open — expose the full catalogue
            logger.warning("phase_exposure_list_failed", outcome="fail_open", exc_info=True)
            return tools

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Deny a masked tool call with a structured error (FR05)."""
        if not self._enabled:
            return await call_next(context)
        tool_name = context.message.name
        try:
            ctx = context.fastmcp_context
            session_id = safe_session_id_from_context(ctx)
            phase = resolve_active_phase(session_id=session_id, fastmcp_context=ctx)
            visible = self._policy(session_id=session_id, fastmcp_context=ctx).list_for(phase) | RIGID_TOOLS
            if tool_name in visible:
                return await call_next(context)
            # Masked: an active single-use override grants exactly one call.
            if self._consume_override(session_id, tool_name):
                logger.info(
                    "phase_override_call_allowed",
                    component="phase_exposure",
                    op="call_tool",
                    session_id=session_id,
                    tool=tool_name,
                    phase=phase,
                )
                return await call_next(context)
            self._emit_mask_event(
                session_id=session_id,
                fastmcp_context=ctx,
                tool_name=tool_name,
                phase=phase,
            )
            return self._deny(tool_name=tool_name, phase=phase, available=visible)
        except Exception:  # justified: fail-open — execute rather than wrongly block
            logger.warning("phase_exposure_call_failed", outcome="fail_open", tool=tool_name, exc_info=True)
            return await call_next(context)

    # ── override helpers ────────────────────────────────────────────────

    @staticmethod
    def _active_override_tools(session_id: str) -> frozenset[str]:
        try:
            from trw_mcp.tools.phase_overrides import _overrides, has_active_override

            return frozenset(
                tool for (sid, tool) in list(_overrides) if sid == session_id and has_active_override(sid, tool)
            )
        except Exception:  # justified: fail-open — no overrides on lookup error
            logger.warning("phase_override_scan_failed", exc_info=True)
            return frozenset()

    @staticmethod
    def _consume_override(session_id: str, tool_name: str) -> bool:
        try:
            from trw_mcp.tools.phase_overrides import consume_override

            return consume_override(session_id, tool_name)
        except Exception:  # justified: fail-open — no override consumed on error
            logger.warning("phase_override_consume_failed", exc_info=True)
            return False

    # ── denial + telemetry ──────────────────────────────────────────────

    @staticmethod
    def _deny(*, tool_name: str, phase: str, available: frozenset[str]) -> ToolResult:
        """Return a SchemaNudge-compatible tool_not_in_phase error (FR05)."""
        payload: dict[str, Any] = {
            "error_type": "tool_not_in_phase",
            "tool_name": tool_name,
            "current_phase": phase,
            "available_tools": sorted(available),
            "override_hint": (
                f"call trw_request_tool_access(tool_name='{tool_name}', reason='...') to grant one masked call"
            ),
        }
        message = f"{tool_name} is not available in phase {phase}. Use a phase-appropriate tool or request an override."
        return ToolResult(
            content=[TextContent(type="text", text=message)],
            structured_content=payload,
        )

    @staticmethod
    def _emit_mask_event(
        *,
        session_id: str,
        fastmcp_context: object | None,
        tool_name: str,
        phase: str,
    ) -> None:
        """Append one PhaseExposureEvent to mask_events.jsonl (FR07).

        Fail-open: telemetry must never block the denial response. No tool
        arguments are emitted (NFR03).
        """
        try:
            run_dir = resolve_run_dir_for_session(session_id=session_id, fastmcp_context=fastmcp_context)
            if run_dir is None:
                return
            from trw_mcp.telemetry.event_base import PhaseExposureEvent

            run_id = Path(run_dir).name
            event = PhaseExposureEvent(
                session_id=session_id or "unknown",
                run_id=run_id,
                payload={
                    "event_type": "mask_denied",
                    "tool_name": tool_name,
                    "phase": phase,
                    "session_id": session_id,
                    "run_id": run_id,
                },
            )
            telemetry_dir = Path(run_dir) / "telemetry"
            telemetry_dir.mkdir(parents=True, exist_ok=True)
            events_file = telemetry_dir / "mask_events.jsonl"
            with events_file.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
        except Exception:  # justified: fail-open — telemetry must not block denial
            logger.warning("phase_mask_event_emit_failed", exc_info=True)


__all__ = ["PhaseExposureMiddleware", "resolve_active_phase"]
