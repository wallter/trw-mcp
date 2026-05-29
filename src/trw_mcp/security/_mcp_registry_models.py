"""Pydantic models for the MCP server registry / allowlist.

Extracted from :mod:`trw_mcp.security.mcp_registry` (PRD-DIST-243
Phase 1 batch 5, cycle 35) to keep that module under the 350-effective-
LOC operator threshold. Holds the 8 immutable Pydantic v2 model
classes + the 2 phase/scope tuples they default against. Logic
(loaders, signature verification, registry orchestration) stays in
``mcp_registry.py``.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ALL_PHASES",
    "ALL_SCOPES",
    "AllowedTool",
    "MCPAllowlist",
    "MCPSecurityConfigError",
    "MCPSecurityError",
    "MCPSecurityUnavailableError",
    "MCPServer",
    "RegistryDecision",
    "RegistrySignatureBlock",
    "_descriptor_fingerprint",
]

ALL_PHASES: tuple[str, ...] = (
    "research",
    "plan",
    "implement",
    "validate",
    "review",
    "deliver",
)
ALL_SCOPES: tuple[str, ...] = ("read", "write", "execute")


class MCPSecurityError(RuntimeError):
    """Base error for MCP security failures."""


class MCPSecurityUnavailableError(MCPSecurityError):
    """Raised when the cryptographic verification dependency is unavailable."""


class MCPSecurityConfigError(MCPSecurityError):
    """Raised when the registry or overlay configuration is invalid."""


def _descriptor_fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class AllowedTool(BaseModel):
    """Per-tool authorization entry from the PRD allowlist schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    allowed_phases: tuple[str, ...] = Field(default_factory=lambda: ALL_PHASES)
    allowed_scopes: tuple[str, ...] = Field(default_factory=lambda: ALL_SCOPES)


class RegistrySignatureBlock(BaseModel):
    """Detached signature metadata for a registry file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm: Literal["ed25519"] = "ed25519"
    signed_at: str
    signer_fingerprint: str
    signature: str


class MCPServer(BaseModel):
    """Authorized MCP server entry."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    url_or_command: str
    public_key_fingerprint: str
    allowed_tools: tuple[AllowedTool, ...] = Field(default_factory=tuple)
    source_tier: Literal["canonical", "overlay"] = "canonical"

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_capabilities(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        upgraded = dict(value)
        if "public_key_fingerprint" not in upgraded:
            upgraded["public_key_fingerprint"] = _descriptor_fingerprint(
                str(upgraded.get("url_or_command", upgraded.get("name", "")))
            )
        if "allowed_tools" in value:
            return upgraded
        capabilities = upgraded.get("capabilities")
        if not isinstance(capabilities, list):
            return upgraded
        upgraded["allowed_tools"] = [
            {
                "name": str(tool_name),
                "allowed_phases": list(ALL_PHASES),
                "allowed_scopes": list(ALL_SCOPES),
            }
            for tool_name in capabilities
        ]
        return upgraded

    def tool_names(self) -> set[str]:
        return {tool.name for tool in self.allowed_tools}

    def tool_by_name(self, tool_name: str) -> AllowedTool | None:
        for tool in self.allowed_tools:
            if tool.name == tool_name:
                return tool
        return None


class MCPAllowlist(BaseModel):
    """Resolved allowlist after canonical + optional overlay merge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = 1
    signing_algorithm: Literal["ed25519"] = "ed25519"
    servers: tuple[MCPServer, ...] = Field(default_factory=tuple)
    signature_block: RegistrySignatureBlock | None = None
    allowlist_hash: str = ""

    def by_name(self, name: str) -> MCPServer | None:
        for server in self.servers:
            if server.name == name:
                return server
        return None

    def by_fingerprint(self, fingerprint: str) -> MCPServer | None:
        for server in self.servers:
            if server.public_key_fingerprint == fingerprint:
                return server
        return None


class RegistryDecision(BaseModel):
    """Authorization result for a server identity check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason: str = ""
    match_type: Literal["canonical", "overlay", "unsigned_admission", "quarantined", "missing"]
    entry: MCPServer | None = None
    drift_detected: bool = False
    quarantine_reason: str = ""
