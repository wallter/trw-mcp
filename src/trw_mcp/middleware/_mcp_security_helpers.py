"""MCP security data models + telemetry helpers.

Belongs to the ``mcp_security.py`` facade. Re-exported there for back-compat.

Holds the four Pydantic-v2 data shapes (``AdvertisedTool``,
``MCPSecurityDecision``, ``MCPSecurityStatusSnapshot``,
``RuntimePeerMetadata``) plus the peer-identity + decision-emit helpers
that ``MCPSecurityMiddleware`` calls per dispatch.

Extracted as DIST-243 batch 27 to keep the parent middleware module under
the 350 effective-LOC ceiling.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.security.capability_scope import CapabilityScope, scope_from_allowed_tool
from trw_mcp.security.mcp_registry import ALL_PHASES, ALL_SCOPES, RegistryDecision
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
            # PRD-CORE-141 pin-key precedence (round-2 transport e2e F2): the
            # *session_id* the middleware receives is the transport-extracted
            # ``ctx.session_id`` (a per-connection UUID). It must NOT be forced
            # in as the Layer-1 ``explicit`` override — doing so shadowed the
            # documented Layer-2 ``TRW_SESSION_ID`` precedence that the tool
            # layer honors when WRITING pins, so phase resolution read a pin
            # keyed on the UUID that was never written and stayed in RESEARCH
            # forever. Pass ``explicit=None`` so ``resolve_pin_key`` applies its
            # full layering (env → ctx-probe → process), matching the tool
            # layer's key exactly.
            pin_key = resolve_pin_key(fastmcp_context, explicit=None)
            call_ctx = TRWCallContext(
                session_id=pin_key,
                client_hint=None,
                # Auto-resolved (env → ctx-probe → process), not caller-supplied.
                explicit=False,
                fastmcp_session=getattr(fastmcp_context, "session_id", None)
                if isinstance(getattr(fastmcp_context, "session_id", None), str)
                else None,
            )
            return get_pinned_run(context=call_ctx) or find_active_run(context=call_ctx)
        if session_id:
            # Security middleware runs on every MCP dispatch.  A transport
            # session id is an isolated caller identity, so a missing pin must
            # not fall through to the legacy filesystem scan.  That scan can
            # take tens of seconds in busy shared workspaces and blocks the
            # CallTool hot path before the tool body starts.  Treat explicit
            # session ids like ctx-aware calls: use a matching pin if present,
            # otherwise emit security telemetry via the fallback directory.
            call_ctx = TRWCallContext(
                session_id=session_id,
                client_hint=None,
                explicit=True,
                fastmcp_session=None,
            )
            return get_pinned_run(context=call_ctx) or find_active_run(context=call_ctx)
        # PRD-FIX-083: no caller identity (neither ctx nor session_id). Pin-only
        # — never fall through to the legacy mtime scan from middleware that
        # fires on every dispatch. With no identity we cannot safely attribute
        # the active run anyway; returning None is the correct semantics.
        return get_pinned_run()
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


def default_scope(
    *,
    server_name: str,
    tool_name: str,
    auth: RegistryDecision,
) -> CapabilityScope | None:
    """Build a capability scope from the allowlist entry, or None when absent."""
    entry = auth.entry
    if entry is None:
        return None
    allowed_tool = entry.tool_by_name(tool_name)
    if allowed_tool is None:
        return None
    return scope_from_allowed_tool(server_name, allowed_tool)


def first_party_tool_scope(
    *,
    server_name: str,
    tool_name: str,
    default_server_name: str,
) -> CapabilityScope | None:
    """Return a scope for bundled TRW tools omitted from the signed seed allowlist.

    The signed allowlist intentionally moves slowly; the live in-process
    TRW server can grow new first-party tools faster than that file is
    re-signed.  For the trusted default server only, bridge that drift from
    ``TOOL_PRESETS["all"]`` so advertisements and direct calls stay aligned
    with the configured first-party surface while non-TRW/unknown tools
    remain denied.
    """
    if server_name != default_server_name:
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


def record_anomalies(
    recent_anomalies: list[dict[str, Any]],
    *,
    fired: Sequence[str],
    transport: Transport,
    server: str,
    tool: str,
    args_hash: str,
    run_id: str | None,
    session_id: str,
) -> list[dict[str, Any]]:
    """Append fired anomalies to *recent_anomalies* and trim to 25 entries.

    Returns the trimmed list (caller rebinds; in-place append + slice).
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    recent_anomalies.extend(
        [
            {
                "ts": now,
                "transport": transport,
                "server": server,
                "tool": tool,
                "type": anomaly_type,
                "run_id": run_id or "",
                "session_id": session_id,
                "arg_hash": args_hash,
            }
            for anomaly_type in fired
        ]
    )
    return recent_anomalies[-25:]


def resolve_transport_from_ctx(fastmcp_ctx: object | None) -> Transport:
    """Read ``transport`` off the FastMCP context and normalize, defaulting to stdio."""
    raw = getattr(fastmcp_ctx, "transport", "stdio") if fastmcp_ctx is not None else "stdio"
    return normalize_transport(raw)


def resolve_run_context(
    *,
    configured_run_dir: Path | None,
    session_id: str = "",
    fastmcp_context: object | None = None,
) -> tuple[Path | None, str | None]:
    """Combined ``_resolve_runtime_run_dir`` + ``_resolve_run_id`` lookup."""
    run_dir = _resolve_runtime_run_dir(
        configured_run_dir=configured_run_dir,
        session_id=session_id,
        fastmcp_context=fastmcp_context,
    )
    return run_dir, _resolve_run_id(run_dir)


def resolve_scope_with_fallback(
    *,
    scopes: Mapping[str, CapabilityScope],
    tool_name: str,
    server_name: str,
    auth: RegistryDecision,
    default_server_name: str,
) -> CapabilityScope | None:
    """Resolve the capability scope, cascading explicit → registry → first-party fallback."""
    scope = scopes.get(tool_name) or default_scope(server_name=server_name, tool_name=tool_name, auth=auth)
    if scope is None and auth.allowed:
        scope = first_party_tool_scope(
            server_name=server_name,
            tool_name=tool_name,
            default_server_name=default_server_name,
        )
    return scope


def peer_extras(runtime_peer: RuntimePeerMetadata) -> dict[str, Any]:
    """Return the 4 peer-identity fields shared across decision telemetry payloads."""
    return {
        "peer_identity_source": runtime_peer.peer_identity_source,
        "fingerprint_source": runtime_peer.fingerprint_source,
        "fingerprint_constraint": runtime_peer.fingerprint_constraint,
        "identity_verification": _identity_verification(runtime_peer),
    }


def build_audit_fields(
    *,
    match_type: str,
    allowed: bool,
    operator: str,
) -> dict[str, Any]:
    """Return per-decision audit_fields keyed off auth match type."""
    if not allowed:
        return {}
    if match_type == "unsigned_admission":
        return {"unsigned_admission": True, "operator": operator}
    if match_type == "overlay":
        return {"operator": operator, "operator_overlay_applied": True}
    return {}
