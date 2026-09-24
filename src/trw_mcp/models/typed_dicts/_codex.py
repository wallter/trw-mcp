"""Typed helpers for Codex bootstrap configuration shapes."""

from __future__ import annotations

from typing import Literal

from typing_extensions import TypedDict

#: The four values codex accepts for an MCP approval mode. MEASURED 2026-09-16
#: against codex-cli 0.154.0: `codex exec --strict-config` rejected an
#: out-of-enum value with "unknown variant `bogus_value`, expected one of `auto`,
#: `prompt`, `writes`, `approve`". "writes" was missing here, so a user who set
#: it had the setting silently dropped on the next update-project.
CodexToolApprovalMode = Literal["auto", "prompt", "writes", "approve"]


class CodexMcpToolConfigEntry(TypedDict, total=False):
    """Per-tool Codex MCP config entry."""

    approval_mode: CodexToolApprovalMode
    enabled: bool


class CodexMcpServerEntry(TypedDict, total=False):
    """Codex MCP server config entry."""

    command: str
    args: list[str]
    cwd: str
    url: str
    enabled: bool
    enabled_tools: list[str]
    disabled_tools: list[str]
    # Names of the client's own environment variables Codex passes through to the server.
    env_vars: list[str]
    # `enabled_tools` is the VISIBILITY axis; `tools` carries the APPROVAL axis.
    # Under `approval_policy = "never"` a visible tool is still refused at call
    # time unless something grants it an approval mode, which is why a codex
    # member could see every trw_* tool and call none of them (PRD-CORE-277-FR06).
    tools: dict[str, CodexMcpToolConfigEntry]
    env: dict[str, str]
    default_tools_approval_mode: CodexToolApprovalMode


class CodexSkillConfigEntry(TypedDict, total=False):
    """Single Codex skill config entry pointing at a skill directory."""

    path: str
    enabled: bool


class CodexSkillsConfig(TypedDict, total=False):
    """Codex `skills` table."""

    config: list[CodexSkillConfigEntry]


class CodexFeaturesConfig(TypedDict, total=False):
    """Codex `features` table."""

    hooks: bool
    # Legacy input accepted for migration; TRW-managed output writes `hooks`.
    codex_hooks: bool


class CodexConfigDict(TypedDict, total=False):
    """Top-level Codex config TOML shape used by TRW bootstrap."""

    features: CodexFeaturesConfig
    mcp_servers: dict[str, CodexMcpServerEntry]
    project_doc_fallback_filenames: list[str]
    model_instructions_file: str
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
