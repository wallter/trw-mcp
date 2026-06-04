"""opencode tool-return enrichment helpers for trw_before_edit_hint and friends.

# Managed by TRW — no trw_distill imports permitted.

Provides client-profile detection and transport resolution when
TRW_CLIENT_PROFILE=opencode is set.  Default tier is T2 per audit fix P1-11
(NOT T3 which consumed ~2.5% per call).

T3 is available as explicit opt-in via ``opencode.tool_return_enrichment_tier: T3``
in ``.trw/config.yaml``.

Transport resolution uses TRW_CLIENT_PROFILE + TRW_MCP_TRANSPORT env vars
(P0-13 audit fix — ctx.session_id is NOT used for client discrimination).

T2 tool-return payload construction is handled by the shared substrate
``channels/_tool_return_tiers.py::enrich_response()``, which is called
directly from ``tools/before_edit_hint.py``, ``tools/entity_risk_map.py``,
and ``tools/codebase_risk_report.py``.  No per-client payload builder is
needed here.

PRD-DIST-2403 FR16-FR19.
"""

from __future__ import annotations

import os

import structlog

from trw_mcp.channels._distill_telemetry import resolve_client_profile

log = structlog.get_logger(__name__)

__all__ = [
    "get_default_tier_for_opencode",
    "is_opencode_client",
    "resolve_transport",
]

_ENV_TRANSPORT = "TRW_MCP_TRANSPORT"
_DEFAULT_TRANSPORT = "stdio"

# Module-level flag to rate-limit the "client=unknown" warning (one per process)
_unknown_client_warned: bool = False


def get_default_tier_for_opencode() -> str:
    """Return the default tier for opencode client (T2 per P1-11 fix).

    Returns:
        ``"T2"``
    """
    return "T2"


def is_opencode_client() -> bool:
    """Return True when TRW_CLIENT_PROFILE=opencode is set.

    Uses env-var detection only (P0-13 audit fix — no ctx.session_id).

    Returns:
        True if the active client profile is opencode.
    """
    return resolve_client_profile() == "opencode"


def resolve_transport() -> str:
    """Resolve the MCP transport type from environment variables.

    Returns ``TRW_MCP_TRANSPORT`` value when ``TRW_CLIENT_PROFILE=opencode``.
    Returns ``"unknown"`` when client profile is not set, and emits a
    one-time structlog warning so misconfigured environments are detectable
    (P0-13 audit fix).

    Returns:
        Transport string: ``"stdio"``, ``"remote_http"``, or ``"unknown"``.
    """
    global _unknown_client_warned

    client = resolve_client_profile()
    if client == "opencode":
        return os.environ.get(_ENV_TRANSPORT, _DEFAULT_TRANSPORT).strip() or _DEFAULT_TRANSPORT

    # Client not set — emit one-time warning (P0-13)
    if not _unknown_client_warned:
        _unknown_client_warned = True
        log.warning(
            "opencode_transport_unknown",
            reason="TRW_CLIENT_PROFILE not set — source .trw/client-profile.env",
            transport="unknown",
            outcome="unknown_client",
        )
    return "unknown"
