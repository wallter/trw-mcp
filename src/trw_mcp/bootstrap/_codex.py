"""Codex-specific bootstrap configuration.

Generates and smart-merges repo-scoped Codex artifacts:
- .codex/config.toml
- optional .codex/hooks.json
- .codex/agents/*.toml
- .agents/skills/
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol, TypeVar, cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python <3.11 fallback
    import tomli as tomllib

import structlog

from trw_mcp.models.typed_dicts import (
    BootstrapFileResult,
    CodexConfigDict,
    CodexFeaturesConfig,
    CodexHookCommand,
    CodexHookMatcherEntry,
    CodexHooksConfig,
    CodexMcpServerEntry,
    CodexSkillConfigEntry,
    CodexSkillsConfig,
)
from trw_mcp.models.typed_dicts._codex import CodexMcpToolConfigEntry

from ._file_ops import _new_result, _record_write

logger = structlog.get_logger(__name__)

_CODEX_AGENTS_DIR = ".codex/agents"
_CODEX_CONFIG_PATH = ".codex/config.toml"
_CODEX_HOOKS_PATH = ".codex/hooks.json"
_CODEX_SKILLS_DIR = ".agents/skills"
_TRW_HOOK_DESCRIPTION_PREFIX = "TRW managed:"
_TRW_PROJECT_DOC = "AGENTS.md"
_LEGACY_PROJECT_DOC = "CLAUDE.md"
_TRW_TOOL_PREFIX = "trw_"
_AsyncResultT = TypeVar("_AsyncResultT")


def _codex_instruction_path() -> str:
    """Return the profile-driven Codex instruction file path."""
    from trw_mcp.models.config._profiles import resolve_client_profile

    return resolve_client_profile("codex").write_targets.instruction_path


def _codex_data_dir() -> Path:
    """Return the bundled Codex-specific data root."""
    from ._utils import _DATA_DIR

    return _DATA_DIR / "codex"


def _codex_skills_source_dir() -> Path:
    """Return the bundled Codex-specific skills root."""
    return _codex_data_dir() / "skills"


class _NamedTool(Protocol):
    """Protocol for FastMCP tool metadata returned by list_tools()."""

    name: str

def _trw_mcp_server_entry() -> CodexMcpServerEntry:
    """Return the TRW MCP server entry for Codex config."""
    if shutil.which("trw-mcp"):
        command = "trw-mcp"
        args = ["--debug"]
    else:
        command = sys.executable
        args = ["-m", "trw_mcp.server", "--debug"]
    return {"command": command, "args": args, "enabled": True}


def _docs_mcp_server_entry() -> CodexMcpServerEntry:
    """Return the OpenAI docs MCP server entry for Codex config."""
    return {"url": "https://developers.openai.com/mcp", "enabled": True}


def _run_async(coro: Coroutine[object, object, _AsyncResultT]) -> _AsyncResultT:
    """Run an async coroutine from sync bootstrap code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _registered_trw_tool_names() -> list[str]:
    """Return the current TRW MCP tool names from the registered server."""
    from trw_mcp.server._app import mcp

    tools = cast("list[_NamedTool]", _run_async(mcp.list_tools()))
    tool_names = sorted(tool.name for tool in tools if tool.name.startswith(_TRW_TOOL_PREFIX))
    if not tool_names:
        logger.warning("codex_trw_tool_discovery_empty")
    return tool_names


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


def _trw_mcp_enabled_tools(existing_server: CodexMcpServerEntry) -> list[str]:
    """Return the documented enabled_tools list for the TRW MCP server."""
    enabled_tools = {
        tool_name
        for tool_name in existing_server.get("enabled_tools", [])
        if isinstance(tool_name, str) and not tool_name.startswith(_TRW_TOOL_PREFIX)
    }
    enabled_tools.update(_registered_trw_tool_names())
    return sorted(enabled_tools)


def _trw_mcp_disabled_tools(existing_server: CodexMcpServerEntry) -> list[str]:
    """Preserve disabled tools that are outside the current TRW tool set."""
    current_trw_tools = set(_registered_trw_tool_names())
    disabled_tools = {
        tool_name
        for tool_name in existing_server.get("disabled_tools", [])
        if isinstance(tool_name, str) and tool_name not in current_trw_tools
    }
    return sorted(disabled_tools)


def _parse_codex_toml(content: str) -> CodexConfigDict:
    """Parse Codex TOML config into a dict."""
    return cast("CodexConfigDict", tomllib.loads(content))


def _toml_key(key: str) -> str:
    """Render a TOML key, quoting only when required."""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if key and all(char in allowed for char in key):
        return key
    return json.dumps(key)


def _toml_value(value: object) -> str:
    """Render a TOML literal for the subset used by Codex bootstrap."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            inline_tables: list[str] = []
            for item in value:
                dict_item = cast("dict[str, object]", item)
                parts = [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in dict_item.items()]
                inline_tables.append("{ " + ", ".join(parts) + " }")
            return "[\n  " + ",\n  ".join(inline_tables) + ",\n]"
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")


def _toml_dumps(data: dict[str, object]) -> str:
    """Serialize the Codex config structure to TOML without external deps."""
    lines: list[str] = []

    def emit_table(table: dict[str, object], prefix: str | None = None) -> None:
        scalar_items: list[tuple[str, object]] = []
        child_tables: list[tuple[str, dict[str, object]]] = []

        for key, value in table.items():
            if isinstance(value, dict):
                child_tables.append((key, cast("dict[str, object]", value)))
            else:
                scalar_items.append((key, value))

        if prefix is not None:
            lines.append(f"[{prefix}]")
        for key, value in scalar_items:
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
        if prefix is not None and (scalar_items or child_tables):
            lines.append("")

        for key, child in child_tables:
            child_prefix = _toml_key(key) if prefix is None else f"{prefix}.{_toml_key(key)}"
            emit_table(child, child_prefix)

    emit_table(data)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _skill_paths() -> list[str]:
    """Return repo-local skill paths for Codex config."""
    skills_dir = _codex_skills_source_dir()
    return [f".agents/skills/{skill_dir.name}" for skill_dir in sorted(skills_dir.iterdir()) if skill_dir.is_dir()]


def _normalize_skill_path(path: str) -> str:
    """Normalize Codex skill paths to the containing skill directory."""
    normalized = path.replace("\\", "/")
    suffix = "/SKILL.md"
    if normalized.endswith(suffix):
        return normalized[: -len(suffix)]
    return normalized


def _normalize_skill_config(existing: object) -> list[CodexSkillConfigEntry]:
    """Coerce a parsed `skills.config` payload to a typed list."""
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
    fallback_files = raw_fallback_files if isinstance(raw_fallback_files, list) else []
    instruction_path = _codex_instruction_path()
    normalized_fallbacks: list[str] = []
    for value in fallback_files:
        if not isinstance(value, str) or value == instruction_path or value in normalized_fallbacks:
            continue
        normalized_fallbacks.append(value)
    return normalized_fallbacks


def _merge_skill_config(raw_skills: object) -> CodexSkillsConfig:
    """Normalize skill entries and ensure bundled Codex skills are enabled."""
    if isinstance(raw_skills, dict):
        skills = cast("CodexSkillsConfig", dict(raw_skills))
    else:
        skills = {}

    skill_config = _normalize_skill_config(skills.get("config"))
    existing_paths = {path for entry in skill_config if isinstance(path := entry.get("path"), str)}
    normalized_skill_config: list[CodexSkillConfigEntry] = []
    for entry in skill_config:
        path = entry.get("path")
        if not isinstance(path, str):
            continue
        normalized_skill_entry: CodexSkillConfigEntry = {"path": path}
        enabled = entry.get("enabled")
        if isinstance(enabled, bool):
            normalized_skill_entry["enabled"] = enabled
        normalized_skill_config.append(normalized_skill_entry)

    normalized_skill_config.extend(
        {"path": path, "enabled": True} for path in _skill_paths() if path not in existing_paths
    )
    skills["config"] = normalized_skill_config
    return skills


def merge_codex_config(existing: CodexConfigDict) -> CodexConfigDict:
    """Merge TRW-managed Codex config into an existing config dict."""
    result = cast("CodexConfigDict", dict(existing))
    instruction_path = _codex_instruction_path()

    result["features"] = _normalize_feature_flags(result.get("features"))

    mcp_servers = _normalize_mcp_servers(result.get("mcp_servers"))
    existing_trw_server: CodexMcpServerEntry = mcp_servers.get("trw", {})
    trw_server = _trw_mcp_server_entry()
    trw_server["enabled_tools"] = _trw_mcp_enabled_tools(existing_trw_server)
    disabled_tools = _trw_mcp_disabled_tools(existing_trw_server)
    if disabled_tools:
        trw_server["disabled_tools"] = disabled_tools
    mcp_servers["trw"] = trw_server
    mcp_servers.setdefault("openaiDeveloperDocs", _docs_mcp_server_entry())
    result["mcp_servers"] = mcp_servers

    fallback_files = _normalize_fallback_files(result.get("project_doc_fallback_filenames"))
    if fallback_files:
        result["project_doc_fallback_filenames"] = fallback_files
    else:
        result.pop("project_doc_fallback_filenames", None)
    result["model_instructions_file"] = instruction_path
    result["skills"] = _merge_skill_config(result.get("skills"))

    return result


def codex_hooks_enabled(target_dir: Path) -> bool:
    """Return whether Codex hooks are explicitly enabled in the repo config."""
    config_path = target_dir / _CODEX_CONFIG_PATH
    if not config_path.exists():
        return False

    try:
        config = _parse_codex_toml(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False

    features = _normalize_feature_flags(config.get("features"))
    return features.get("codex_hooks", False)


def generate_codex_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Generate or smart-merge `.codex/config.toml`."""
    result: BootstrapFileResult = cast("BootstrapFileResult", _new_result())
    codex_dir = target_dir / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    config_path = codex_dir / "config.toml"
    existed = config_path.exists()

    if existed and not force:
        try:
            existing = _parse_codex_toml(config_path.read_text(encoding="utf-8"))
            merged = merge_codex_config(existing)
            config_path.write_text(_toml_dumps(cast("dict[str, object]", merged)), encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_CONFIG_PATH, existed=True)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            result["errors"].append(f"Failed to read/merge {config_path}: {exc}")
    else:
        try:
            merged = merge_codex_config({})
            config_path.write_text(_toml_dumps(cast("dict[str, object]", merged)), encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_CONFIG_PATH, existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {config_path}: {exc}")

    return result


def _trw_hook_group(
    *,
    event: str,
    script_name: str,
    status_message: str | None = None,
    matcher: str | None = None,
    timeout: int | None = None,
) -> CodexHookMatcherEntry:
    """Create a single TRW-managed hook matcher group."""
    git_root = "$(git rev-parse --show-toplevel)"
    command = f'/bin/sh "{git_root}/.claude/hooks/{script_name}"'
    hook_command: CodexHookCommand = {"type": "command", "command": command}
    if status_message is not None:
        hook_command["statusMessage"] = status_message
    if timeout is not None:
        hook_command["timeout"] = timeout

    group: CodexHookMatcherEntry = {
        "description": f"{_TRW_HOOK_DESCRIPTION_PREFIX} {event}",
        "hooks": [hook_command],
    }
    if matcher is not None:
        group["matcher"] = matcher
    return group


def _codex_hooks_payload() -> CodexHooksConfig:
    """Return a Codex hooks.json payload backed by existing TRW shell hooks."""
    return {
        "hooks": {
            "SessionStart": [
                _trw_hook_group(
                    event="SessionStart",
                    matcher="startup|resume",
                    script_name="session-start.sh",
                    status_message="Loading TRW session context",
                )
            ],
            "UserPromptSubmit": [
                _trw_hook_group(
                    event="UserPromptSubmit",
                    script_name="user-prompt-submit.sh",
                    status_message="Checking TRW phase guidance",
                )
            ],
            "PreToolUse": [
                _trw_hook_group(
                    event="PreToolUse",
                    script_name="pre-tool-deliver-gate.sh",
                    status_message="Checking TRW delivery gate",
                )
            ],
            "PostToolUse": [
                _trw_hook_group(
                    event="PostToolUse",
                    script_name="post-tool-event.sh",
                    status_message="Logging TRW tool effects",
                )
            ],
            "Stop": [_trw_hook_group(event="Stop", script_name="stop-ceremony.sh", timeout=30)],
        }
    }


def _is_trw_hook_group(event: str, group: CodexHookMatcherEntry) -> bool:
    """Identify a TRW-managed hook group in an existing hooks config."""
    description = group.get("description")
    if isinstance(description, str) and description.startswith(_TRW_HOOK_DESCRIPTION_PREFIX):
        return True

    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False

    expected_script_names = {
        "SessionStart": "session-start.sh",
        "UserPromptSubmit": "user-prompt-submit.sh",
        "PreToolUse": "pre-tool-deliver-gate.sh",
        "PostToolUse": "post-tool-event.sh",
        "Stop": "stop-ceremony.sh",
    }
    expected_script = expected_script_names.get(event)
    if expected_script is None:
        return False

    for hook in hooks:
        if isinstance(hook, dict):
            command = hook.get("command")
            if isinstance(command, str) and expected_script in command and "/.claude/hooks/" in command:
                return True
    return False


def merge_codex_hooks(existing: CodexHooksConfig) -> CodexHooksConfig:
    """Merge TRW-managed Codex hooks into an existing hooks config."""
    merged = _normalize_hook_config(existing)
    current_hooks = merged.get("hooks", {})
    trw_hooks = _codex_hooks_payload()["hooks"]
    merged_hooks: dict[str, list[CodexHookMatcherEntry]] = {}

    for event_name in sorted(set(current_hooks) | set(trw_hooks)):
        user_groups = [
            group for group in current_hooks.get(event_name, []) if not _is_trw_hook_group(event_name, group)
        ]
        if event_name in trw_hooks:
            merged_hooks[event_name] = user_groups + trw_hooks[event_name]
        elif user_groups:
            merged_hooks[event_name] = user_groups

    return {"hooks": merged_hooks}


def generate_codex_hooks(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Generate `.codex/hooks.json`."""
    result: BootstrapFileResult = cast("BootstrapFileResult", _new_result())
    codex_dir = target_dir / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    hooks_path = codex_dir / "hooks.json"
    existed = hooks_path.exists()

    try:
        if existed and not force:
            raw_existing = json.loads(hooks_path.read_text(encoding="utf-8"))
            payload = merge_codex_hooks(_normalize_hook_config(raw_existing))
            hooks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_HOOKS_PATH, existed=True)
        else:
            payload = _codex_hooks_payload()
            hooks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_HOOKS_PATH, existed=existed)
    except (OSError, json.JSONDecodeError) as exc:
        result["errors"].append(f"Failed to write {hooks_path}: {exc}")

    return result


_CODEX_AGENT_TEMPLATES: dict[str, str] = {
    "trw-explorer.toml": '''name = "trw_explorer"
description = "Read-only codebase explorer for gathering evidence before edits."
model = "gpt-5.4-mini"
model_reasoning_effort = "medium"
sandbox_mode = "read-only"
developer_instructions = """
Stay in exploration mode.
Trace the real execution path, cite files and symbols, and avoid proposing fixes unless asked.
Prefer fast search and targeted reads over broad scans.
"""
''',
    "trw-implementer.toml": '''name = "trw_implementer"
description = "Implementation-focused agent for bounded code changes in the current repository."
model = "gpt-5.4"
model_reasoning_effort = "medium"
sandbox_mode = "workspace-write"
developer_instructions = """
Own the requested fix or feature slice.
Make the smallest defensible change, keep unrelated files untouched, and validate the behavior you changed.
"""
''',
    "trw-reviewer.toml": '''name = "trw_reviewer"
description = "Read-only reviewer focused on correctness, regressions, security, and missing tests."
model = "gpt-5.4"
model_reasoning_effort = "high"
sandbox_mode = "read-only"
developer_instructions = """
Review like an owner.
Lead with concrete findings, prioritize correctness and missing tests, and avoid style-only feedback unless it hides a real defect.
"""
''',
    "trw-docs-researcher.toml": '''name = "trw_docs_researcher"
description = "Documentation specialist that uses docs MCP servers to verify APIs and runtime behavior."
model = "gpt-5.4-mini"
model_reasoning_effort = "medium"
sandbox_mode = "read-only"
developer_instructions = """
Use configured docs MCP servers to confirm APIs, options, and version-specific behavior.
Return concise answers with links or exact references when available.
Do not make code changes.
"""
''',
}


def generate_codex_agents(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Generate `.codex/agents/*.toml`."""
    result: BootstrapFileResult = cast("BootstrapFileResult", _new_result())
    agents_dir = target_dir / _CODEX_AGENTS_DIR
    agents_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in _CODEX_AGENT_TEMPLATES.items():
        path = agents_dir / filename
        try:
            existed = path.exists()
            if existed and not force:
                result["preserved"].append(f"{_CODEX_AGENTS_DIR}/{filename}")
                continue
            path.write_text(content, encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), f"{_CODEX_AGENTS_DIR}/{filename}", existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {path}: {exc}")

    return result


def install_codex_skills(
    target_dir: Path,
    *,
    force: bool = False,
) -> BootstrapFileResult:
    """Install TRW bundled skills into `.agents/skills/` for Codex."""
    from ._init_project import _validate_skill

    result: BootstrapFileResult = cast("BootstrapFileResult", _new_result())
    skills_source = _codex_skills_source_dir()
    dest_root = target_dir / _CODEX_SKILLS_DIR
    dest_root.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted(skills_source.iterdir()):
        if not skill_dir.is_dir():
            continue
        is_valid, reason = _validate_skill(skill_dir)
        if not is_valid:
            logger.warning("codex_skill_validation_failed", skill=skill_dir.name, reason=reason)
            continue

        dest_skill = dest_root / skill_dir.name
        dest_skill.mkdir(parents=True, exist_ok=True)
        for skill_file in sorted(skill_dir.iterdir()):
            if not skill_file.is_file():
                continue
            dest = dest_skill / skill_file.name
            rel_path = f"{_CODEX_SKILLS_DIR}/{skill_dir.name}/{skill_file.name}"
            try:
                if dest.exists() and not force:
                    result["preserved"].append(rel_path)
                    continue
                existed = dest.exists()
                shutil.copy2(skill_file, dest)
                _record_write(cast("dict[str, list[str]]", result), rel_path, existed=existed)
            except OSError as exc:
                result["errors"].append(f"Failed to copy {skill_file} -> {dest}: {exc}")

    return result
