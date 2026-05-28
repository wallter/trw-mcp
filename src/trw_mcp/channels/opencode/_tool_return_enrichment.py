"""opencode T2 tool-return enrichment for trw_before_edit_hint and friends.

# Managed by TRW — no trw_distill imports permitted.

Provides T2 payload construction when TRW_CLIENT_PROFILE=opencode is set.
Default tier is T2 per audit fix P1-11 (NOT T3 which consumed ~2.5% per call).

T3 is available as explicit opt-in via ``opencode.tool_return_enrichment_tier: T3``
in ``.trw/config.yaml``.

Transport resolution uses TRW_CLIENT_PROFILE + TRW_MCP_TRANSPORT env vars
(P0-13 audit fix — ctx.session_id is NOT used for client discrimination).

PRD-DIST-2403 FR16-FR19.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from trw_mcp.channels._distill_telemetry import resolve_client_profile

log = structlog.get_logger(__name__)

__all__ = [
    "build_t2_payload",
    "get_default_tier_for_opencode",
    "is_opencode_client",
    "resolve_transport",
]

_ENV_CLIENT = "TRW_CLIENT_PROFILE"
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


def build_t2_payload(
    file_path: str,
    sidecar_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a T2 enrichment payload for *file_path*.

    T2 fields: importers, co_change_neighbors, inferred_tests, risk_score,
    hotspot_warnings.  Does NOT include edge_cases or rationale_records (T3).

    Args:
        file_path: The file path being edited.
        sidecar_data: Parsed sidecar payload, or None if absent.

    Returns:
        T2 payload dict or None if file not found / sidecar absent.
    """
    if sidecar_data is None:
        log.debug(
            "opencode_t2_payload_sidecar_absent",
            file_path=file_path,
            outcome="sidecar_absent",
        )
        return None

    hotspots: list[dict[str, Any]] = sidecar_data.get("hotspots", [])
    file_map: dict[str, dict[str, Any]] = sidecar_data.get("file_map", {})

    entry: dict[str, Any] | None = file_map.get(file_path)
    if entry is None:
        for h in hotspots:
            h_path = h.get("file", h.get("path", ""))
            if h_path == file_path:
                entry = h
                break

    if entry is None:
        log.debug(
            "opencode_t2_payload_file_not_in_sidecar",
            file_path=file_path,
            outcome="not_in_sidecar",
        )
        return None

    payload: dict[str, Any] = {
        "file_path": file_path,
        "importers": entry.get("importers", []),
        "co_change_neighbors": entry.get("co_change_neighbors", []),
        "inferred_tests": entry.get("inferred_tests", []),
        "risk_score": entry.get("risk_score", entry.get("score")),
        "hotspot_warnings": entry.get("warnings", []),
        "tier": "T2",
    }

    log.debug(
        "opencode_t2_payload_built",
        file_path=file_path,
        importers_count=len(payload["importers"]),
        outcome="ok",
    )
    return payload
