"""Project bootstrap — sets up and updates TRW framework in a target directory.

PRD-INFRA-006: ``trw-mcp init-project`` CLI command that copies all
required framework files into a target git repository.

``trw-mcp update-project`` selectively updates framework files (hooks,
skills, agents, FRAMEWORK.md) while preserving user-customized files
(config.yaml, learnings, CLAUDE.md user sections).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger()

_DATA_DIR = Path(__file__).parent / "data"

# Directories to scaffold inside the target repo.
_TRW_DIRS = [
    ".trw/frameworks",
    ".trw/context",
    ".trw/templates",
    ".trw/learnings/entries",
    ".trw/scripts",
    ".claude/hooks",
    ".claude/skills",
    ".claude/agents",
]

# Mapping of bundled data files to their destination paths (relative to target).
_DATA_FILE_MAP: list[tuple[str, str]] = [
    ("framework.md", ".trw/frameworks/FRAMEWORK.md"),
    ("framework.md", "FRAMEWORK.md"),
    ("behavioral_protocol.yaml", ".trw/context/behavioral_protocol.yaml"),
    ("messages/messages.yaml", ".trw/context/messages.yaml"),
    ("templates/claude_md.md", ".trw/templates/claude_md.md"),
    ("gitignore.txt", ".trw/.gitignore"),
    ("settings.json", ".claude/settings.json"),
]


def init_project(
    target_dir: Path,
    *,
    force: bool = False,
    source_package: str = "",
    test_path: str = "",
) -> dict[str, list[str]]:
    """Bootstrap TRW framework in *target_dir*.

    Args:
        target_dir: Root of the target git repository.
        force: If ``True``, overwrite existing files.
        source_package: Pre-populate ``source_package_name`` in config.
        test_path: Pre-populate ``tests_relative_path`` in config.

    Returns:
        Dict with ``created``, ``skipped``, ``errors`` lists.
    """
    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    # Validate target is a git repo
    if not (target_dir / ".git").exists():
        result["errors"].append(
            f"{target_dir} is not a git repository (.git/ not found)"
        )
        return result

    # 1. Create directory structure
    for rel_dir in _TRW_DIRS:
        _ensure_dir(target_dir / rel_dir, result)

    # 2. Copy bundled data files
    for data_name, dest_rel in _DATA_FILE_MAP:
        _copy_file(_DATA_DIR / data_name, target_dir / dest_rel, force, result)

    # 3. Write generated config and seed files
    _write_if_missing(
        target_dir / ".trw" / "config.yaml",
        _default_config(source_package=source_package, test_path=test_path),
        force,
        result,
    )
    _write_if_missing(
        target_dir / ".trw" / "learnings" / "index.yaml",
        "entries: []\n",
        force,
        result,
    )

    # 4. Copy hook scripts
    hooks_source = _DATA_DIR / "hooks"
    if hooks_source.is_dir():
        for hook_file in sorted(hooks_source.iterdir()):
            if hook_file.suffix == ".sh":
                _copy_file(
                    hook_file,
                    target_dir / ".claude" / "hooks" / hook_file.name,
                    force,
                    result,
                )

    # 5. Copy skills
    skills_source = _DATA_DIR / "skills"
    if skills_source.is_dir():
        for skill_dir in sorted(skills_source.iterdir()):
            if skill_dir.is_dir():
                dest_skill = target_dir / ".claude" / "skills" / skill_dir.name
                _ensure_dir(dest_skill, result)
                for skill_file in sorted(skill_dir.iterdir()):
                    if skill_file.is_file():
                        _copy_file(skill_file, dest_skill / skill_file.name, force, result)

    # 6. Copy agents
    agents_source = _DATA_DIR / "agents"
    if agents_source.is_dir():
        for agent_file in sorted(agents_source.iterdir()):
            if agent_file.suffix == ".md":
                _copy_file(
                    agent_file,
                    target_dir / ".claude" / "agents" / agent_file.name,
                    force,
                    result,
                )

    # 7. Generate root-level files
    _merge_mcp_json(target_dir, result)
    _write_if_missing(target_dir / "CLAUDE.md", _minimal_claude_md(), force, result)

    # 8. Write managed-artifacts manifest
    _write_manifest(target_dir, result)

    # 9. Write installer metadata
    _write_installer_metadata(target_dir, "init-project", result)

    logger.info(
        "bootstrap_complete",
        target=str(target_dir),
        created=len(result["created"]),
        skipped=len(result["skipped"]),
        errors=len(result["errors"]),
    )
    return result


# Files that are always overwritten during update (framework-managed).
_ALWAYS_UPDATE: list[tuple[str, str]] = [
    ("framework.md", ".trw/frameworks/FRAMEWORK.md"),
    ("framework.md", "FRAMEWORK.md"),
    ("behavioral_protocol.yaml", ".trw/context/behavioral_protocol.yaml"),
    ("messages/messages.yaml", ".trw/context/messages.yaml"),
    ("templates/claude_md.md", ".trw/templates/claude_md.md"),
    ("settings.json", ".claude/settings.json"),
]

# Files that are never overwritten during update (user-customized).
# These are only created if missing.
_NEVER_OVERWRITE = {
    ".trw/config.yaml",
    ".trw/learnings/index.yaml",
}

# Files that are smart-merged during update (preserve user content, ensure
# framework entries exist).  .mcp.json was previously in _NEVER_OVERWRITE
# which caused the trw server entry to be lost if removed by the user.
_MERGE_FILES = {".mcp.json"}

# CLAUDE.md markers for the auto-generated section.
_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER_MARKER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"


def update_project(
    target_dir: Path,
    *,
    pip_install: bool = False,
    dry_run: bool = False,
    data_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Update TRW framework files in *target_dir* while preserving user config.

    Always updates: hooks, skills, agents, FRAMEWORK.md, behavioral_protocol.yaml,
    claude_md template, settings.json.

    Never overwrites: config.yaml, learnings/.

    Smart merge: .mcp.json — ensures ``trw`` server entry exists while preserving
    all other user-configured MCP servers.

    Smart update: CLAUDE.md — replaces content between ``trw:start``/``trw:end``
    markers while preserving all user-written sections.

    Args:
        target_dir: Root of the target git repository.
        pip_install: If True, reinstall the trw-mcp package after file updates.
        dry_run: If True, report what would change without modifying files.
        data_dir: Optional override for the bundled data directory. When provided,
            artifact lookups use this path instead of the module-level ``_DATA_DIR``.

    Returns:
        Dict with ``updated``, ``created``, ``preserved``, ``errors``,
        and ``warnings`` lists.
    """
    effective_data = data_dir or _DATA_DIR
    result: dict[str, list[str]] = {
        "updated": [],
        "created": [],
        "preserved": [],
        "errors": [],
        "warnings": [],
    }

    if dry_run:
        result["warnings"].append("DRY RUN — no files will be modified.")

    # Validate target has TRW installed
    if not (target_dir / ".trw").exists():
        result["errors"].append(
            f"{target_dir} does not have TRW installed (.trw/ not found). "
            "Run `trw-mcp init-project` first."
        )
        return result

    # 1. Ensure directories exist
    if not dry_run:
        for rel_dir in _TRW_DIRS:
            _ensure_dir(target_dir / rel_dir, result)

    # 2. Update framework files (always overwrite)
    for data_name, dest_rel in _ALWAYS_UPDATE:
        src = effective_data / data_name
        dest = target_dir / dest_rel
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
                if existed:
                    result["updated"].append(str(dest))
                else:
                    result["created"].append(str(dest))
            except OSError as exc:
                result["errors"].append(f"Failed to copy {src} -> {dest}: {exc}")

    # 3. Create-only files (never overwrite existing)
    for rel_path in _NEVER_OVERWRITE:
        dest = target_dir / rel_path
        if dest.exists():
            result["preserved"].append(str(dest))

    # 4. Update hooks (always overwrite)
    hooks_source = effective_data / "hooks"
    if hooks_source.is_dir():
        for hook_file in sorted(hooks_source.iterdir()):
            if hook_file.suffix == ".sh":
                dest = target_dir / ".claude" / "hooks" / hook_file.name
                if dry_run:
                    if dest.exists():
                        if not _files_identical(hook_file, dest):
                            result["updated"].append(f"would update: {dest}")
                    else:
                        result["created"].append(f"would create: {dest}")
                else:
                    existed = dest.exists()
                    try:
                        shutil.copy2(hook_file, dest)
                        if dest.suffix == ".sh":
                            executable = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                            os.chmod(dest, os.stat(dest).st_mode | executable)
                        if existed:
                            result["updated"].append(str(dest))
                        else:
                            result["created"].append(str(dest))
                    except OSError as exc:
                        result["errors"].append(
                            f"Failed to copy {hook_file} -> {dest}: {exc}"
                        )

    # 5. Update skills (always overwrite)
    skills_source = effective_data / "skills"
    if skills_source.is_dir():
        for skill_dir in sorted(skills_source.iterdir()):
            if skill_dir.is_dir():
                dest_skill = target_dir / ".claude" / "skills" / skill_dir.name
                if not dry_run:
                    _ensure_dir(dest_skill, result)
                for skill_file in sorted(skill_dir.iterdir()):
                    if skill_file.is_file():
                        dest = dest_skill / skill_file.name
                        if dry_run:
                            if dest.exists():
                                if not _files_identical(skill_file, dest):
                                    result["updated"].append(f"would update: {dest}")
                            else:
                                result["created"].append(f"would create: {dest}")
                        else:
                            existed = dest.exists()
                            try:
                                shutil.copy2(skill_file, dest)
                                if existed:
                                    result["updated"].append(str(dest))
                                else:
                                    result["created"].append(str(dest))
                            except OSError as exc:
                                result["errors"].append(
                                    f"Failed to copy {skill_file} -> {dest}: {exc}"
                                )

    # 6. Update agents (always overwrite)
    agents_source = effective_data / "agents"
    if agents_source.is_dir():
        for agent_file in sorted(agents_source.iterdir()):
            if agent_file.suffix == ".md":
                dest = target_dir / ".claude" / "agents" / agent_file.name
                if dry_run:
                    if dest.exists():
                        if not _files_identical(agent_file, dest):
                            result["updated"].append(f"would update: {dest}")
                    else:
                        result["created"].append(f"would create: {dest}")
                else:
                    existed = dest.exists()
                    try:
                        shutil.copy2(agent_file, dest)
                        if existed:
                            result["updated"].append(str(dest))
                        else:
                            result["created"].append(str(dest))
                    except OSError as exc:
                        result["errors"].append(
                            f"Failed to copy {agent_file} -> {dest}: {exc}"
                        )

    # 7. Smart-merge .mcp.json (ensure trw entry, preserve user entries)
    if not dry_run:
        _merge_mcp_json(target_dir, result)
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

    # 8. Smart-update CLAUDE.md (preserve user sections, update trw block)
    claude_md_path = target_dir / "CLAUDE.md"
    if dry_run:
        if claude_md_path.exists():
            result["updated"].append(f"would update: {claude_md_path} (TRW section)")
        else:
            result["created"].append(f"would create: {claude_md_path}")
    else:
        if claude_md_path.exists():
            _update_claude_md_trw_section(claude_md_path, result)
        else:
            try:
                claude_md_path.write_text(_minimal_claude_md(), encoding="utf-8")
                result["created"].append(str(claude_md_path))
            except OSError as exc:
                result["errors"].append(f"Failed to write {claude_md_path}: {exc}")

    # 9. Remove stale hooks/skills/agents no longer in bundled data
    if not dry_run:
        _remove_stale_artifacts(target_dir, result, data_dir)

    # 10. Check installed package version
    _check_package_version(result)

    # 11. Reinstall package if requested
    if pip_install and not dry_run:
        _pip_install_package(target_dir, result)

    # 12. Write installer metadata
    if not dry_run:
        _write_installer_metadata(target_dir, "update-project", result)

    # 13. Post-update verification
    if not dry_run:
        _verify_installation(target_dir, result)

    # 13b. Post-update CLAUDE.md sync (resolve placeholders, promote learnings)
    if not dry_run:
        _run_claude_md_sync(target_dir, result)

    # 14. Remind about running sessions
    result["warnings"].append(
        "Running Claude Code sessions use cached hooks/settings. "
        "Restart active sessions (or run /mcp) to pick up updates."
    )

    logger.info(
        "update_complete",
        target=str(target_dir),
        updated=len(result["updated"]),
        created=len(result["created"]),
        preserved=len(result["preserved"]),
        errors=len(result["errors"]),
        dry_run=dry_run,
    )
    return result


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
        # No TRW section — append it
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + new_block
        try:
            claude_md_path.write_text(content, encoding="utf-8")
            result["updated"].append(str(claude_md_path))
        except OSError as exc:
            result["errors"].append(f"Failed to update {claude_md_path}: {exc}")
    else:
        result["errors"].append(
            f"CLAUDE.md has malformed TRW markers — found start but not end"
        )


def _minimal_claude_md_trw_block() -> str:
    """Return just the auto-generated TRW section for CLAUDE.md updates."""
    # Extract the TRW block from the full template
    full = _minimal_claude_md()
    start_idx = full.find(_TRW_HEADER_MARKER)
    end_idx = full.find(_TRW_END_MARKER)
    if start_idx != -1 and end_idx != -1:
        return full[start_idx : end_idx + len(_TRW_END_MARKER)] + "\n"
    # Fallback: return entire trw:start..trw:end
    start_idx = full.find(_TRW_START_MARKER)
    if start_idx != -1 and end_idx != -1:
        return full[start_idx : end_idx + len(_TRW_END_MARKER)] + "\n"
    return ""


def _get_bundled_names(data_dir: Path | None = None) -> dict[str, list[str]]:
    """Return sorted lists of bundled artifact names by category."""
    effective = data_dir or _DATA_DIR
    skills_source = effective / "skills"
    agents_source = effective / "agents"
    hooks_source = effective / "hooks"
    return {
        "skills": sorted(
            d.name for d in skills_source.iterdir() if d.is_dir()
        ) if skills_source.is_dir() else [],
        "agents": sorted(
            f.name for f in agents_source.iterdir() if f.suffix == ".md"
        ) if agents_source.is_dir() else [],
        "hooks": sorted(
            f.name for f in hooks_source.iterdir() if f.suffix == ".sh"
        ) if hooks_source.is_dir() else [],
    }


def _get_custom_names(target_dir: Path, data_dir: Path | None = None) -> dict[str, list[str]]:
    """Return sorted lists of user-created artifact names not in bundled data."""
    bundled = _get_bundled_names(data_dir)
    bundled_skills = set(bundled["skills"])
    bundled_agents = set(bundled["agents"])
    bundled_hooks = set(bundled["hooks"])
    result: dict[str, list[str]] = {"skills": [], "agents": [], "hooks": []}

    skills_dir = target_dir / ".claude" / "skills"
    if skills_dir.is_dir():
        result["skills"] = sorted(
            d.name for d in skills_dir.iterdir()
            if d.is_dir() and d.name not in bundled_skills
        )

    agents_dir = target_dir / ".claude" / "agents"
    if agents_dir.is_dir():
        result["agents"] = sorted(
            f.name for f in agents_dir.iterdir()
            if f.suffix == ".md" and f.name not in bundled_agents
        )

    hooks_dir = target_dir / ".claude" / "hooks"
    if hooks_dir.is_dir():
        result["hooks"] = sorted(
            f.name for f in hooks_dir.iterdir()
            if f.suffix == ".sh" and f.name not in bundled_hooks
        )

    return result


_MANIFEST_FILE = "managed-artifacts.yaml"


def _read_manifest(target_dir: Path) -> dict[str, list[str]] | None:
    """Read the managed-artifacts manifest from a target project.

    Returns ``None`` if the manifest does not exist (first update after
    manifest support was added).
    """
    manifest_path = target_dir / ".trw" / _MANIFEST_FILE
    if not manifest_path.exists():
        return None
    try:
        from trw_mcp.state.persistence import FileStateReader

        reader = FileStateReader()
        data = reader.read_yaml(manifest_path)
        if not isinstance(data, dict):
            return None
        skills_raw = data.get("skills", [])
        agents_raw = data.get("agents", [])
        hooks_raw = data.get("hooks", [])
        custom_skills_raw = data.get("custom_skills", [])
        custom_agents_raw = data.get("custom_agents", [])
        custom_hooks_raw = data.get("custom_hooks", [])
        return {
            "skills": [str(s) for s in skills_raw] if isinstance(skills_raw, list) else [],
            "agents": [str(a) for a in agents_raw] if isinstance(agents_raw, list) else [],
            "hooks": [str(h) for h in hooks_raw] if isinstance(hooks_raw, list) else [],
            "custom_skills": [str(s) for s in custom_skills_raw] if isinstance(custom_skills_raw, list) else [],
            "custom_agents": [str(a) for a in custom_agents_raw] if isinstance(custom_agents_raw, list) else [],
            "custom_hooks": [str(h) for h in custom_hooks_raw] if isinstance(custom_hooks_raw, list) else [],
        }
    except OSError:
        return None


def _write_manifest(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None = None,
) -> None:
    """Write the managed-artifacts manifest to the target project.

    The manifest records which skills, agents, and hooks were installed
    by TRW so that ``_remove_stale_artifacts`` can distinguish
    TRW-managed artifacts from user-created custom ones.
    """
    bundled = _get_bundled_names(data_dir)
    custom = _get_custom_names(target_dir, data_dir)
    manifest = {
        "version": 1,
        "skills": bundled["skills"],
        "agents": bundled["agents"],
        "hooks": bundled["hooks"],
        "custom_skills": custom["skills"],
        "custom_agents": custom["agents"],
        "custom_hooks": custom["hooks"],
    }
    manifest_path = target_dir / ".trw" / _MANIFEST_FILE
    try:
        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        writer.write_yaml(manifest_path, manifest)
        key = "updated" if "updated" in result else "created"
        result[key].append(str(manifest_path))
    except OSError as exc:
        result["errors"].append(f"Failed to write manifest: {exc}")


def _remove_stale_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None = None,
) -> None:
    """Remove hooks/skills/agents that no longer exist in bundled data.

    Uses a manifest file (``.trw/managed-artifacts.yaml``) to track which
    artifacts were previously installed by TRW.  Only artifacts listed in
    the manifest are candidates for removal — custom user-created
    artifacts are never touched.

    On the first update after manifest support is added, no stale cleanup
    is performed (the manifest is written for future updates).
    """
    prev_manifest = _read_manifest(target_dir)
    bundled = _get_bundled_names(data_dir)
    bundled_skills = set(bundled["skills"])
    bundled_agents = set(bundled["agents"])
    bundled_hooks = set(bundled["hooks"])

    if prev_manifest is None:
        # First run with manifest support — write manifest, skip cleanup
        _write_manifest(target_dir, result, data_dir)
        return

    prev_skills = set(prev_manifest.get("skills", []))
    prev_agents = set(prev_manifest.get("agents", []))
    prev_hooks = set(prev_manifest.get("hooks", []))
    prev_custom_skills = set(prev_manifest.get("custom_skills", []))
    prev_custom_agents = set(prev_manifest.get("custom_agents", []))
    prev_custom_hooks = set(prev_manifest.get("custom_hooks", []))

    # Stale skills: previously managed but no longer in current bundle
    stale_skills = prev_skills - bundled_skills
    target_skills = target_dir / ".claude" / "skills"
    if target_skills.is_dir():
        for skill_name in stale_skills:
            if skill_name in prev_custom_skills:
                continue
            stale = target_skills / skill_name
            if stale.is_dir():
                try:
                    shutil.rmtree(stale)
                    result["updated"].append(f"removed:{stale}")
                except OSError:
                    pass

    # Stale agents: previously managed but no longer in current bundle
    stale_agents = prev_agents - bundled_agents
    target_agents = target_dir / ".claude" / "agents"
    if target_agents.is_dir():
        for agent_name in stale_agents:
            if agent_name in prev_custom_agents:
                continue
            stale = target_agents / agent_name
            if stale.is_file():
                try:
                    stale.unlink()
                    result["updated"].append(f"removed:{stale}")
                except OSError:
                    pass

    # Stale hooks: previously managed but no longer in current bundle
    stale_hooks = prev_hooks - bundled_hooks
    target_hooks = target_dir / ".claude" / "hooks"
    if target_hooks.is_dir():
        for hook_name in stale_hooks:
            if hook_name in prev_custom_hooks:
                continue
            stale = target_hooks / hook_name
            if stale.is_file():
                try:
                    stale.unlink()
                    result["updated"].append(f"removed:{stale}")
                except OSError:
                    pass

    # Write updated manifest
    _write_manifest(target_dir, result, data_dir)


def _check_package_version(result: dict[str, list[str]]) -> None:
    """Compare installed trw-mcp version against source version.

    Warns if the installed package is outdated, which means server-side
    fixes (log filtering, LLM client, tool logic) won't be active.
    """
    from trw_mcp import __version__ as source_version

    try:
        installed_version = importlib.metadata.version("trw-mcp")
    except importlib.metadata.PackageNotFoundError:
        result["warnings"].append(
            "trw-mcp package not found in Python environment. "
            "Install with: pip install -e trw-mcp[dev]"
        )
        return

    if installed_version != source_version:
        result["warnings"].append(
            f"Installed trw-mcp ({installed_version}) differs from source "
            f"({source_version}). Server-side fixes require reinstall: "
            f"pip install -e trw-mcp[dev]"
        )
    else:
        result["preserved"].append(
            f"trw-mcp package v{installed_version} (up to date)"
        )


def _pip_install_package(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Reinstall trw-mcp package from the source tree.

    Uses the trw-mcp directory that contains the bundled data, ensuring
    the installed package matches the source version.
    """
    # The package source is the parent of the data directory
    package_dir = _DATA_DIR.parent.parent.parent  # trw-mcp/src -> trw-mcp/
    if not (package_dir / "pyproject.toml").exists():
        # Fall back: try to find trw-mcp relative to data dir
        package_dir = _DATA_DIR.parent.parent.parent
        if not (package_dir / "pyproject.toml").exists():
            result["errors"].append(
                "Cannot find trw-mcp pyproject.toml for pip install. "
                "Manually run: pip install -e /path/to/trw-mcp[dev]"
            )
            return

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", f"{package_dir}[dev]"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode == 0:
            result["updated"].append(f"pip install trw-mcp (reinstalled)")
        else:
            result["errors"].append(
                f"pip install failed (exit {proc.returncode}): {proc.stderr[:200]}"
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        result["errors"].append(f"pip install failed: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_dir(path: Path, result: dict[str, list[str]]) -> None:
    """Create directory if it doesn't exist."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        result["created"].append(str(path) + "/")
    # Already existing dirs are silently fine -- not worth reporting as "skipped".


def _copy_file(
    src: Path,
    dest: Path,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Copy *src* to *dest* with idempotency."""
    if dest.exists() and not force:
        result["skipped"].append(str(dest))
        return
    try:
        shutil.copy2(src, dest)
        # Ensure shell scripts are executable (pip install may strip permissions)
        if dest.suffix == ".sh":
            executable = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            os.chmod(dest, os.stat(dest).st_mode | executable)
        result["created"].append(str(dest))
    except OSError as exc:
        result["errors"].append(f"Failed to copy {src} -> {dest}: {exc}")


def _write_if_missing(
    dest: Path,
    content: str,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Write *content* to *dest* if it doesn't exist (or *force* is True)."""
    if dest.exists() and not force:
        result["skipped"].append(str(dest))
        return
    try:
        dest.write_text(content, encoding="utf-8")
        result["created"].append(str(dest))
    except OSError as exc:
        result["errors"].append(f"Failed to write {dest}: {exc}")


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def _default_config(
    *,
    source_package: str = "",
    test_path: str = "",
) -> str:
    """Generate default ``.trw/config.yaml``.

    Args:
        source_package: If set, adds ``source_package_name`` field.
        test_path: If set, adds ``tests_relative_path`` field.
    """
    lines = [
        "# TRW Framework Configuration",
        "# See trw://config resource for all available fields.",
        "task_root: docs",
        "debug: false",
        "claude_md_max_lines: 500",
    ]
    if source_package:
        lines.append(f"source_package_name: {source_package}")
    if test_path:
        lines.append(f"tests_relative_path: {test_path}")
    return "\n".join(lines) + "\n"


def _files_identical(a: Path, b: Path) -> bool:
    """Compare two files by SHA-256 hash for dry-run diffing."""
    try:
        ha = hashlib.sha256(a.read_bytes()).hexdigest()
        hb = hashlib.sha256(b.read_bytes()).hexdigest()
        return ha == hb
    except OSError:
        return False


def _trw_mcp_server_entry() -> dict[str, object]:
    """Build the ``trw`` MCP server entry for .mcp.json.

    Prefers the venv-local ``trw-mcp`` (same Python that has all deps)
    over a system-wide install which may lack trw_memory.
    """
    # Check for venv-local trw-mcp first (same Python with all deps)
    venv_bin = Path(sys.executable).parent / "trw-mcp"
    if venv_bin.exists():
        cmd = str(venv_bin)
    else:
        system_cmd = shutil.which("trw-mcp")
        cmd = system_cmd or f"{sys.executable} -m trw_mcp.server"
    return {"command": cmd, "args": ["--debug"]}


def _merge_mcp_json(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Ensure ``.mcp.json`` has the ``trw`` server entry.

    Reads existing .mcp.json, merges the ``trw`` key into ``mcpServers``
    while preserving all other user-configured servers, and writes back.
    Creates the file from scratch if it doesn't exist.
    """
    mcp_path = target_dir / ".mcp.json"
    trw_entry = _trw_mcp_server_entry()

    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            servers = {}
        existed = "trw" in servers
        servers["trw"] = trw_entry
        data["mcpServers"] = servers
        try:
            mcp_path.write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8",
            )
            key = "updated" if "updated" in result else "created"
            if existed:
                result[key].append(str(mcp_path))
            else:
                result[key].append(f"{mcp_path} (added trw entry)")
        except OSError as exc:
            result["errors"].append(f"Failed to write {mcp_path}: {exc}")
    else:
        content = json.dumps(
            {"mcpServers": {"trw": trw_entry}}, indent=2,
        ) + "\n"
        try:
            mcp_path.write_text(content, encoding="utf-8")
            result["created"].append(str(mcp_path))
        except OSError as exc:
            result["errors"].append(f"Failed to write {mcp_path}: {exc}")


def _write_installer_metadata(
    target_dir: Path,
    action: str,
    result: dict[str, list[str]],
) -> None:
    """Write ``.trw/installer-meta.yaml`` with deployment metadata.

    Tracks framework version, package version, timestamp, and artifact
    counts so audits can detect stale deployments.
    """
    from trw_mcp import __version__ as pkg_version
    from trw_mcp.models.config import get_config

    config = get_config()

    # Count deployed artifacts
    hooks_dir = target_dir / ".claude" / "hooks"
    skills_dir = target_dir / ".claude" / "skills"
    agents_dir = target_dir / ".claude" / "agents"
    hooks_count = len(list(hooks_dir.glob("*.sh"))) if hooks_dir.is_dir() else 0
    skills_count = len([d for d in skills_dir.iterdir() if d.is_dir()]) if skills_dir.is_dir() else 0
    agents_count = len(list(agents_dir.glob("*.md"))) if agents_dir.is_dir() else 0

    meta = {
        "framework_version": config.framework_version,
        "package_version": pkg_version,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "installed_by": f"trw-mcp {action}",
        "hooks_count": hooks_count,
        "skills_count": skills_count,
        "agents_count": agents_count,
    }
    meta_path = target_dir / ".trw" / "installer-meta.yaml"
    try:
        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        writer.write_yaml(meta_path, meta)
        # init_project uses "created", update_project uses "updated"
        key = "updated" if "updated" in result else "created"
        result[key].append(str(meta_path))
    except OSError as exc:
        result["errors"].append(f"Failed to write {meta_path}: {exc}")


def _verify_installation(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Run lightweight post-update health checks.

    Verifies hooks are executable, .mcp.json has trw entry, and
    CLAUDE.md has TRW markers.  Adds warnings for any failures.
    """
    # Check hooks are executable
    hooks_dir = target_dir / ".claude" / "hooks"
    if hooks_dir.is_dir():
        for hook in hooks_dir.glob("*.sh"):
            if not os.access(hook, os.X_OK):
                result["warnings"].append(f"Hook not executable: {hook.name}")

    # Check .mcp.json has trw entry
    mcp_path = target_dir / ".mcp.json"
    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            if "trw" not in data.get("mcpServers", {}):
                result["warnings"].append(
                    ".mcp.json missing 'trw' server entry"
                )
        except (json.JSONDecodeError, OSError):
            result["warnings"].append(".mcp.json is not valid JSON")
    else:
        result["warnings"].append(".mcp.json not found")

    # Check CLAUDE.md has TRW markers
    claude_md = target_dir / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if _TRW_START_MARKER not in content or _TRW_END_MARKER not in content:
            result["warnings"].append(
                "CLAUDE.md missing TRW auto-generated markers"
            )


def _run_claude_md_sync(target_dir: Path, result: dict[str, list[str]]) -> None:
    """Run CLAUDE.md sync after update to resolve placeholders and promote learnings.

    Temporarily changes cwd to the target project so that resolve_project_root()
    finds the correct .trw/ directory and learnings database.
    Fail-open: rendering errors are logged as warnings but never break the update.
    """
    import os

    original_cwd = Path.cwd()
    try:
        os.chdir(target_dir)

        from trw_mcp.state.claude_md import execute_claude_md_sync
        from trw_mcp.models.config import get_config, _reset_config
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter
        from trw_mcp.state.llm_helpers import LLMClient

        # Reset config so it picks up the target project's .trw/config.yaml
        _reset_config()
        config = get_config()
        reader = FileStateReader()
        writer = FileStateWriter()
        llm = LLMClient()

        sync_result = execute_claude_md_sync(
            scope="root",
            target_dir=None,
            config=config,
            reader=reader,
            writer=writer,
            llm=llm,
        )
        result["updated"].append(
            f"CLAUDE.md synced (learnings promoted: {sync_result.get('learnings_promoted', 0)})"
        )
    except Exception as exc:
        result["warnings"].append(f"CLAUDE.md sync skipped: {exc}")
    finally:
        os.chdir(original_cwd)
        # Reset config back to original project
        try:
            from trw_mcp.models.config import _reset_config
            _reset_config()
        except Exception:
            pass


def _generate_mcp_json() -> str:
    """Generate ``.mcp.json`` pointing to installed trw-mcp.

    Legacy helper kept for backward compatibility. New code uses
    ``_merge_mcp_json()`` which does smart merging.
    """
    entry = _trw_mcp_server_entry()
    return json.dumps({"mcpServers": {"trw": entry}}, indent=2) + "\n"


def _minimal_claude_md() -> str:
    """Generate ``CLAUDE.md`` with behavioral protocol and tool reference."""
    return """\
# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What This Is

{Describe your project here}

## Build & Test Commands

```bash
# Add your project's build and test commands here
```

## Project Conventions

{Add project-specific conventions here}

<!-- TRW AUTO-GENERATED — do not edit between markers -->
<!-- trw:start -->

TRW tools help you build effectively and preserve your work across sessions:
- **Start**: call `trw_session_start()` to load prior learnings and recover any active run
- **Finish**: call `trw_deliver()` to persist your learnings for future sessions

## TRW Behavioral Protocol (Auto-Generated)

- `trw_session_start()` loads your prior learnings and recovers any active run — call it first so you have full context before writing code
- `trw_status()` shows your current phase, completed work, and next steps — call it when resuming so you pick up where you left off instead of redoing work
- `trw_init(task_name)` creates your run directory and event log — call it for new tasks so checkpoints and progress tracking work
- `trw_checkpoint(message)` saves your implementation progress — call it after each milestone so you can resume here if context compacts, instead of re-implementing from scratch
- `trw_learn(summary, detail)` records discoveries for all future sessions — call it when you hit errors or find gotchas so no agent repeats your mistakes
- `trw_claude_md_sync()` promotes your best learnings into CLAUDE.md — call it at delivery so the next session starts with your insights built in
- For quick tasks without a run: `trw_recall()` gives you relevant prior learnings at the start, `trw_learn()` saves new ones for next time

## TRW Ceremony Tools (Auto-Generated)

### Execution Phases

```
RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER
```

### Tool Lifecycle

| Phase | Tool | When to Use |
|-------|------|-------------|
| Start | `trw_session_start` | At session start — loads learnings + run state |
| Start | `trw_recall` | Quick tasks — retrieves relevant prior learnings |
| Start | `trw_status` | When resuming — shows phase, progress, next steps |
| RESEARCH | `trw_init` | New tasks — creates run directory for tracking |
| Any | `trw_learn` | On errors/discoveries — saves for future sessions |
| Any | `trw_checkpoint` | After milestones — preserves progress across compactions |
| VALIDATE | `trw_build_check` | Before delivery — runs pytest + mypy |
| DELIVER | `trw_claude_md_sync` | At delivery — promotes learnings to CLAUDE.md |
| DELIVER | `trw_deliver` | At task completion — persists everything in one call |

### Example Flows

**Quick Task** (no run needed):
```
trw_session_start -> work -> trw_learn (if discovery) -> trw_deliver()
```

**Full Run**:
```
trw_session_start -> trw_init(task_name, prd_scope)
  -> work + trw_checkpoint (periodic) + trw_learn (discoveries)
  -> trw_build_check(scope='full')
  -> trw_deliver()
```

<!-- trw:end -->
"""
