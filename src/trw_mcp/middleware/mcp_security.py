"""MCP security middleware — signed registry + capability scope + anomaly detect.

Implements PRD-INFRA-SEC-001 FR-2, FR-4, FR-6, FR-9 (production-dispatch
reachability across stdio / HTTP / SSE transports).

Design contract:

* Every tool advertisement is routed through
  :meth:`MCPSecurityMiddleware.filter_advertised_tools`. This is where the
  ``mcp__trw__<name>`` → ``<name>`` normalization happens (claude-code
  flagship), because the canonical allowlist stores short names.
* Every tool-call dispatch is routed through
  :meth:`MCPSecurityMiddleware.on_tool_call`. All three sub-layers fire
  per-dispatch — any missing layer is a silent CVE-2025-53773 bypass
  (FR-9 reachability).
* v1 is observe mode only: capability-scope denials produce a structured
  log + ``MCPSecurityEvent`` with ``decision="shadow_deny"`` but NEVER raise.
  Anomaly detection is pure observation (see ``security/anomaly_detector``).
* Transports supported: ``stdio`` (claude-code), ``http`` (opencode remote),
  ``sse`` (legacy/streamable). Transport is recorded in every event payload
  so the FR-9 reachability fuzz test can enumerate (transport × layer) ≥ 1.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import structlog
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
)
from trw_mcp.security.mcp_registry import (
    MCPAllowlist,
    MCPServer,
    verify_signature,
)
from trw_mcp.telemetry.event_base import MCPSecurityEvent
from trw_mcp.telemetry.unified_events import emit as emit_unified_event

logger = structlog.get_logger(__name__)

Transport = Literal["stdio", "http", "sse"]
TRANSPORTS: tuple[Transport, ...] = ("stdio", "http", "sse")

# Claude Code flagship prefix (models/config/_profiles.py:102). Middleware
# normalizes inbound tool names by stripping this prefix at advertise-time;
# un-prefixed short names are what the canonical allowlist stores.
CLAUDE_CODE_PREFIX = "mcp__trw__"


class AdvertisedTool(BaseModel):
    """A tool advertisement from an MCP server prior to scope filtering."""

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


def normalize_tool_name(raw: str) -> str:
    """Strip the claude-code ``mcp__trw__`` prefix if present.

    Normalization happens at advertise-time only; the allowlist stores short
    names. Idempotent for already-short names.
    """
    if raw.startswith(CLAUDE_CODE_PREFIX):
        return raw[len(CLAUDE_CODE_PREFIX) :]
    return raw


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
    """Emit a single :class:`MCPSecurityEvent` for this dispatch.

    Observe-mode: allowed and denied decisions both emit. Never raises.
    """
    payload: dict[str, Any] = {
        "decision": decision,
        "transport": transport,
        "server": server,
        "tool": tool,
        "layers_fired": list(layers_fired),
        "mode": "observe",
    }
    if extra:
        payload.update(dict(extra))
    event = MCPSecurityEvent(
        session_id=session_id or "observe",
        run_id=run_id,
        payload=payload,
    )
    emit_unified_event(event, run_dir=run_dir, fallback_dir=fallback_dir)
    logger.info(
        "mcp_security_dispatch",
        action="dispatch",
        decision=decision,
        transport=transport,
        server=server,
        tool=tool,
        outcome=decision,
    )


class MCPSecurityMiddleware:
    """Signed-registry + capability-scope + anomaly-detect middleware.

    Wire order (FR-6): this middleware runs BEFORE the MCP adapter writes
    to the wire on every transport path.
    """

    def __init__(
        self,
        *,
        allowlist: MCPAllowlist,
        scopes: Mapping[str, CapabilityScope],
        anomaly_detector: AnomalyDetector,
        run_dir: Path | None = None,
        fallback_dir: Path | None = None,
    ) -> None:
        self._allowlist = allowlist
        self._scopes: dict[str, CapabilityScope] = dict(scopes)
        self._detector = anomaly_detector
        self._run_dir = run_dir
        self._fallback_dir = fallback_dir

    # ------------------------------------------------------------------
    # FR-1 / FR-8 — registry signature verification
    # ------------------------------------------------------------------
    def _verify_registry(self, server_name: str) -> tuple[bool, MCPServer | None]:
        entry = self._allowlist.by_name(server_name)
        if entry is None:
            return False, None
        ok = verify_signature(entry)
        return ok, entry

    # ------------------------------------------------------------------
    # FR-2 — capability-scope filter for advertised tools
    # ------------------------------------------------------------------
    def filter_advertised_tools(
        self,
        *,
        transport: Transport,
        advertisements: Sequence[AdvertisedTool],
        session_id: str = "",
        run_id: str | None = None,
    ) -> list[AdvertisedTool]:
        """Filter advertised tools through signed-registry + scope.

        Normalizes ``mcp__trw__<x>`` → ``<x>`` at this boundary so the
        allowlist (short names) can match regardless of client profile.
        """
        allowed: list[AdvertisedTool] = []
        for ad in advertisements:
            short = normalize_tool_name(ad.name)
            normalized = AdvertisedTool(
                server=ad.server,
                name=short,
                namespaced_name=ad.name,
            )
            verified, entry = self._verify_registry(ad.server)
            if not verified or entry is None:
                _emit_decision(
                    decision="shadow_deny",
                    transport=transport,
                    server=ad.server,
                    tool=short,
                    layers_fired=["registry"],
                    run_dir=self._run_dir,
                    fallback_dir=self._fallback_dir,
                    session_id=session_id,
                    run_id=run_id,
                    extra={"reason": "server_not_in_allowlist"},
                )
                continue
            if short not in entry.capabilities:
                _emit_decision(
                    decision="shadow_deny",
                    transport=transport,
                    server=ad.server,
                    tool=short,
                    layers_fired=["registry", "capability_scope"],
                    run_dir=self._run_dir,
                    fallback_dir=self._fallback_dir,
                    session_id=session_id,
                    run_id=run_id,
                    extra={"reason": "tool_not_in_capabilities"},
                )
                continue
            allowed.append(normalized)
            _emit_decision(
                decision="shadow_allow",
                transport=transport,
                server=ad.server,
                tool=short,
                layers_fired=["registry", "capability_scope"],
                run_dir=self._run_dir,
                fallback_dir=self._fallback_dir,
                session_id=session_id,
                run_id=run_id,
            )
        return allowed

    # ------------------------------------------------------------------
    # FR-9 — per-dispatch reachability: registry + scope + anomaly
    # ------------------------------------------------------------------
    def on_tool_call(
        self,
        *,
        transport: Transport,
        server: str,
        tool: str,
        args: dict[str, Any] | None = None,
        session_id: str = "",
        run_id: str | None = None,
    ) -> MCPSecurityDecision:
        """Route a single tool-call dispatch through all three sub-layers.

        Observe-mode returns ``allowed=True`` even when a sub-layer would
        normally deny; the decision carries ``reason`` for audit. The three
        layers ALWAYS fire so the FR-9 reachability test sees all cells in
        the ``(transport × layer)`` matrix populated.
        """
        layers_fired: list[str] = []
        args = args or {}
        normalized_tool = normalize_tool_name(tool)

        # Layer 1: registry signature verification (always fires)
        verified, entry = self._verify_registry(server)
        layers_fired.append("registry")

        # Layer 2: capability-scope check (always fires, observe-mode)
        scope_reason = ""
        scope = self._scopes.get(normalized_tool)
        if scope is not None:
            try:
                apply_scope({"name": normalized_tool, "args": args}, scope)
            except CapabilityScopeError as exc:  # justified: observe-mode, log + continue, never raise
                scope_reason = str(exc)
        elif entry is not None and normalized_tool not in entry.capabilities:
            scope_reason = "tool_not_in_server_capabilities"
        layers_fired.append("capability_scope")

        # Layer 3: anomaly detector (always fires — pure observation)
        now = datetime.now(tz=timezone.utc)
        self._detector.observe(
            AnomalyObservation(
                ts=now,
                server=server,
                tool=normalized_tool,
                args_hash=hash_tool_args(args),
                run_id=run_id,
                session_id=session_id,
            )
        )
        layers_fired.append("anomaly_detector")

        reason = ""
        decision_tag = "shadow_allow"
        if not verified:
            reason = "server_not_in_allowlist"
            decision_tag = "shadow_deny"
        elif scope_reason:
            reason = scope_reason
            decision_tag = "shadow_deny"

        _emit_decision(
            decision=decision_tag,
            transport=transport,
            server=server,
            tool=normalized_tool,
            layers_fired=layers_fired,
            run_dir=self._run_dir,
            fallback_dir=self._fallback_dir,
            session_id=session_id,
            run_id=run_id,
            extra={"reason": reason} if reason else None,
        )
        # Observe-mode never blocks — ``allowed=True`` regardless of reason.
        return MCPSecurityDecision(
            allowed=True,
            reason=reason,
            transport=transport,
            server=server,
            tool=normalized_tool,
            layers_fired=layers_fired,
        )


__all__ = [
    "AdvertisedTool",
    "CLAUDE_CODE_PREFIX",
    "MCPSecurityDecision",
    "MCPSecurityMiddleware",
    "TRANSPORTS",
    "Transport",
    "normalize_tool_name",
]
