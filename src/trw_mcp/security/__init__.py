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
    CapabilityScope,
    CapabilityScopeError,
    apply_scope,
    scope_from_allowed_tool,
)
from trw_mcp.security.mcp_registry import (
    AllowedTool,
    MCPAllowlist,
    MCPRegistry,
    MCPSecurityConfigError,
    MCPSecurityUnavailableError,
    MCPServer,
    load_allowlist,
)

__all__ = [
    "AnomalyDetector",
    "AnomalyDetectorConfig",
    "AnomalyObservation",
    "CapabilityScope",
    "CapabilityScopeError",
    "AllowedTool",
    "MCPAllowlist",
    "MCPRegistry",
    "MCPSecurityConfigError",
    "MCPSecurityUnavailableError",
    "MCPServer",
    "apply_scope",
    "hash_tool_args",
    "load_allowlist",
    "scope_from_allowed_tool",
]
