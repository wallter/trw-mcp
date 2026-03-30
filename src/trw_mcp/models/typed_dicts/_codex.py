"""Typed helpers for Codex bootstrap configuration shapes."""

from __future__ import annotations

from typing_extensions import Literal, TypedDict


CodexToolApprovalMode = Literal["auto", "prompt", "approve"]


class CodexMcpToolConfigEntry(TypedDict, total=False):
    """Per-tool Codex MCP config entry."""

    approval_mode: CodexToolApprovalMode
    enabled: bool


class CodexMcpServerEntry(TypedDict, total=False):
    """Codex MCP server config entry."""

    command: str
    args: list[str]
    url: str
    enabled: bool
    tools: dict[str, CodexMcpToolConfigEntry]


class CodexSkillConfigEntry(TypedDict, total=False):
    """Single Codex skill config entry."""

    path: str
    enabled: bool


class CodexSkillsConfig(TypedDict, total=False):
    """Codex `skills` table."""

    config: list[CodexSkillConfigEntry]


class CodexFeaturesConfig(TypedDict, total=False):
    """Codex `features` table."""

    codex_hooks: bool


class CodexConfigDict(TypedDict, total=False):
    """Top-level Codex config TOML shape used by TRW bootstrap."""

    features: CodexFeaturesConfig
    mcp_servers: dict[str, CodexMcpServerEntry]
    project_doc_fallback_filenames: list[str]
    skills: CodexSkillsConfig
    model: str
    model_reasoning_effort: str
    sandbox_mode: str
    approval_policy: str


class CodexHookCommand(TypedDict, total=False):
    """Single Codex hook command entry."""

    type: str
    command: str
    statusMessage: str
    timeout: int


class CodexHookMatcherEntry(TypedDict, total=False):
    """Matcher group under one Codex hook event."""

    matcher: str
    description: str
    hooks: list[CodexHookCommand]


class CodexHooksConfig(TypedDict, total=False):
    """Top-level `.codex/hooks.json` shape."""

    hooks: dict[str, list[CodexHookMatcherEntry]]
