"""Codex-specific bootstrap configuration.

Generates and smart-merges repo-scoped Codex artifacts:
- .codex/config.toml
- optional .codex/hooks.json
- .codex/agents/*.toml
- .agents/skills/
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python <3.11 fallback
    import tomli as tomllib

import structlog

from trw_mcp.bootstrap._codex_hooks import (
    _codex_hooks_payload,
    _is_trw_hook_group,
    _trw_hook_group,
    codex_hooks_review_warning,
    codex_trw_hook_count,
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
    render_config_text,
    split_managed_block,
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
_READINESS_PHASES = frozenset({"trw-prd-groom", "trw-prd-review", "trw-exec-plan"})
_TRW_HOOK_DESCRIPTION_PREFIX = "TRW managed:"
_TRW_PROJECT_DOC = "AGENTS.md"
_LEGACY_PROJECT_DOC = "CLAUDE.md"
_TRW_TOOL_PREFIX = "trw_"

#: TRW tools granted ``approval_mode = "approve"`` in the generated config
#: (PRD-CORE-277-FR06). MEASURED 2026-09-16 against codex-cli 0.154.0: under
#: ``approval_policy = "never"`` every MCP call is refused with "MCP tool call
#: requires approval, but approval policy is never" unless the tool carries an
#: approval mode, which left ``--dangerously-bypass-approvals-and-sandbox`` --
#: which ALSO drops the OS sandbox -- as the only working path.
#:
#: This is the CEREMONY CORE and nothing else: read and progress-recording tools
#: a non-interactive session must call to start from prior context and leave a
#: trace. Deliberately absent, and therefore still prompted for: ``trw_deliver``
#: (records acceptance), ``trw_dispatch`` (launches another agent),
#: ``trw_instructions_sync`` and ``trw_claude_md_sync`` (rewrite the operator's
#: instruction files). ``default_tools_approval_mode`` is NOT emitted: a blanket
#: grant would cover those four the moment a new one is registered.
_CODEX_APPROVED_TOOLS: tuple[str, ...] = (
    "trw_session_start",
    "trw_init",
    "trw_status",
    "trw_checkpoint",
    "trw_recall",
    "trw_learn",
    "trw_peers",
    "trw_send",
    "trw_inbox",
)

#: Keys of ``mcp_servers.trw`` that TRW owns and overwrites on every run.
#: Everything else under that table belongs to the user and is carried through.
#: ``url`` is in the managed set even though TRW never writes it: the stdio
#: launcher IS the portability boundary, so a legacy direct-HTTP entry must be
#: REPLACED, not merged beside a command (a server table carrying both is
#: ambiguous). ``cwd`` and ``env`` are absent on purpose -- those are the user's.
_TRW_MANAGED_SERVER_KEYS: frozenset[str] = frozenset(
    {"command", "args", "url", "enabled", "enabled_tools", "disabled_tools", "tools"}
)

#: The operator's own environment the server reads for the trw_assess backend (enablement, key, endpoint,
#: model). Codex passes an MCP server only an allow-list of variables, so without ``env_vars`` an operator's
#: ``TRW_JEV_ENABLED=false`` never reaches the server and cannot switch the backend off. Forwarding passes
#: along only what the operator set themselves; it never sets a value. Added to the user's own list.
_TRW_FORWARDED_ENV: tuple[str, ...] = ("OPENROUTER_API_KEY", "TRW_JEV_BASE_URL", "TRW_JEV_ENABLED", "TRW_JEV_MODEL")


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
    "codex_hooks_review_warning",
    "codex_trw_hook_count",
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


def _trw_mcp_server_entry(target_dir: Path | None = None) -> CodexMcpServerEntry:
    """Return the TRW MCP server entry for Codex config.

    TRW always writes a stdio launcher: every client spawns its own
    ``trw-mcp`` instance. The ``trw-mcp`` CLI is the portability boundary —
    writing a direct Codex ``url`` is unsupported.

    No ``--debug``: a client profile tunes surface density, never protocol, and
    log verbosity is protocol. Codex, Cursor, and opencode used to bake the flag
    in while Claude Code did not, so the same install logged different things
    depending on which client spawned the server. Verbose logging is now opted
    into once, portably, via ``.trw/config.yaml`` ``debug: true`` (or
    ``TRW_LOG_LEVEL``/``TRW_DEBUG``), which applies to every client alike.
    """
    from trw_mcp.bootstrap._utils import resolve_trw_mcp_launcher

    # One resolver for every client (N17): project .venv, then PATH, then a
    # portable python3 (never sys.executable: .codex/config.toml is committed).
    command, args = resolve_trw_mcp_launcher(target_dir)
    return {"command": command, "args": args, "enabled": True}


def _docs_mcp_server_entry() -> CodexMcpServerEntry:
    """Return the OpenAI docs MCP server entry for Codex config."""
    return {"url": "https://developers.openai.com/mcp", "enabled": True}


def _registered_trw_tool_names() -> list[str]:
    """Return the FULL set of TRW MCP tool names for Codex's enabled_tools.

    Codex manages its own per-tool exposure, so this list must reflect the
    complete tool registry — NOT the live server's session-masked surface. The
    live ``mcp.list_tools()`` view is now narrowed per-session by
    ``SurfaceAuthorityMiddleware`` (PRD-CORE-218), so a bounded resolution would
    otherwise truncate the Codex config and silently hide tools from Codex users.

    The authoritative eligible public manifest supplies this installation list.
    Do not dispatch live list_tools middleware during bootstrap. Unmanifested
    live TRW registrations are intentionally excluded; extensions must declare
    eligibility at the manifest boundary. Registry imports can still initialize
    server machinery, so this is not a server-free import guarantee.

    Why no REVIEWER variant is generated here (PRD-SEC-015-FR10). The reviewer
    bound is a per-lane containment applied at the two dispatch call sites
    (``dispatch/_commands.py`` and ``scripts/audit-external.sh``), not a property
    of the interactive install, for three reasons:

    * this lane is INTERACTIVE and intentionally mirrors Claude Code's
      full-but-server-masked surface — ``SurfaceAuthorityMiddleware`` narrows it
      per session, so a narrower generated allowlist would only desynchronise the
      two layers;
    * TOML comments do not survive the merge-and-rewrite this module performs on
      an existing config, so an in-file warning explaining a reviewer table would
      be silently dropped on the next ``update-project``;
    * Codex **0.134.0 removed ``[profiles.<name>]`` tables**. Profiles are now
      user-scoped standalone ``$CODEX_HOME/<name>.config.toml`` files selected by
      ``--profile``, so a repository *cannot* ship a reviewer profile at all —
      emitting one would be rejected by the client this file targets.

    ``tests/test_bootstrap_codex_split.py::TestCodexNoReviewerProfile`` asserts
    that no ``profiles`` key is ever emitted, so nobody re-adds one.
    """
    from trw_mcp.server._surface_manifest_registry import eligible_tool_names

    tool_names = sorted(name for name in eligible_tool_names() if name.startswith(_TRW_TOOL_PREFIX))
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


def _trw_mcp_tool_approvals(existing_server: CodexMcpServerEntry) -> dict[str, CodexMcpToolConfigEntry]:
    """Grant the ceremony core an approval mode, preserving the user's own entries.

    A user entry WINS for its tool: someone who set ``approval_mode = "prompt"``
    on ``trw_checkpoint`` asked to be asked, and a generated file that silently
    re-approved it would be the same defect as dropping the table. TRW only fills
    in a core tool the user has not spoken about, and only for names the eligible
    manifest still carries -- a renamed tool must not linger as a stale grant.
    """
    registered = set(_registered_trw_tool_names())
    approvals: dict[str, CodexMcpToolConfigEntry] = dict(existing_server.get("tools", {}))
    for tool_name in _CODEX_APPROVED_TOOLS:
        if tool_name not in registered:
            logger.warning("codex_approval_tool_unregistered", tool=tool_name)
            continue
        if tool_name in approvals and "approval_mode" in approvals[tool_name]:
            continue
        entry: CodexMcpToolConfigEntry = dict(approvals.get(tool_name, {}))  # type: ignore[assignment]
        entry["approval_mode"] = "approve"
        approvals[tool_name] = entry
    return approvals


def _skill_paths() -> list[str]:
    """Return repo-local skill paths for Codex config."""
    from ._client_skills import skill_names

    return [f".agents/skills/{name}" for name in skill_names("codex") if name not in _READINESS_PHASES]


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
    # Carry through every key TRW does not own (``env`` above all). The entry used
    # to be REPLACED wholesale, which is why a hand-added [mcp_servers.trw.env]
    # table did not survive one update-project (PRD-CORE-277-FR07).
    for key, value in existing_trw_server.items():
        if key not in _TRW_MANAGED_SERVER_KEYS:
            trw_server[key] = value  # type: ignore[literal-required]
    user_env_vars = existing_trw_server.get("env_vars")
    kept_env_vars = list(user_env_vars) if isinstance(user_env_vars, list) else []
    trw_server["env_vars"] = kept_env_vars + [n for n in _TRW_FORWARDED_ENV if n not in kept_env_vars]
    trw_server["enabled_tools"] = _trw_mcp_enabled_tools(existing_trw_server)
    disabled_tools = _trw_mcp_disabled_tools(existing_trw_server)
    if disabled_tools:
        trw_server["disabled_tools"] = disabled_tools
    approvals = _trw_mcp_tool_approvals(existing_trw_server)
    if approvals:
        trw_server["tools"] = approvals
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
            raw = config_path.read_text(encoding="utf-8")
            existing = _parse_codex_toml(raw)
            merged = merge_codex_config(existing, target_dir=target_dir)
            text = render_config_text(merged, user_region=split_managed_block(raw), result=result)
            config_path.write_text(text, encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_CONFIG_PATH, existed=True)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            result["errors"].append(f"Failed to read/merge {config_path}: {exc}")
    else:
        try:
            merged = merge_codex_config({}, target_dir=target_dir)
            config_path.write_text(render_config_text(merged, user_region="", result=result), encoding="utf-8")
            _record_write(cast("dict[str, list[str]]", result), _CODEX_CONFIG_PATH, existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {config_path}: {exc}")

    return result


def _codex_user_edited(dest: Path, rel: str, incoming: bytes, manifest_hashes: dict[str, str] | None) -> bool:
    """Return True when *dest* is a user edit that must be preserved (FIX B).

    Content-aware refresh: an on-disk artifact that still matches the PREVIOUS
    bundled content (recorded ``manifest_hashes[rel]``) — or the incoming bundle
    content itself — is framework-managed and safe to overwrite with upstream
    fixes; one that diverges from both is a genuine user edit. Falls back to
    "preserve" when there is no manifest record but the content already diverges
    from the incoming bundle (can't prove it's stale-vs-edited).

    Thin alias over the shared predicate. It used to be a private fifth copy of
    the same three lines; ``_managed_client_artifacts`` warns there must not be a
    sixth, and a duty with N implementations is exactly how PRD-FIX-121's defect
    reached seven surfaces.
    """
    from ._managed_client_artifacts import artifact_user_edited

    return artifact_user_edited(dest, rel, incoming, manifest_hashes)


def install_codex_skills(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> BootstrapFileResult:
    """Install TRW bundled skills into `.agents/skills/` for Codex.

    FIX B: an UNMODIFIED skill file is refreshed when its bundled source
    changes; a user-edited one is preserved (content-aware).
    """
    from ._init_project import _validate_skill

    result: BootstrapFileResult = cast("BootstrapFileResult", _new_result())
    dest_root = target_dir / _CODEX_SKILLS_DIR
    dest_root.mkdir(parents=True, exist_ok=True)

    from ._client_skills import canonical_skills_dir, skill_files, skill_names
    from ._optional_skills import CONDITIONAL_SKILLS, retire_disabled_skills, skill_enabled

    canonical = canonical_skills_dir()
    retire_disabled_skills(
        dest_root, canonical, cast("dict[str, list[str]]", result), _CODEX_SKILLS_DIR, client="codex"
    )
    names = [*skill_names("codex"), *(name for name in CONDITIONAL_SKILLS if skill_enabled(name))]
    for skill_dir in (canonical / name for name in names):
        if not skill_dir.is_dir():
            continue
        is_valid, reason = _validate_skill(skill_dir)
        if not is_valid:
            logger.warning("codex_skill_validation_failed", skill=skill_dir.name, reason=reason)
            continue

        internal_phase = skill_dir.name in _READINESS_PHASES
        if internal_phase and (dest_root / skill_dir.name).exists():
            # No ownership proof for old directories: retain and disclose them.
            legacy = f"{_CODEX_SKILLS_DIR}/{skill_dir.name}/SKILL.md"
            result["preserved"].append(legacy)
            logger.warning("codex_legacy_phase_skill_preserved", path=legacy)
        if internal_phase:
            continue  # written as trw-prd-ready/<phase>-contract.md by skill_files("codex", "trw-prd-ready")
        dest_name = skill_dir.name
        dest_skill = dest_root / dest_name
        dest_skill.mkdir(parents=True, exist_ok=True)
        for filename, incoming in skill_files("codex", skill_dir.name):
            dest = dest_skill / filename
            rel_path = f"{_CODEX_SKILLS_DIR}/{dest_name}/{filename}"
            try:
                existed = dest.exists()
                if existed and not force:
                    if _codex_user_edited(dest, rel_path, incoming, manifest_hashes):
                        result["preserved"].append(rel_path)
                        continue
                    if dest.read_bytes() == incoming:
                        result["preserved"].append(rel_path)
                        continue
                dest.write_bytes(incoming)
                _record_write(cast("dict[str, list[str]]", result), rel_path, existed=existed)
            except OSError as exc:
                result["errors"].append(f"Failed to write {dest}: {exc}")

    return result
