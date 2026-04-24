"""MCP server authorization + capability scoping (PRD-INFRA-SEC-001).

This package implements the trust boundary around MCP servers exposed to
TRW-brokered agent sessions. It covers:

* Signed registry of allowlisted MCP servers (FR-1)
* Per-tool capability scoping filter (FR-2)
* Signature verification hooks (observe-mode stub in v1; FR-5)
* 3-week shadow-mode anomaly detector (FR-3 / FR-4) — see
  :mod:`trw_mcp.security.anomaly_detector`.

The middleware entry point lives in :mod:`trw_mcp.middleware.mcp_security`
and is where these pieces are composed for every MCP transport path
(stdio / HTTP / SSE — FR-9 reachability).
"""

from trw_mcp.security.anomaly_detector import (
    AnomalyDetector,
    AnomalyDetectorConfig,
    AnomalyObservation,
    hash_tool_args,
)
from trw_mcp.security.capability_scope import (
    CapabilityFilter,
    CapabilityScope,
    CapabilityScopeError,
    apply_scope,
    default_scopes_for_family,
)
from trw_mcp.security.mcp_registry import (
    MCPAllowlist,
    MCPServer,
    TrustLevel,
    is_allowed,
    load_allowlist,
    verify_signature,
)

__all__ = [
    "AnomalyDetector",
    "AnomalyDetectorConfig",
    "AnomalyObservation",
    "CapabilityFilter",
    "CapabilityScope",
    "CapabilityScopeError",
    "MCPAllowlist",
    "MCPServer",
    "TrustLevel",
    "apply_scope",
    "default_scopes_for_family",
    "hash_tool_args",
    "is_allowed",
    "load_allowlist",
    "verify_signature",
]
