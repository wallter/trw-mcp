"""Codex config normalization helpers — extracted from _codex.py.

Belongs to the ``_codex.py`` facade. Re-exported there for back-compat.

Pure-function normalizers that coerce parsed Codex config payloads to
the typed Codex config shapes (TypedDicts in
``trw_mcp.models.typed_dicts``):

- ``_normalize_mcp_tool_config`` — MCP server tools table → typed dict
- ``_normalize_mcp_server_entry`` — single server entry → typed shape,
  preserving back-compat tools→enabled/disabled translation
- ``_normalize_skill_config`` — skills.config payload → typed list
- ``_normalize_hook_command`` — single hook command → typed shape
- ``_normalize_hook_matcher_group`` — single matcher group → typed shape
- ``_normalize_hook_config`` — hooks payload → typed config
- ``_normalize_feature_flags`` — feature flags map → typed config
- ``_normalize_mcp_servers`` — full mcp_servers map → typed dict
- ``_normalize_fallback_files`` — project-doc fallback list → str list

Extracted as DIST-243 batch 42 to keep the parent ``_codex.py`` module
under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

from typing import cast

from trw_mcp.models.typed_dicts import (
    CodexFeaturesConfig,
    CodexHookCommand,
    CodexHookMatcherEntry,
    CodexHooksConfig,
    CodexMcpServerEntry,
    CodexSkillConfigEntry,
)
from trw_mcp.models.typed_dicts._codex import CodexMcpToolConfigEntry


def _normalize_mcp_tool_config(existing: object) -> dict[str, CodexMcpToolConfigEntry]:
    """Coerce an MCP server `tools` table to typed per-tool config."""
    if not isinstance(existing, dict):
        return {}

    normalized: dict[str, CodexMcpToolConfigEntry] = {}
    for tool_name, raw_entry in existing.items():
        if not isinstance(tool_name, str) or not isinstance(raw_entry, dict):
            continue
        entry: CodexMcpToolConfigEntry = {}
        approval_mode = raw_entry.get("approval_mode")
        enabled = raw_entry.get("enabled")
        if approval_mode in {"auto", "prompt", "approve"}:
            entry["approval_mode"] = approval_mode
        if isinstance(enabled, bool):
            entry["enabled"] = enabled
        normalized[tool_name] = entry
    return normalized


def _normalize_mcp_server_entry(existing: object) -> CodexMcpServerEntry | None:
    """Coerce a parsed MCP server table to the typed server shape."""
    if not isinstance(existing, dict):
        return None

    entry: CodexMcpServerEntry = {}
    command = existing.get("command")
    args = existing.get("args")
    url = existing.get("url")
    enabled = existing.get("enabled")
    enabled_tools = existing.get("enabled_tools")
    disabled_tools = existing.get("disabled_tools")
    tools = existing.get("tools")

    if isinstance(command, str):
        entry["command"] = command
    if isinstance(args, list) and all(isinstance(arg, str) for arg in args):
        entry["args"] = cast("list[str]", args)
    if isinstance(url, str):
        entry["url"] = url
    if isinstance(enabled, bool):
        entry["enabled"] = enabled
    if isinstance(enabled_tools, list) and all(isinstance(tool_name, str) for tool_name in enabled_tools):
        entry["enabled_tools"] = cast("list[str]", enabled_tools)
    if isinstance(disabled_tools, list) and all(isinstance(tool_name, str) for tool_name in disabled_tools):
        entry["disabled_tools"] = cast("list[str]", disabled_tools)

    # Backward compatibility: older TRW bootstrap versions wrote unsupported
    # per-MCP-tool config under `tools`. Preserve only the enable/disable
    # signal by translating it to the documented enabled/disabled tool lists.
    normalized_tools = _normalize_mcp_tool_config(tools)
    if normalized_tools:
        enabled_tool_set = set(entry.get("enabled_tools", []))
        disabled_tool_set = set(entry.get("disabled_tools", []))
        for tool_name, tool_config in normalized_tools.items():
            tool_enabled = tool_config.get("enabled")
            if tool_enabled is False:
                disabled_tool_set.add(tool_name)
                enabled_tool_set.discard(tool_name)
            else:
                enabled_tool_set.add(tool_name)
                disabled_tool_set.discard(tool_name)
        if enabled_tool_set:
            entry["enabled_tools"] = sorted(enabled_tool_set)
        if disabled_tool_set:
            entry["disabled_tools"] = sorted(disabled_tool_set)
    return entry


def _normalize_skill_config(existing: object) -> list[CodexSkillConfigEntry]:
    """Coerce a parsed `skills.config` payload to a typed list."""
    from trw_mcp.bootstrap._codex import _normalize_skill_path

    if not isinstance(existing, list):
        return []

    normalized: list[CodexSkillConfigEntry] = []
    for entry in existing:
        if isinstance(entry, dict):
            item: CodexSkillConfigEntry = {}
            path = entry.get("path")
            enabled = entry.get("enabled")
            if isinstance(path, str):
                item["path"] = _normalize_skill_path(path)
            if isinstance(enabled, bool):
                item["enabled"] = enabled
            if item:
                normalized.append(item)
    return normalized


def _normalize_hook_command(hook: object) -> CodexHookCommand | None:
    """Coerce a single hook command entry to the typed Codex shape."""
    if not isinstance(hook, dict):
        return None

    normalized_hook: CodexHookCommand = {}
    hook_type = hook.get("type")
    command = hook.get("command")
    status_message = hook.get("statusMessage")
    timeout = hook.get("timeout")
    if isinstance(hook_type, str):
        normalized_hook["type"] = hook_type
    if isinstance(command, str):
        normalized_hook["command"] = command
    if isinstance(status_message, str):
        normalized_hook["statusMessage"] = status_message
    if isinstance(timeout, int):
        normalized_hook["timeout"] = timeout
    return normalized_hook or None


def _normalize_hook_matcher_group(group: object) -> CodexHookMatcherEntry | None:
    """Coerce a single hook matcher group to the typed Codex shape."""
    if not isinstance(group, dict):
        return None

    normalized_group: CodexHookMatcherEntry = {}
    matcher = group.get("matcher")
    description = group.get("description")
    if isinstance(matcher, str):
        normalized_group["matcher"] = matcher
    if isinstance(description, str):
        normalized_group["description"] = description

    hooks = group.get("hooks")
    if isinstance(hooks, list):
        normalized_hooks = [hook for raw_hook in hooks if (hook := _normalize_hook_command(raw_hook)) is not None]
        if normalized_hooks:
            normalized_group["hooks"] = normalized_hooks

    return normalized_group or None


def _normalize_hook_config(existing: object) -> CodexHooksConfig:
    """Coerce a parsed hooks payload to the typed Codex hook config shape."""
    if not isinstance(existing, dict):
        return {"hooks": {}}

    existing_hooks = existing.get("hooks")
    if not isinstance(existing_hooks, dict):
        return {"hooks": {}}

    normalized_hooks: dict[str, list[CodexHookMatcherEntry]] = {}
    for event_name, groups in existing_hooks.items():
        if not isinstance(event_name, str) or not isinstance(groups, list):
            continue
        normalized_groups = [
            normalized_group
            for group in groups
            if (normalized_group := _normalize_hook_matcher_group(group)) is not None
        ]
        if normalized_groups:
            normalized_hooks[event_name] = normalized_groups

    return {"hooks": normalized_hooks}


def _normalize_feature_flags(raw_features: object) -> CodexFeaturesConfig:
    """Extract boolean feature flags while defaulting Codex hooks off."""
    features_map: dict[str, bool] = {}
    if isinstance(raw_features, dict):
        features_map = {
            key: value for key, value in raw_features.items() if isinstance(key, str) and isinstance(value, bool)
        }
    features_map.setdefault("codex_hooks", False)
    return cast("CodexFeaturesConfig", features_map)


def _normalize_mcp_servers(raw_mcp_servers: object) -> dict[str, CodexMcpServerEntry]:
    """Normalize configured MCP servers while preserving user-managed entries."""
    if not isinstance(raw_mcp_servers, dict):
        return {}

    mcp_servers: dict[str, CodexMcpServerEntry] = {}
    for key, value in raw_mcp_servers.items():
        if not isinstance(key, str):
            continue
        normalized_server = _normalize_mcp_server_entry(value)
        if normalized_server is not None:
            mcp_servers[key] = normalized_server
    return mcp_servers


def _normalize_fallback_files(raw_fallback_files: object) -> list[str]:
    """Normalize project-doc fallback files and strip model-instruction paths."""
    from trw_mcp.bootstrap._codex import _codex_instruction_path

    fallback_files = raw_fallback_files if isinstance(raw_fallback_files, list) else []
    instruction_path = _codex_instruction_path()
    normalized_fallbacks: list[str] = []
    for value in fallback_files:
        if not isinstance(value, str) or value == instruction_path or value in normalized_fallbacks:
            continue
        normalized_fallbacks.append(value)
    return normalized_fallbacks
