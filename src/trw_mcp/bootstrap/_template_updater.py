"""Template updater — file copying, CLAUDE.md management, artifact discovery.

Handles:
- Copying/updating framework-managed files (hooks, skills, agents, etc.)
- CLAUDE.md auto-generated section management (marker-based replacement)
- MCP config smart-merge
- Artifact name discovery (bundled vs. custom)

IDE-specific logic (opencode, cursor, config target_platforms, CLAUDE.md sync)
lives in ``_ide_targets.py`` and is re-exported here for backward compatibility.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import structlog

from ._ide_targets import _extract_trw_section_content as _extract_trw_section_content
from ._ide_targets import _run_claude_md_sync as _run_claude_md_sync
from ._ide_targets import _update_codex_artifacts as _update_codex_artifacts
from ._ide_targets import _update_config_target_platforms as _update_config_target_platforms
from ._ide_targets import _update_copilot_artifacts as _update_copilot_artifacts
from ._ide_targets import _update_cursor_artifacts as _update_cursor_artifacts
from ._ide_targets import _update_gemini_artifacts as _update_gemini_artifacts
from ._ide_targets import _update_opencode_artifacts as _update_opencode_artifacts
from ._utils import (
    _DATA_DIR,
    ProgressCallback,
    _ensure_dir,
    _files_identical,
    _merge_mcp_json,
    _minimal_claude_md,
)

logger = structlog.get_logger(__name__)

# Files that are always overwritten during update (framework-managed).
_ALWAYS_UPDATE: list[tuple[str, str]] = [
    ("framework.md", ".trw/frameworks/FRAMEWORK.md"),
    ("framework.md", "FRAMEWORK.md"),
    ("behavioral_protocol.yaml", ".trw/context/behavioral_protocol.yaml"),
    ("messages/messages.yaml", ".trw/context/messages.yaml"),
    ("templates/claude_md.md", ".trw/templates/claude_md.md"),
]

# Files that are never overwritten during update (user-customized).
# These are only created if missing.
_NEVER_OVERWRITE = {
    ".trw/config.yaml",
    ".trw/learnings/index.yaml",
}

# CLAUDE.md markers for the auto-generated section.
_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER_MARKER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"


# ---------------------------------------------------------------------------
# Update helpers
# ---------------------------------------------------------------------------


def _update_or_report(
    src: Path,
    dest: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    *,
    make_executable: bool = False,
    on_progress: ProgressCallback = None,
) -> None:
    """Copy *src* to *dest* (or report what would change in dry-run mode).

    Args:
        src: Source file to copy from.
        dest: Destination path to copy to.
        result: Mutable result dict.
        dry_run: When ``True``, only report without writing.
        make_executable: When ``True``, set executable bits on *dest* after copy.
        on_progress: Optional callback for real-time progress reporting.
    """
    if dry_run:
        if dest.exists():
            if not _files_identical(src, dest):
                result["updated"].append(f"would update: {dest}")
        else:
            result["created"].append(f"would create: {dest}")
    else:
        existed = dest.exists()
        try:
            shutil.copy2(src, dest)
            if make_executable:
                executable = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                os.chmod(dest, os.stat(dest).st_mode | executable)
            if existed:
                result["updated"].append(str(dest))
                if on_progress:
                    on_progress("Updated", str(dest))
            else:
                result["created"].append(str(dest))
                if on_progress:
                    on_progress("Created", str(dest))
        except OSError as exc:
            result["errors"].append(f"Failed to copy {src} -> {dest}: {exc}")
            if on_progress:
                on_progress("Error", str(dest))


def _update_always_overwrite_files(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
) -> None:
    """Update framework files in ``_ALWAYS_UPDATE`` (always overwritten)."""
    for data_name, dest_rel in _ALWAYS_UPDATE:
        src = effective_data / data_name
        dest = target_dir / dest_rel
        _update_or_report(src, dest, result, dry_run, on_progress=on_progress)


def _report_preserved_files(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Report create-only files in ``_NEVER_OVERWRITE`` that already exist."""
    for rel_path in _NEVER_OVERWRITE:
        dest = target_dir / rel_path
        if dest.exists():
            result["preserved"].append(str(dest))


def _merge_settings_json(
    src: Path,
    dest: Path,
    result: dict[str, list[str]],
    dry_run: bool = False,
) -> None:
    """Smart-merge bundled settings.json into existing user settings.

    PRD-INFRA-044-FR04: Preserves user opt-out of ENABLE_TOOL_SEARCH
    while adding missing env keys from the bundled template. All
    non-env top-level keys (hooks, permissions, etc.) from the existing
    file are preserved.
    """
    _log = structlog.get_logger(__name__)
    if not src.is_file():
        return
    if not dest.exists():
        # New install — copy bundled template directly
        _update_or_report(src, dest, result, dry_run)
        return
    if dry_run:
        result["updated"].append(f"would merge: {dest}")
        return
    try:
        bundled = json.loads(src.read_text(encoding="utf-8"))
        existing = json.loads(dest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("settings_json_merge_fallback", path=str(dest), reason=str(exc))
        _update_or_report(src, dest, result, dry_run)
        return

    # Merge env block: add missing keys, preserve existing values
    bundled_env = bundled.get("env", {})
    existing_env = existing.get("env", {})
    for key, value in bundled_env.items():
        if key not in existing_env:
            existing_env[key] = value
    existing["env"] = existing_env

    # Merge hooks: add missing hook event types, preserve existing
    bundled_hooks = bundled.get("hooks", {})
    existing_hooks = existing.get("hooks", {})
    for hook_event, hook_list in bundled_hooks.items():
        if hook_event not in existing_hooks:
            existing_hooks[hook_event] = hook_list
    existing["hooks"] = existing_hooks

    try:
        dest.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        result["updated"].append(str(dest))
    except OSError as exc:
        result["errors"].append(f"Failed to merge settings.json: {exc}")


def _update_hooks(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
) -> None:
    """Update hook ``.sh`` files (always overwritten, made executable)."""
    hooks_source = effective_data / "hooks"
    if hooks_source.is_dir():
        for hook_file in sorted(hooks_source.iterdir()):
            if hook_file.suffix == ".sh":
                dest = target_dir / ".claude" / "hooks" / hook_file.name
                _update_or_report(
                    hook_file,
                    dest,
                    result,
                    dry_run,
                    make_executable=True,
                    on_progress=on_progress,
                )


def _update_skills(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
) -> None:
    """Update skill directories (always overwritten)."""
    skills_source = effective_data / "skills"
    if skills_source.is_dir():
        for skill_dir in sorted(skills_source.iterdir()):
            if skill_dir.is_dir():
                dest_skill = target_dir / ".claude" / "skills" / skill_dir.name
                if not dry_run:
                    _ensure_dir(dest_skill, result, on_progress)
                for skill_file in sorted(skill_dir.iterdir()):
                    if skill_file.is_file():
                        dest = dest_skill / skill_file.name
                        _update_or_report(skill_file, dest, result, dry_run, on_progress=on_progress)


def _is_user_modified(
    dest: Path,
    name: str,
    manifest_hashes: dict[str, str] | None,
) -> bool:
    """Check if an installed file was modified by the user since last install.

    PRD-FIX-068-FR05: Compares current on-disk SHA256 against the stored
    manifest hash.  Returns ``True`` when hashes differ (user modification).
    """
    if not manifest_hashes or name not in manifest_hashes:
        return False
    if not dest.is_file():
        return False
    try:
        current_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
        return current_hash != manifest_hashes[name]
    except OSError:
        return False


def _update_agents(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Update agent ``.md`` files.

    PRD-FIX-068-FR05: If the installed file's SHA256 differs from the
    manifest hash, the file is user-modified and is NOT overwritten.
    """
    _log = structlog.get_logger(__name__)
    agents_source = effective_data / "agents"
    if agents_source.is_dir():
        for agent_file in sorted(agents_source.iterdir()):
            if agent_file.suffix == ".md":
                dest = target_dir / ".claude" / "agents" / agent_file.name
                if _is_user_modified(dest, agent_file.name, manifest_hashes):
                    _log.info("artifact_user_modified", path=str(dest))
                    result.setdefault("modified", []).append(str(dest))
                    continue
                _update_or_report(agent_file, dest, result, dry_run, on_progress=on_progress)


def _update_framework_files(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Copy/update all framework-managed files from bundled data.

    Handles:
    - Framework files in ``_ALWAYS_UPDATE`` (always overwritten).
    - Never-overwrite files in ``_NEVER_OVERWRITE`` (preserved reporting).
    - Hook ``.sh`` files (always overwritten, made executable).
    - Skill directories (always overwritten).
    - Agent ``.md`` files (overwritten unless user-modified per PRD-FIX-068-FR05).

    Args:
        target_dir: Root of the target git repository.
        effective_data: Resolved bundled data directory (may be overridden by
            the caller for testing).
        result: Mutable result dict accumulating ``updated``, ``created``,
            ``preserved``, ``modified``, and ``errors`` entries.
        dry_run: When ``True``, report what would change without writing files.
        on_progress: Optional callback for real-time progress reporting.
        manifest_hashes: SHA256 content hashes from prior manifest for
            user-modification detection (PRD-FIX-068-FR05).
    """
    _update_always_overwrite_files(target_dir, effective_data, result, dry_run, on_progress)
    _report_preserved_files(target_dir, result)
    # PRD-INFRA-044-FR04: Smart-merge settings.json (preserves user ENABLE_TOOL_SEARCH opt-out)
    _merge_settings_json(
        effective_data / "settings.json",
        target_dir / ".claude" / "settings.json",
        result,
        dry_run,
    )
    _update_hooks(target_dir, effective_data, result, dry_run, on_progress)
    _update_skills(target_dir, effective_data, result, dry_run, on_progress)
    _update_agents(target_dir, effective_data, result, dry_run, on_progress, manifest_hashes)


# ---------------------------------------------------------------------------
# MCP config + CLAUDE.md update
# ---------------------------------------------------------------------------


def _update_mcp_config(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
) -> None:
    """Update ``.mcp.json`` and ``CLAUDE.md`` configuration files.

    Handles the smart-merge of ``.mcp.json`` (ensures the ``trw`` server entry
    is present while preserving all other user-configured MCP servers) and the
    smart-update of ``CLAUDE.md`` (replaces the TRW auto-generated section while
    preserving all user-written content outside the markers).

    Args:
        target_dir: Root of the target git repository.
        result: Mutable result dict accumulating ``updated``, ``created``,
            ``preserved``, and ``errors`` entries.
        dry_run: When ``True``, report what would change without writing files.
        on_progress: Optional callback for real-time progress reporting.
    """
    # Smart-merge .mcp.json (ensure trw entry, preserve user entries)
    if not dry_run:
        _merge_mcp_json(target_dir, result, on_progress)
    else:
        mcp_path = target_dir / ".mcp.json"
        if mcp_path.exists():
            try:
                data = json.loads(mcp_path.read_text(encoding="utf-8"))
                servers = data.get("mcpServers", {})
                if "trw" not in servers:
                    result["updated"].append(f"would merge: {mcp_path} (add trw entry)")
                else:
                    result["preserved"].append(str(mcp_path))
            except (json.JSONDecodeError, OSError):
                result["updated"].append(f"would merge: {mcp_path}")
        else:
            result["created"].append(f"would create: {mcp_path}")

    # Smart-update CLAUDE.md (preserve user sections, update trw block)
    claude_md_path = target_dir / "CLAUDE.md"
    if dry_run:
        if claude_md_path.exists():
            result["updated"].append(f"would update: {claude_md_path} (TRW section)")
        else:
            result["created"].append(f"would create: {claude_md_path}")
    else:
        if claude_md_path.exists():
            _update_claude_md_trw_section(claude_md_path, result)
            if on_progress and str(claude_md_path) in result.get("updated", []):
                on_progress("Updated", str(claude_md_path))
        else:
            try:
                claude_md_path.write_text(_minimal_claude_md(), encoding="utf-8")
                result["created"].append(str(claude_md_path))
                if on_progress:
                    on_progress("Created", str(claude_md_path))
            except OSError as exc:
                result["errors"].append(f"Failed to write {claude_md_path}: {exc}")
                if on_progress:
                    on_progress("Error", str(claude_md_path))


# ---------------------------------------------------------------------------
# CLAUDE.md section management
# ---------------------------------------------------------------------------


def _update_claude_md_trw_section(
    claude_md_path: Path,
    result: dict[str, list[str]],
) -> None:
    """Replace the auto-generated TRW section in CLAUDE.md.

    Preserves all user-written content above and below the markers.
    """
    content = claude_md_path.read_text(encoding="utf-8")
    new_block = _minimal_claude_md_trw_block()

    start_idx = content.find(_TRW_START_MARKER)
    end_idx = content.find(_TRW_END_MARKER)

    if start_idx != -1 and end_idx != -1:
        # Replace the existing auto-generated section
        end_idx += len(_TRW_END_MARKER)
        # Also capture the header marker line if present
        header_idx = content.rfind(_TRW_HEADER_MARKER, 0, start_idx)
        replace_start = header_idx if header_idx != -1 else start_idx
        updated = content[:replace_start] + new_block + content[end_idx:]
        try:
            claude_md_path.write_text(updated, encoding="utf-8")
            result["updated"].append(str(claude_md_path))
        except OSError as exc:
            result["errors"].append(f"Failed to update {claude_md_path}: {exc}")
    elif _TRW_START_MARKER not in content:
        # No TRW section -- append it
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + new_block
        try:
            claude_md_path.write_text(content, encoding="utf-8")
            result["updated"].append(str(claude_md_path))
        except OSError as exc:
            result["errors"].append(f"Failed to update {claude_md_path}: {exc}")
    else:
        result["errors"].append("CLAUDE.md has malformed TRW markers — found start but not end")


def _minimal_claude_md_trw_block() -> str:
    """Return just the auto-generated TRW section for CLAUDE.md updates."""
    import sys

    # Look up _minimal_claude_md via the package module so that
    # patch("trw_mcp.bootstrap._minimal_claude_md", ...) in tests
    # correctly intercepts the call.
    bootstrap_pkg = sys.modules["trw_mcp.bootstrap"]
    full: str = bootstrap_pkg._minimal_claude_md()
    start_idx = full.find(_TRW_HEADER_MARKER)
    end_idx = full.find(_TRW_END_MARKER)
    if start_idx != -1 and end_idx != -1:
        return str(full[start_idx : end_idx + len(_TRW_END_MARKER)]) + "\n"
    # Fallback: return entire trw:start..trw:end
    start_idx = full.find(_TRW_START_MARKER)
    if start_idx != -1 and end_idx != -1:
        return str(full[start_idx : end_idx + len(_TRW_END_MARKER)]) + "\n"
    return ""


# ---------------------------------------------------------------------------
# Artifact name discovery
# ---------------------------------------------------------------------------


def _get_bundled_names(data_dir: Path | None = None) -> dict[str, list[str]]:
    """Return sorted lists of bundled artifact names by category."""
    effective = data_dir or _DATA_DIR
    skills_source = effective / "skills"
    agents_source = effective / "agents"
    hooks_source = effective / "hooks"
    opencode_root = effective / "opencode"
    opencode_commands = opencode_root / "commands"
    opencode_agents = opencode_root / "agents"
    opencode_skills = opencode_root / "skills"
    return {
        "skills": sorted(d.name for d in skills_source.iterdir() if d.is_dir()) if skills_source.is_dir() else [],
        "agents": sorted(f.name for f in agents_source.iterdir() if f.suffix == ".md")
        if agents_source.is_dir()
        else [],
        "hooks": sorted(f.name for f in hooks_source.iterdir() if f.suffix == ".sh") if hooks_source.is_dir() else [],
        "opencode_commands": sorted(f.name for f in opencode_commands.iterdir() if f.suffix == ".md")
        if opencode_commands.is_dir()
        else [],
        "opencode_agents": sorted(f.name for f in opencode_agents.iterdir() if f.suffix == ".md")
        if opencode_agents.is_dir()
        else [],
        "opencode_skills": sorted(d.name for d in opencode_skills.iterdir() if d.is_dir())
        if opencode_skills.is_dir()
        else [],
    }


def _get_custom_names(target_dir: Path, data_dir: Path | None = None) -> dict[str, list[str]]:
    """Return sorted lists of user-created artifact names not in bundled data."""
    bundled = _get_bundled_names(data_dir)
    bundled_skills = set(bundled["skills"])
    bundled_agents = set(bundled["agents"])
    bundled_hooks = set(bundled["hooks"])
    bundled_opencode_commands = set(bundled.get("opencode_commands", []))
    bundled_opencode_agents = set(bundled.get("opencode_agents", []))
    bundled_opencode_skills = set(bundled.get("opencode_skills", []))
    result: dict[str, list[str]] = {
        "skills": [],
        "agents": [],
        "hooks": [],
        "opencode_commands": [],
        "opencode_agents": [],
        "opencode_skills": [],
    }

    skills_dir = target_dir / ".claude" / "skills"
    if skills_dir.is_dir():
        result["skills"] = sorted(d.name for d in skills_dir.iterdir() if d.is_dir() and d.name not in bundled_skills)

    agents_dir = target_dir / ".claude" / "agents"
    if agents_dir.is_dir():
        result["agents"] = sorted(
            f.name for f in agents_dir.iterdir() if f.suffix == ".md" and f.name not in bundled_agents
        )

    hooks_dir = target_dir / ".claude" / "hooks"
    if hooks_dir.is_dir():
        result["hooks"] = sorted(
            f.name for f in hooks_dir.iterdir() if f.suffix == ".sh" and f.name not in bundled_hooks
        )

    opencode_commands_dir = target_dir / ".opencode" / "commands"
    if opencode_commands_dir.is_dir():
        result["opencode_commands"] = sorted(
            f.name
            for f in opencode_commands_dir.iterdir()
            if f.suffix == ".md" and f.name not in bundled_opencode_commands
        )

    opencode_agents_dir = target_dir / ".opencode" / "agents"
    if opencode_agents_dir.is_dir():
        result["opencode_agents"] = sorted(
            f.name for f in opencode_agents_dir.iterdir() if f.suffix == ".md" and f.name not in bundled_opencode_agents
        )

    opencode_skills_dir = target_dir / ".opencode" / "skills"
    if opencode_skills_dir.is_dir():
        result["opencode_skills"] = sorted(
            d.name for d in opencode_skills_dir.iterdir() if d.is_dir() and d.name not in bundled_opencode_skills
        )

    return result
