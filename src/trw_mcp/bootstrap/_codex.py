"""Codex-specific bootstrap configuration.

Generates and smart-merges repo-scoped Codex artifacts:
- .codex/config.toml
- optional .codex/hooks.json
- .codex/agents/*.toml
- .agents/skills/
"""

from __future__ import annotations

import asyncio
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

from trw_mcp.bootstrap._codex_hooks import (
    _codex_hooks_payload,
    _is_trw_hook_group,
    _trw_hook_group,
    generate_codex_hooks,
    merge_codex_hooks,
)
from trw_mcp.bootstrap._codex_normalize import (
    _normalize_fallback_files,
    _normalize_feature_flags,
    _normalize_hook_command,
    _normalize_hook_config,
    _normalize_hook_matcher_group,
    _normalize_mcp_server_entry,
    _normalize_mcp_servers,
    _normalize_mcp_tool_config,
    _normalize_skill_config,
)
from trw_mcp.bootstrap._codex_toml import (
    _parse_codex_toml,
    _toml_dumps,
    _toml_key,
    _toml_value,
)
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

__all__ = [
    "BootstrapFileResult",
    "CodexConfigDict",
    "CodexFeaturesConfig",
    "CodexHookCommand",
    "CodexHookMatcherEntry",
    "CodexHooksConfig",
    "CodexMcpServerEntry",
    "CodexMcpToolConfigEntry",
    "CodexSkillConfigEntry",
    "CodexSkillsConfig",
    "_codex_hooks_payload",
    "_is_trw_hook_group",
    "_normalize_fallback_files",
    "_normalize_feature_flags",
    "_normalize_hook_command",
    "_normalize_hook_config",
    "_normalize_hook_matcher_group",
    "_normalize_mcp_server_entry",
    "_normalize_mcp_servers",
    "_normalize_mcp_tool_config",
    "_normalize_skill_config",
    "_parse_codex_toml",
    "_toml_dumps",
    "_toml_key",
    "_toml_value",
    "_trw_hook_group",
    "generate_codex_agents",
    "generate_codex_config",
    "generate_codex_hooks",
    "install_codex_skills",
    "merge_codex_config",
    "merge_codex_hooks",
]


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


def _trw_mcp_server_entry(target_dir: Path | None = None) -> CodexMcpServerEntry:
    """Return the TRW MCP server entry for Codex config."""
    project_executable = target_dir / ".venv" / "bin" / "trw-mcp" if target_dir is not None else None
    if project_executable is not None and project_executable.exists():
        command = ".venv/bin/trw-mcp"
        args = ["--debug"]
    elif shutil.which("trw-mcp"):
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


def merge_codex_config(existing: CodexConfigDict, *, target_dir: Path | None = None) -> CodexConfigDict:
    """Merge TRW-managed Codex config into an existing config dict."""
    result = cast("CodexConfigDict", dict(existing))
    instruction_path = _codex_instruction_path()

    result["features"] = _normalize_feature_flags(result.get("features"))

    mcp_servers = _normalize_mcp_servers(result.get("mcp_servers"))
    existing_trw_server: CodexMcpServerEntry = mcp_servers.get("trw", {})
    trw_server = _trw_mcp_server_entry(target_dir)
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
    # Codex resolves model_instructions_file relative to .codex/, so strip
    # the leading ".codex/" prefix from the repo-relative instruction_path.
    _codex_prefix = ".codex/"
    if instruction_path.startswith(_codex_prefix):
        result["model_instructions_file"] = instruction_path[len(_codex_prefix) :]
    else:
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
    return features.get("hooks", False)


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
            merged = merge_codex_config(existing, target_dir=target_dir)
            config_path.write_text(_toml_dumps(cast("dict[str, object]", merged)), encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_CONFIG_PATH, existed=True)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            result["errors"].append(f"Failed to read/merge {config_path}: {exc}")
    else:
        try:
            merged = merge_codex_config({}, target_dir=target_dir)
            config_path.write_text(_toml_dumps(cast("dict[str, object]", merged)), encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_CONFIG_PATH, existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {config_path}: {exc}")

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
