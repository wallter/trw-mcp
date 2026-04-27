"""Per-tool capability scoping for MCP server authorization."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.security.mcp_registry import AllowedTool


class CapabilityScopeError(Exception):
    """Raised when a tool call violates its declared capability scope."""


class CapabilityScope(BaseModel):
    """Runtime capability scope for a single server/tool pair."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    server_name: str
    tool_name: str
    allowed_phases: tuple[str, ...] = Field(default_factory=tuple)
    allowed_scopes: tuple[str, ...] = Field(default_factory=tuple)


def scope_from_allowed_tool(server_name: str, allowed_tool: AllowedTool) -> CapabilityScope:
    """Build a runtime scope from an allowlist entry."""

    return CapabilityScope(
        server_name=server_name,
        tool_name=allowed_tool.name,
        allowed_phases=allowed_tool.allowed_phases,
        allowed_scopes=allowed_tool.allowed_scopes,
    )


def apply_scope(
    *,
    server_name: str,
    tool_name: str,
    scope: CapabilityScope,
    current_phase: str | None,
    requested_scope: str | None,
) -> None:
    """Validate that a dispatch stays within its authorized phase/scope."""

    if scope.server_name != server_name:
        raise CapabilityScopeError(f"tool {tool_name!r} is not authorized for server {server_name!r}")
    if scope.tool_name != tool_name:
        raise CapabilityScopeError(f"tool {tool_name!r} does not match scope {scope.tool_name!r}")
    if current_phase is not None and scope.allowed_phases and current_phase not in scope.allowed_phases:
        raise CapabilityScopeError(f"tool {tool_name!r} is not allowed during phase {current_phase!r}")
    if requested_scope is not None and scope.allowed_scopes and requested_scope not in scope.allowed_scopes:
        raise CapabilityScopeError(f"tool {tool_name!r} is not allowed for scope {requested_scope!r}")


__all__ = [
    "CapabilityScope",
    "CapabilityScopeError",
    "apply_scope",
    "scope_from_allowed_tool",
]
