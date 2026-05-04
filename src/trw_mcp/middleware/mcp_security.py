"""Mounted MCP security middleware for registry, scope, and anomaly checks."""

from __future__ import annotations

import getpass
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult
from mcp.types import CallToolRequestParams, ListToolsRequest, TextContent

from trw_mcp.middleware._mcp_security_helpers import (
    CLAUDE_CODE_PREFIX as CLAUDE_CODE_PREFIX,
    TRANSPORTS as TRANSPORTS,
    AdvertisedTool as AdvertisedTool,
    MCPSecurityDecision as MCPSecurityDecision,
    MCPSecurityStatusSnapshot as MCPSecurityStatusSnapshot,
    RuntimePeerMetadata as RuntimePeerMetadata,
    Transport,
    _emit_decision,
    _identity_verification,
    _resolve_run_id,
    _resolve_runtime_run_dir,
    normalize_tool_name as normalize_tool_name,
    normalize_transport as normalize_transport,
    resolve_runtime_peer_metadata as resolve_runtime_peer_metadata,
)
from trw_mcp.security.anomaly_detector import (
    AnomalyDetector,
    AnomalyObservation,
    hash_tool_args,
)
from trw_mcp.security.capability_scope import (
    CapabilityScope,
    CapabilityScopeError,
    apply_scope,
    scope_from_allowed_tool,
)
from trw_mcp.security.mcp_registry import ALL_PHASES, ALL_SCOPES, MCPAllowlist, MCPRegistry, RegistryDecision

logger = structlog.get_logger(__name__)


def _safe_session_id(fastmcp_ctx: object | None) -> str:
    """Return ``ctx.session_id`` or ``""`` when no request context exists.

    Recent FastMCP versions raise :class:`RuntimeError` from the
    ``Context.session_id`` property descriptor when accessed outside a
    request context (rather than returning a generated id).  ``getattr``
    only catches :class:`AttributeError`, so we need an explicit
    ``try``/``except`` to keep startup-time and test-time code paths
    (where there is no live MCP session) from blowing up.
    """
    if fastmcp_ctx is None:
        return ""
    try:
        value = fastmcp_ctx.session_id  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return ""
    return value if isinstance(value, str) else ""


class MCPSecurityMiddleware(Middleware):
    """Registry + capability + anomaly middleware mounted in the FastMCP chain."""

    mounted_in_chain = True

    def __init__(
        self,
        *,
        allowlist: MCPAllowlist | None = None,
        registry: MCPRegistry | None = None,
        scopes: Mapping[str, CapabilityScope],
        anomaly_detector: AnomalyDetector,
        run_dir: Path | None = None,
        fallback_dir: Path | None = None,
        default_server_name: str = "trw",
        enforce: bool = True,
        quarantine_auto_release: bool = False,
    ) -> None:
        self._registry = registry or MCPRegistry.from_allowlist(allowlist or MCPAllowlist())
        self._scopes: dict[str, CapabilityScope] = dict(scopes)
        self._detector = anomaly_detector
        self._run_dir = run_dir
        self._fallback_dir = fallback_dir
        self.default_server_name = default_server_name
        self._enforce = enforce
        self._quarantine_auto_release = quarantine_auto_release
        self._recent_anomalies: list[dict[str, Any]] = []

    def _default_scope(
        self,
        *,
        server_name: str,
        tool_name: str,
        auth: RegistryDecision,
    ) -> CapabilityScope | None:
        entry = auth.entry
        if entry is None:
            return None
        allowed_tool = entry.tool_by_name(tool_name)
        if allowed_tool is None:
            return None
        return scope_from_allowed_tool(server_name, allowed_tool)

    def _first_party_tool_scope(self, *, server_name: str, tool_name: str) -> CapabilityScope | None:
        """Return a scope for bundled TRW tools omitted from the signed seed allowlist.

        The signed allowlist intentionally moves slowly; the live in-process
        TRW server can grow new first-party tools faster than that file is
        re-signed.  For the trusted default server only, bridge that drift from
        ``TOOL_PRESETS["all"]`` so advertisements and direct calls stay aligned
        with the configured first-party surface while non-TRW/unknown tools
        remain denied.
        """
        if server_name != self.default_server_name:
            return None
        from trw_mcp.models.config._defaults import TOOL_PRESETS

        if tool_name not in TOOL_PRESETS["all"]:
            return None
        return CapabilityScope(
            server_name=server_name,
            tool_name=tool_name,
            allowed_phases=ALL_PHASES,
            allowed_scopes=ALL_SCOPES,
        )

    def _record_anomalies(
        self,
        *,
        fired: Sequence[str],
        transport: Transport,
        server: str,
        tool: str,
        args_hash: str,
        run_id: str | None,
        session_id: str,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        for anomaly_type in fired:
            record = {
                "ts": now,
                "transport": transport,
                "server": server,
                "tool": tool,
                "type": anomaly_type,
                "run_id": run_id or "",
                "session_id": session_id,
                "arg_hash": args_hash,
            }
            self._recent_anomalies.append(record)
        self._recent_anomalies = self._recent_anomalies[-25:]

    def filter_advertised_tools(
        self,
        *,
        transport: str,
        advertisements: Sequence[AdvertisedTool],
        session_id: str = "",
        run_id: str | None = None,
        current_phase: str | None = None,
        fastmcp_context: object | None = None,
    ) -> list[AdvertisedTool]:
        normalized_transport = normalize_transport(transport)
        resolved_run_dir = _resolve_runtime_run_dir(
            configured_run_dir=self._run_dir,
            session_id=session_id,
            fastmcp_context=fastmcp_context,
        )
        resolved_run_id = run_id or _resolve_run_id(resolved_run_dir)
        allowed: list[AdvertisedTool] = []
        for ad in advertisements:
            runtime_peer = resolve_runtime_peer_metadata(
                raw_tool=ad.name,
                explicit_server=ad.server,
                default_server_name=self.default_server_name,
                fastmcp_context=fastmcp_context,
            )
            auth = self._registry.authorize_server(
                runtime_peer.server,
                observed_fingerprint=runtime_peer.observed_fingerprint,
                auto_release=self._quarantine_auto_release,
            )
            resolved_server = auth.entry.name if auth.entry is not None else runtime_peer.server
            layers = ["registry"]
            reason = auth.reason
            scope = self._scopes.get(runtime_peer.tool) or self._default_scope(
                server_name=resolved_server,
                tool_name=runtime_peer.tool,
                auth=auth,
            )
            if scope is None and auth.allowed:
                scope = self._first_party_tool_scope(
                    server_name=resolved_server,
                    tool_name=runtime_peer.tool,
                )
            try:
                if auth.allowed and scope is not None:
                    apply_scope(
                        server_name=resolved_server,
                        tool_name=runtime_peer.tool,
                        scope=scope,
                        current_phase=current_phase,
                        requested_scope=None,
                    )
                elif auth.allowed and auth.match_type == "unsigned_admission":
                    reason = "unsigned_admission"
            except CapabilityScopeError as exc:
                reason = str(exc)
                auth = auth.model_copy(update={"allowed": False, "reason": reason})
            layers.append("capability_scope")
            is_allowed = auth.allowed and (scope is not None or auth.match_type == "unsigned_admission")
            if not self._enforce:
                is_allowed = True
            if is_allowed:
                allowed.append(
                    AdvertisedTool(
                        server=resolved_server,
                        name=runtime_peer.tool,
                        namespaced_name=ad.name,
                    )
                )
            _emit_decision(
                decision="allow" if is_allowed else "deny",
                transport=normalized_transport,
                server=resolved_server,
                tool=runtime_peer.tool,
                layers_fired=layers,
                run_dir=resolved_run_dir,
                fallback_dir=self._fallback_dir,
                session_id=session_id,
                run_id=resolved_run_id,
                extra={
                    "reason": reason,
                    "allowlist_match_type": auth.match_type,
                    "peer_identity_source": runtime_peer.peer_identity_source,
                    "fingerprint_source": runtime_peer.fingerprint_source,
                    "fingerprint_constraint": runtime_peer.fingerprint_constraint,
                    "identity_verification": _identity_verification(runtime_peer),
                },
            )
        return allowed

    def on_tool_call(
        self,
        *,
        transport: str,
        server: str,
        tool: str,
        args: dict[str, Any] | None = None,
        session_id: str = "",
        run_id: str | None = None,
        current_phase: str | None = None,
        requested_scope: str | None = None,
        observed_fingerprint: str | None = None,
    ) -> MCPSecurityDecision:
        normalized_transport = normalize_transport(transport)
        safe_args = args or {}
        layers_fired: list[str] = []
        resolved_run_dir = _resolve_runtime_run_dir(
            configured_run_dir=self._run_dir,
            session_id=session_id,
        )
        resolved_run_id = run_id or _resolve_run_id(resolved_run_dir)
        runtime_peer = resolve_runtime_peer_metadata(
            raw_tool=tool,
            explicit_server=server,
            default_server_name=self.default_server_name,
        )
        live_fingerprint = observed_fingerprint or runtime_peer.observed_fingerprint
        auth = self._registry.authorize_server(
            runtime_peer.server,
            observed_fingerprint=live_fingerprint,
            auto_release=self._quarantine_auto_release,
        )
        resolved_server = auth.entry.name if auth.entry is not None else runtime_peer.server
        layers_fired.append("registry")

        scope_reason = ""
        scope = self._scopes.get(runtime_peer.tool) or self._default_scope(
            server_name=resolved_server,
            tool_name=runtime_peer.tool,
            auth=auth,
        )
        if scope is None and auth.allowed:
            scope = self._first_party_tool_scope(
                server_name=resolved_server,
                tool_name=runtime_peer.tool,
            )
        try:
            if auth.allowed and scope is not None:
                apply_scope(
                    server_name=resolved_server,
                    tool_name=runtime_peer.tool,
                    scope=scope,
                    current_phase=current_phase,
                    requested_scope=requested_scope,
                )
            elif auth.allowed and auth.match_type == "unsigned_admission":
                scope_reason = ""
            elif auth.allowed:
                scope_reason = "tool_not_in_server_capabilities"
        except CapabilityScopeError as exc:
            scope_reason = str(exc)
        layers_fired.append("capability_scope")

        args_hash = hash_tool_args(safe_args)
        fired = self._detector.observe(
            AnomalyObservation(
                ts=datetime.now(tz=timezone.utc),
                server=resolved_server,
                tool=runtime_peer.tool,
                args_hash=args_hash,
                run_id=run_id,
                session_id=session_id,
            )
        )
        layers_fired.append("anomaly_detector")
        self._record_anomalies(
            fired=fired,
            transport=normalized_transport,
            server=resolved_server,
            tool=runtime_peer.tool,
            args_hash=args_hash,
            run_id=resolved_run_id,
            session_id=session_id,
        )

        reason = auth.reason or scope_reason
        allowed = auth.allowed and not scope_reason
        if self._detector._config.mode == "enforce" and "rate_spike" in fired:
            allowed = False
            reason = "rate_spike"
        if not self._enforce:
            allowed = True
        audit_fields: dict[str, Any] = {}
        if auth.match_type == "unsigned_admission" and allowed:
            audit_fields = {"unsigned_admission": True, "operator": getpass.getuser()}
        elif auth.match_type == "overlay" and allowed:
            audit_fields = {"operator": getpass.getuser(), "operator_overlay_applied": True}
        _emit_decision(
            decision="allow" if allowed else "deny",
            transport=normalized_transport,
            server=resolved_server,
            tool=runtime_peer.tool,
            layers_fired=layers_fired,
            run_dir=resolved_run_dir,
            fallback_dir=self._fallback_dir,
            session_id=session_id,
            run_id=resolved_run_id,
            extra={
                "reason": reason,
                "allowlist_match_type": auth.match_type,
                "arg_hash": args_hash,
                "novel_arg_pattern": "novel_arg_pattern" in fired,
                "drift_detected": auth.drift_detected,
                "quarantine_reason": auth.quarantine_reason,
                "peer_identity_source": runtime_peer.peer_identity_source,
                "fingerprint_source": runtime_peer.fingerprint_source,
                "fingerprint_constraint": runtime_peer.fingerprint_constraint,
                "identity_verification": _identity_verification(runtime_peer),
                "enforced": self._enforce,
                **audit_fields,
            },
        )
        return MCPSecurityDecision(
            allowed=allowed,
            reason=reason,
            transport=normalized_transport,
            server=resolved_server,
            tool=runtime_peer.tool,
            layers_fired=layers_fired,
        )

    def status_snapshot(self) -> MCPSecurityStatusSnapshot:
        return MCPSecurityStatusSnapshot(
            registered_servers=self._registry.registered_servers,
            allowlist_hash=self._registry.allowlist_hash,
            recent_anomalies=list(self._recent_anomalies),
            quarantined_servers=self._registry.quarantined_servers,
        )

    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: CallNext[ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = list(await call_next(context))
        fastmcp_ctx = context.fastmcp_context
        transport = normalize_transport(
            getattr(fastmcp_ctx, "transport", "stdio") if fastmcp_ctx is not None else "stdio"
        )
        allowed_ads = self.filter_advertised_tools(
            transport=transport,
            advertisements=[AdvertisedTool(server=self.default_server_name, name=tool.name) for tool in tools],
            session_id=_safe_session_id(fastmcp_ctx),
            run_id=_resolve_run_id(
                _resolve_runtime_run_dir(
                    configured_run_dir=self._run_dir,
                    session_id=_safe_session_id(fastmcp_ctx),
                    fastmcp_context=fastmcp_ctx,
                )
            ),
            fastmcp_context=fastmcp_ctx,
        )
        allowed_names = {ad.name for ad in allowed_ads}
        return [tool for tool in tools if normalize_tool_name(tool.name) in allowed_names]

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        fastmcp_ctx = context.fastmcp_context
        runtime_peer = resolve_runtime_peer_metadata(
            raw_tool=context.message.name,
            explicit_server=self.default_server_name,
            default_server_name=self.default_server_name,
            fastmcp_context=fastmcp_ctx,
        )
        decision = self.on_tool_call(
            transport=getattr(fastmcp_ctx, "transport", "stdio") if fastmcp_ctx is not None else "stdio",
            server=runtime_peer.server,
            tool=context.message.name,
            args=context.message.arguments or {},
            session_id=_safe_session_id(fastmcp_ctx),
            run_id=_resolve_run_id(
                _resolve_runtime_run_dir(
                    configured_run_dir=self._run_dir,
                    session_id=_safe_session_id(fastmcp_ctx),
                    fastmcp_context=fastmcp_ctx,
                )
            ),
            observed_fingerprint=runtime_peer.observed_fingerprint,
        )
        if not decision.allowed:
            logger.warning(
                "mcp_security_blocked",
                server=decision.server,
                tool=decision.tool,
                reason=decision.reason,
                outcome="blocked",
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"MCP security blocked {decision.tool}: {decision.reason}")],
                structured_content={
                    "error": "mcp_security_blocked",
                    "server": decision.server,
                    "tool": decision.tool,
                    "reason": decision.reason,
                },
            )
        return await call_next(context)


__all__ = [
    "CLAUDE_CODE_PREFIX",
    "TRANSPORTS",
    "AdvertisedTool",
    "MCPSecurityDecision",
    "MCPSecurityMiddleware",
    "MCPSecurityStatusSnapshot",
    "RuntimePeerMetadata",
    "normalize_tool_name",
    "normalize_transport",
    "resolve_runtime_peer_metadata",
]
