"""Mounted MCP security middleware for registry, scope, and anomaly checks."""

from __future__ import annotations

import getpass
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import structlog
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult
from mcp.types import CallToolRequestParams, ListToolsRequest, TextContent
from pydantic import BaseModel, ConfigDict, Field

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
from trw_mcp.security.mcp_registry import MCPAllowlist, MCPRegistry, RegistryDecision
from trw_mcp.telemetry.event_base import MCPSecurityEvent
from trw_mcp.telemetry.unified_events import emit as emit_unified_event

logger = structlog.get_logger(__name__)

Transport = Literal["stdio", "streamable-http", "sse"]
TRANSPORTS: tuple[Transport, ...] = ("stdio", "streamable-http", "sse")
CLAUDE_CODE_PREFIX = "mcp__trw__"


class AdvertisedTool(BaseModel):
    """A tool advertisement emitted by a server before exposure."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    server: str
    name: str
    namespaced_name: str = ""


class MCPSecurityDecision(BaseModel):
    """Result of a tool-call dispatch through the security middleware."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    allowed: bool
    reason: str = ""
    transport: Transport
    server: str
    tool: str
    layers_fired: list[str] = Field(default_factory=list)


class MCPSecurityStatusSnapshot(BaseModel):
    """Structured snapshot returned by the status tool."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    registered_servers: list[str] = Field(default_factory=list)
    allowlist_hash: str = ""
    recent_anomalies: list[dict[str, Any]] = Field(default_factory=list)
    quarantined_servers: list[str] = Field(default_factory=list)


class RuntimePeerMetadata(BaseModel):
    """Resolved runtime peer identity for a live tool dispatch."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    server: str
    tool: str
    observed_fingerprint: str | None = None
    peer_identity_source: str = "default"
    fingerprint_source: str = "unavailable"
    fingerprint_constraint: str = ""


def normalize_tool_name(raw: str) -> str:
    if raw.startswith(CLAUDE_CODE_PREFIX):
        return raw[len(CLAUDE_CODE_PREFIX) :]
    return raw


def normalize_transport(raw: str) -> Transport:
    if raw == "http":
        return "streamable-http"
    if raw not in TRANSPORTS:
        return "stdio"
    return raw


def _resolve_run_id(run_dir: Path | None) -> str | None:
    return run_dir.name if run_dir is not None else None


def _resolve_runtime_run_dir(
    *,
    configured_run_dir: Path | None,
    session_id: str = "",
    fastmcp_context: object | None = None,
) -> Path | None:
    if configured_run_dir is not None:
        return configured_run_dir
    try:
        from trw_mcp.state._paths import TRWCallContext, find_active_run, get_pinned_run, resolve_pin_key

        if fastmcp_context is not None:
            pin_key = resolve_pin_key(fastmcp_context, explicit=session_id or None)
            call_ctx = TRWCallContext(
                session_id=pin_key,
                client_hint=None,
                explicit=bool(session_id),
                fastmcp_session=getattr(fastmcp_context, "session_id", None)
                if isinstance(getattr(fastmcp_context, "session_id", None), str)
                else None,
            )
            return get_pinned_run(context=call_ctx) or find_active_run(context=call_ctx)
        if session_id:
            return get_pinned_run(session_id=session_id) or find_active_run(session_id=session_id)
        return get_pinned_run() or find_active_run()
    except Exception:
        logger.debug("mcp_security_run_dir_resolution_failed", exc_info=True)
        return None


def _split_server_and_tool(
    raw_tool: str,
    *,
    explicit_server: str,
    default_server_name: str,
) -> tuple[str, str, str]:
    normalized = normalize_tool_name(raw_tool)
    if "__" in normalized:
        server_name, tool_name = normalized.split("__", 1)
        if server_name and tool_name:
            return server_name, tool_name, "tool_namespace"
    if explicit_server and explicit_server != default_server_name:
        return explicit_server, normalized, "explicit_server"
    return default_server_name, normalized, "default_server_name_fallback"


def _extract_header(headers: object, key: str) -> str | None:
    if hasattr(headers, "get"):
        value = headers.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def resolve_runtime_peer_metadata(
    *,
    raw_tool: str,
    explicit_server: str,
    default_server_name: str,
    fastmcp_context: object | None = None,
) -> RuntimePeerMetadata:
    server_name, tool_name, peer_identity_source = _split_server_and_tool(
        raw_tool,
        explicit_server=explicit_server,
        default_server_name=default_server_name,
    )
    observed_fingerprint: str | None = None
    fingerprint_source = "unavailable"
    request_context = getattr(fastmcp_context, "request_context", None)
    request = getattr(request_context, "request", None)
    headers = getattr(request, "headers", None)
    for key in ("x-trw-mcp-fingerprint", "x-mcp-server-fingerprint", "mcp-server-fingerprint"):
        value = _extract_header(headers, key)
        if value:
            observed_fingerprint = value
            fingerprint_source = f"request_header:{key}"
            break
    if observed_fingerprint is None:
        for attr_name in ("peer_fingerprint", "public_key_fingerprint", "server_fingerprint"):
            value = getattr(fastmcp_context, attr_name, None)
            if isinstance(value, str) and value:
                observed_fingerprint = value
                fingerprint_source = f"context_attr:{attr_name}"
                break
    fingerprint_constraint = ""
    if observed_fingerprint is None:
        fingerprint_constraint = "runtime_did_not_expose_peer_fingerprint"
    return RuntimePeerMetadata(
        server=server_name,
        tool=tool_name,
        observed_fingerprint=observed_fingerprint,
        peer_identity_source=peer_identity_source,
        fingerprint_source=fingerprint_source,
        fingerprint_constraint=fingerprint_constraint,
    )


def _emit_decision(
    *,
    decision: str,
    transport: Transport,
    server: str,
    tool: str,
    layers_fired: Sequence[str],
    run_dir: Path | None,
    fallback_dir: Path | None,
    session_id: str,
    run_id: str | None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "decision": decision,
        "transport": transport,
        "server": server,
        "tool": tool,
        "layers_fired": list(layers_fired),
    }
    if extra:
        payload.update(dict(extra))
    emit_unified_event(
        MCPSecurityEvent(session_id=session_id or "mcp-security", run_id=run_id, payload=payload),
        run_dir=run_dir,
        fallback_dir=fallback_dir,
    )


def _identity_verification(metadata: RuntimePeerMetadata) -> str:
    if metadata.observed_fingerprint:
        return "verified_fingerprint"
    return "server_name_only_runtime_constraint"


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
                decision="allow"
                if is_allowed
                else "deny",
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
            advertisements=[
                AdvertisedTool(server=self.default_server_name, name=tool.name)
                for tool in tools
            ],
            session_id=getattr(fastmcp_ctx, "session_id", "") if fastmcp_ctx is not None else "",
            run_id=_resolve_run_id(
                _resolve_runtime_run_dir(
                    configured_run_dir=self._run_dir,
                    session_id=getattr(fastmcp_ctx, "session_id", "") if fastmcp_ctx is not None else "",
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
            session_id=getattr(fastmcp_ctx, "session_id", "") if fastmcp_ctx is not None else "",
            run_id=_resolve_run_id(
                _resolve_runtime_run_dir(
                    configured_run_dir=self._run_dir,
                    session_id=getattr(fastmcp_ctx, "session_id", "") if fastmcp_ctx is not None else "",
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
