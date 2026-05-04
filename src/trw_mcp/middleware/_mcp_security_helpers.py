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
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

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
