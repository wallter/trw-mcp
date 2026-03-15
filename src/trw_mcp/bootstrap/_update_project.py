"""update_project flow — selectively updates TRW framework files.

``trw-mcp update-project`` selectively updates framework files (hooks,
skills, agents, FRAMEWORK.md) while preserving user-customized files
(config.yaml, learnings, CLAUDE.md user sections).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import structlog

from ._utils import (
    _DATA_DIR,
    ProgressCallback,
    _check_package_version,
    _ensure_dir,
    _files_identical,
    _merge_mcp_json,
    _minimal_claude_md,
    _pip_install_package,
    _result_action_key,
    _verify_installation,
    _write_installer_metadata,
    _write_version_yaml,
    detect_ide,
    resolve_ide_targets,
)

logger = structlog.get_logger()

# Files that are always overwritten during update (framework-managed).
_ALWAYS_UPDATE: list[tuple[str, str]] = [
    ("framework.md", ".trw/frameworks/FRAMEWORK.md"),
    ("framework.md", "FRAMEWORK.md"),
    ("behavioral_protocol.yaml", ".trw/context/behavioral_protocol.yaml"),
    ("messages/messages.yaml", ".trw/context/messages.yaml"),
    ("templates/claude_md.md", ".trw/templates/claude_md.md"),
    ("settings.json", ".claude/settings.json"),
    ("trw_readme.md", "docs/TRW_README.md"),
    ("config_reference.md", "docs/CONFIG-REFERENCE.md"),
]

# Files that are never overwritten during update (user-customized).
# These are only created if missing.
_NEVER_OVERWRITE = {
    ".trw/config.yaml",
    ".trw/learnings/index.yaml",
}

# Files in .trw/context/ that are always preserved during cleanup.
_CONTEXT_ALLOWLIST: frozenset[str] = frozenset({
    "analytics.yaml",
    "behavioral_protocol.yaml",
    "build-status.yaml",
    "messages.yaml",
    "pre_compact_state.json",
    "hooks-reference.yaml",
})

# CLAUDE.md markers for the auto-generated section.
_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER_MARKER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"

_MANIFEST_FILE = "managed-artifacts.yaml"


def _coerce_manifest_list(value: object) -> list[str]:
    """Coerce a manifest field to ``list[str]``, returning ``[]`` for non-lists."""
    return [str(item) for item in value] if isinstance(value, list) else []

# PRD-FIX-032: Maps old non-prefixed skill/agent names to their trw- successors.
# Used by _migrate_prefix_predecessors() to remove stale predecessors during
# update-project when the trw- prefixed successor is already installed.
PREDECESSOR_MAP: dict[str, dict[str, str]] = {
    "skills": {
        "audit": "trw-audit",
        "commit": "trw-commit",
        "deliver": "trw-deliver",
        "exec-plan": "trw-exec-plan",
        "framework-check": "trw-framework-check",
        "learn": "trw-learn",
        "memory-audit": "trw-memory-audit",
        "memory-optimize": "trw-memory-optimize",
        "prd-groom": "trw-prd-groom",
        "prd-new": "trw-prd-new",
        "prd-review": "trw-prd-review",
        "project-health": "trw-project-health",
        "review-pr": "trw-review-pr",
        "security-check": "trw-security-check",
        "simplify": "trw-simplify",
        "sprint-finish": "trw-sprint-finish",
        "sprint-init": "trw-sprint-init",
        "sprint-team": "trw-sprint-team",
        "team-playbook": "trw-team-playbook",
        "test-strategy": "trw-test-strategy",
    },
    "agents": {
        "code-simplifier.md": "trw-code-simplifier.md",
        "implementer.md": "trw-implementer.md",
        "lead.md": "trw-lead.md",
        "researcher.md": "trw-researcher.md",
        "reviewer.md": "trw-reviewer.md",
        "tester.md": "trw-tester.md",
        "adversarial-auditor.md": "trw-adversarial-auditor.md",
        "prd-groomer.md": "trw-prd-groomer.md",
        "requirement-reviewer.md": "trw-requirement-reviewer.md",
        "requirement-writer.md": "trw-requirement-writer.md",
        "traceability-checker.md": "trw-traceability-checker.md",
    },
}


# ---------------------------------------------------------------------------
# Context cleanup
# ---------------------------------------------------------------------------


def _cleanup_context_transients(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool = False,
) -> None:
    """Remove transient artifacts from .trw/context/ during update-project.

    Preserves files in ``_CONTEXT_ALLOWLIST``.  Deletes everything else that
    is a regular file (not a directory, not a symlink).

    Args:
        target_dir: Root of the target git repository.
        result: Mutable result dict -- cleaned paths appended to ``result["cleaned"]``.
        dry_run: When ``True``, report what would be removed without deleting.
    """
    context_dir = target_dir / ".trw" / "context"
    if not context_dir.is_dir():
        return

    cleaned: list[str] = []
    for path in sorted(context_dir.iterdir()):
        # Skip symlinks first -- is_file() returns True for symlinks to files
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        if path.name in _CONTEXT_ALLOWLIST:
            continue
        if dry_run:
            result["cleaned"].append(f"would remove: {path}")
        else:
            try:
                path.unlink()
                result["cleaned"].append(str(path))
                cleaned.append(path.name)
            except OSError as exc:
                result["errors"].append(f"Failed to remove {path}: {exc}")

    logger.info(
        "context_cleanup",
        target=str(target_dir),
        cleaned_count=len(cleaned),
        dry_run=dry_run,
    )


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
                    hook_file, dest, result, dry_run,
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


def _update_agents(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
) -> None:
    """Update agent ``.md`` files (always overwritten)."""
    agents_source = effective_data / "agents"
    if agents_source.is_dir():
        for agent_file in sorted(agents_source.iterdir()):
            if agent_file.suffix == ".md":
                dest = target_dir / ".claude" / "agents" / agent_file.name
                _update_or_report(agent_file, dest, result, dry_run, on_progress=on_progress)


def _update_framework_files(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
) -> None:
    """Copy/update all framework-managed files from bundled data.

    Handles:
    - Framework files in ``_ALWAYS_UPDATE`` (always overwritten).
    - Never-overwrite files in ``_NEVER_OVERWRITE`` (preserved reporting).
    - Hook ``.sh`` files (always overwritten, made executable).
    - Skill directories (always overwritten).
    - Agent ``.md`` files (always overwritten).

    Args:
        target_dir: Root of the target git repository.
        effective_data: Resolved bundled data directory (may be overridden by
            the caller for testing).
        result: Mutable result dict accumulating ``updated``, ``created``,
            ``preserved``, and ``errors`` entries.
        dry_run: When ``True``, report what would change without writing files.
        on_progress: Optional callback for real-time progress reporting.
    """
    _update_always_overwrite_files(target_dir, effective_data, result, dry_run, on_progress)
    _report_preserved_files(target_dir, result)
    _update_hooks(target_dir, effective_data, result, dry_run, on_progress)
    _update_skills(target_dir, effective_data, result, dry_run, on_progress)
    _update_agents(target_dir, effective_data, result, dry_run, on_progress)


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
        result["errors"].append(
            "CLAUDE.md has malformed TRW markers — found start but not end"
        )


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


# ---------------------------------------------------------------------------
# Manifest management
# ---------------------------------------------------------------------------


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
        return {
            key: _coerce_manifest_list(data.get(key, []))
            for key in (
                "skills", "agents", "hooks",
                "custom_skills", "custom_agents", "custom_hooks",
            )
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
    # PRD-FIX-032-FR05: Exclude predecessor names from custom lists so they
    # are not permanently protected as false-custom entries.
    predecessor_skills = set(PREDECESSOR_MAP["skills"].keys())
    predecessor_agents = set(PREDECESSOR_MAP["agents"].keys())
    manifest = {
        "version": 1,
        "skills": bundled["skills"],
        "agents": bundled["agents"],
        "hooks": bundled["hooks"],
        "custom_skills": [s for s in custom["skills"] if s not in predecessor_skills],
        "custom_agents": [a for a in custom["agents"] if a not in predecessor_agents],
        "custom_hooks": custom["hooks"],
    }
    manifest_path = target_dir / ".trw" / _MANIFEST_FILE
    try:
        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        writer.write_yaml(manifest_path, manifest)
        key = _result_action_key(result)
        result[key].append(str(manifest_path))
    except OSError as exc:
        result["errors"].append(f"Failed to write manifest: {exc}")


# ---------------------------------------------------------------------------
# Migration & stale artifact removal
# ---------------------------------------------------------------------------


def _migrate_predecessor_set(
    parent_dir: Path,
    name_map: dict[str, str],
    result: dict[str, list[str]],
    *,
    is_dir_artifact: bool,
    log_event: str,
    dry_run: bool,
) -> None:
    """Remove predecessor artifacts when their ``trw-`` successor is installed.

    Args:
        parent_dir: Directory containing both predecessor and successor artifacts.
        name_map: Mapping of old (predecessor) name to new (successor) name.
        result: Mutable result dict.
        is_dir_artifact: ``True`` for directory artifacts (skills), ``False`` for files (agents).
        log_event: structlog event name on removal failure.
        dry_run: When ``True``, only report without deleting.
    """
    for old_name, new_name in name_map.items():
        predecessor = parent_dir / old_name
        successor = parent_dir / new_name
        if is_dir_artifact:
            if not predecessor.is_dir() or not successor.is_dir():
                continue
        else:
            if not predecessor.is_file() or not successor.is_file():
                continue
        if dry_run:
            result["updated"].append(f"would migrate:{predecessor}")
            continue
        try:
            if is_dir_artifact:
                shutil.rmtree(predecessor)
            else:
                predecessor.unlink()
            result["updated"].append(f"migrated:{predecessor}")
        except OSError:
            logger.debug(log_event, path=str(predecessor), exc_info=True)


def _migrate_prefix_predecessors(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool = False,
) -> None:
    """Remove non-prefixed predecessor skills/agents when trw- successor is installed.

    PRD-FIX-032: Projects initialized before the trw- prefix migration
    (PRD-INFRA-013) still have old non-prefixed skill directories and agent
    files.  This function removes them only when the trw- prefixed successor
    is already present, ensuring no data loss.

    This function is intended for ``update_project()`` only.  It is called
    before ``_remove_stale_artifacts()`` so the manifest written afterwards
    is already clean of predecessor entries.
    """
    skills_dir = target_dir / ".claude" / "skills"
    agents_dir = target_dir / ".claude" / "agents"

    _migrate_predecessor_set(
        skills_dir, PREDECESSOR_MAP["skills"], result,
        is_dir_artifact=True, log_event="predecessor_skill_removal_failed", dry_run=dry_run,
    )
    _migrate_predecessor_set(
        agents_dir, PREDECESSOR_MAP["agents"], result,
        is_dir_artifact=False, log_event="predecessor_agent_removal_failed", dry_run=dry_run,
    )


def _remove_stale_set(
    stale_names: set[str],
    target_dir: Path,
    prev_custom: set[str],
    result: dict[str, list[str]],
    *,
    is_dir_artifact: bool,
    log_event: str,
    require_prefix: bool = True,
) -> None:
    """Remove a set of stale artifacts from *target_dir*.

    Skips names that are in *prev_custom* (user-created).  When
    *require_prefix* is ``True`` (the default), also skips names that do
    not start with ``trw-`` (defense-in-depth for skills and agents).

    Args:
        stale_names: Artifact names to consider for removal.
        target_dir: Directory containing the artifacts.
        prev_custom: Names from the previous manifest's custom list.
        result: Mutable result dict.
        is_dir_artifact: ``True`` to use ``shutil.rmtree``, ``False`` to use ``unlink``.
        log_event: structlog event name on removal failure.
        require_prefix: When ``True``, only remove names starting with ``trw-``.
    """
    if not target_dir.is_dir():
        return
    for name in stale_names:
        if name in prev_custom:
            continue
        if require_prefix and not name.startswith("trw-"):
            continue
        stale = target_dir / name
        exists = stale.is_dir() if is_dir_artifact else stale.is_file()
        if not exists:
            continue
        try:
            if is_dir_artifact:
                shutil.rmtree(stale)
            else:
                stale.unlink()
            result["updated"].append(f"removed:{stale}")
        except OSError:
            logger.debug(log_event, path=str(stale), exc_info=True)


def _remove_stale_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None = None,
) -> None:
    """Remove hooks/skills/agents that no longer exist in bundled data.

    Uses a manifest file (``.trw/managed-artifacts.yaml``) to track which
    artifacts were previously installed by TRW.  Only artifacts listed in
    the manifest are candidates for removal -- custom user-created
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
        # First run with manifest support -- write manifest, skip cleanup
        _write_manifest(target_dir, result, data_dir)
        return

    prev_skills = set(prev_manifest.get("skills", []))
    prev_agents = set(prev_manifest.get("agents", []))
    prev_hooks = set(prev_manifest.get("hooks", []))
    prev_custom_skills = set(prev_manifest.get("custom_skills", []))
    prev_custom_agents = set(prev_manifest.get("custom_agents", []))
    prev_custom_hooks = set(prev_manifest.get("custom_hooks", []))

    # Remove stale artifacts per category
    # Defense-in-depth: only remove trw-prefixed items to protect custom artifacts
    _remove_stale_set(
        stale_names=prev_skills - bundled_skills,
        target_dir=target_dir / ".claude" / "skills",
        prev_custom=prev_custom_skills,
        result=result,
        is_dir_artifact=True,
        log_event="stale_skill_removal_failed",
    )
    _remove_stale_set(
        stale_names=prev_agents - bundled_agents,
        target_dir=target_dir / ".claude" / "agents",
        prev_custom=prev_custom_agents,
        result=result,
        is_dir_artifact=False,
        log_event="stale_agent_removal_failed",
    )
    _remove_stale_set(
        stale_names=prev_hooks - bundled_hooks,
        target_dir=target_dir / ".claude" / "hooks",
        prev_custom=prev_custom_hooks,
        result=result,
        is_dir_artifact=False,
        log_event="stale_hook_removal_failed",
        require_prefix=False,
    )

    # Write updated manifest
    _write_manifest(target_dir, result, data_dir)


# ---------------------------------------------------------------------------
# Stale artifact cleanup orchestrator
# ---------------------------------------------------------------------------


def _cleanup_stale_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None,
    dry_run: bool,
) -> None:
    """Remove stale and transient artifacts after a framework update.

    Runs three cleanup passes in order:

    1. PRD-FIX-032: Migrate non-prefixed predecessor skills/agents to their
       ``trw-`` successors (safe: only removes old name when new name exists).
    2. Remove stale bundled artifacts (hooks/skills/agents that were previously
       managed by TRW but are no longer in the current bundle).
    3. Remove transient files from ``.trw/context/`` (cache/session files that
       should not persist across updates).

    Args:
        target_dir: Root of the target git repository.
        result: Mutable result dict accumulating ``updated``, ``cleaned``,
            and ``errors`` entries.
        data_dir: Optional override for the bundled data directory; passed
            through to ``_remove_stale_artifacts``.
        dry_run: When ``True``, report what would change without deleting files.
    """
    # PRD-FIX-032: Remove non-prefixed predecessors before stale cleanup
    _migrate_prefix_predecessors(target_dir, result, dry_run=dry_run)

    # Remove stale hooks/skills/agents no longer in bundled data
    if not dry_run:
        _remove_stale_artifacts(target_dir, result, data_dir)

    # Clean transient artifacts from .trw/context/
    _cleanup_context_transients(target_dir, result, dry_run=dry_run)


# ---------------------------------------------------------------------------
# CLAUDE.md sync
# ---------------------------------------------------------------------------


def _run_claude_md_sync(
    target_dir: Path,
    result: dict[str, list[str]],
    timeout: int = 30,
) -> None:
    """Run CLAUDE.md sync after update to resolve placeholders and promote learnings.

    Temporarily changes cwd to the target project so that resolve_project_root()
    finds the correct .trw/ directory and learnings database.
    Fail-open: rendering errors are logged as warnings but never break the update.

    Stdout/stderr are suppressed during sync to prevent structlog noise and
    SDK error messages from leaking into the installer's progress pipe.

    A *timeout* (seconds, default 30) prevents the sync from blocking the
    installer indefinitely when LLM initialisation or network calls stall.
    """
    import concurrent.futures
    import io
    import os
    import sys

    original_cwd = Path.cwd()
    try:
        os.chdir(target_dir)

        from trw_mcp.models.config import _reset_config, get_config
        from trw_mcp.state.claude_md import execute_claude_md_sync
        from trw_mcp.state.llm_helpers import LLMClient
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        # Reset config so it picks up the target project's .trw/config.yaml
        _reset_config()
        config = get_config()
        reader = FileStateReader()
        writer = FileStateWriter()

        def _do_sync() -> dict[str, object]:
            # Suppress stdout/stderr so structlog noise and SDK auth errors
            # don't leak into the installer's subprocess pipe.
            saved_stdout, saved_stderr = sys.stdout, sys.stderr
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            try:
                llm = LLMClient()
                return execute_claude_md_sync(
                    scope="root",
                    target_dir=None,
                    config=config,
                    reader=reader,
                    writer=writer,
                    llm=llm,
                )
            finally:
                sys.stdout, sys.stderr = saved_stdout, saved_stderr

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_sync)
            sync_result = future.result(timeout=timeout)

        result["updated"].append(
            f"CLAUDE.md synced (learnings promoted: {sync_result.get('learnings_promoted', 0)})"
        )
    except concurrent.futures.TimeoutError:
        result["warnings"].append(
            f"CLAUDE.md sync timed out ({timeout}s) "
            "\u2014 will complete on next trw_session_start()"
        )
    except Exception as exc:  # justified: fail-open, CLAUDE.md sync is best-effort
        result["warnings"].append(f"CLAUDE.md sync skipped: {exc}")
    finally:
        os.chdir(original_cwd)
        # Reset config back to original project
        try:
            from trw_mcp.models.config import _reset_config
            _reset_config()
        except Exception:  # justified: cleanup, config reset is best-effort during finally
            logger.debug("config_reset_failed", exc_info=True)


# ---------------------------------------------------------------------------
# OpenCode update helper (FR15)
# ---------------------------------------------------------------------------


def _update_opencode_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
) -> None:
    """Update opencode artifacts when opencode is detected (FR15).

    Checks IDE targets and, when opencode is included, calls
    ``generate_opencode_config()`` to smart-merge ``opencode.json`` and
    ``generate_agents_md()`` to sync ``AGENTS.md``.

    Fail-open: errors are captured in ``result["warnings"]`` so they never
    break the overall update flow.
    """
    from ._opencode import generate_agents_md, generate_opencode_config

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "opencode" not in ide_targets:
        return

    # Update opencode.json (smart merge)
    try:
        oc_result = generate_opencode_config(target_dir)
        result["created"].extend(oc_result.get("created", []))
        result["updated"].extend(oc_result.get("updated", []))
        result["errors"].extend(oc_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, opencode update is best-effort
        result["warnings"].append(f"opencode.json update skipped: {exc}")
        return

    # Update AGENTS.md with platform-generic TRW section
    try:
        from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

        agents_section = render_agents_trw_section()
        agents_result = generate_agents_md(target_dir, agents_section)
        result["created"].extend(agents_result.get("created", []))
        result["updated"].extend(agents_result.get("updated", []))
        result["errors"].extend(agents_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, AGENTS.md update is best-effort
        result["warnings"].append(f"AGENTS.md update skipped: {exc}")


def _extract_trw_section_content() -> str:
    """Extract the content between trw:start and trw:end from _minimal_claude_md."""
    full = _minimal_claude_md()
    start_idx = full.find(_TRW_START_MARKER)
    end_idx = full.find(_TRW_END_MARKER)
    if start_idx != -1 and end_idx != -1:
        # Return content between the markers (exclusive)
        inner_start = start_idx + len(_TRW_START_MARKER)
        return full[inner_start:end_idx].strip()
    return ""


# ---------------------------------------------------------------------------
# Cursor update helper (FR05, FR06, FR07)
# ---------------------------------------------------------------------------


def _update_cursor_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
) -> None:
    """Update Cursor artifacts when Cursor is detected (FR05, FR06, FR07).

    Checks IDE targets and, when cursor is included, calls
    ``generate_cursor_hooks()`` (FR05), ``generate_cursor_rules()`` (FR06),
    and ``generate_cursor_mcp_config()`` (FR07) to smart-merge/update the
    respective ``.cursor/`` files.

    Fail-open: errors are captured in ``result["warnings"]`` so they never
    break the overall update flow.
    """
    from ._cursor import (
        generate_cursor_hooks,
        generate_cursor_mcp_config,
        generate_cursor_rules,
    )

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "cursor" not in ide_targets:
        return

    # FR05: Update .cursor/hooks.json (smart merge)
    try:
        hooks_result = generate_cursor_hooks(target_dir)
        result["created"].extend(hooks_result.get("created", []))
        result["updated"].extend(hooks_result.get("updated", []))
        result["errors"].extend(hooks_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, cursor update is best-effort
        result["warnings"].append(f".cursor/hooks.json update skipped: {exc}")

    # FR06: Update .cursor/rules/trw-ceremony.mdc
    try:
        trw_section = _extract_trw_section_content()
        rules_result = generate_cursor_rules(target_dir, trw_section)
        result["created"].extend(rules_result.get("created", []))
        result["updated"].extend(rules_result.get("updated", []))
        result["errors"].extend(rules_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, cursor rules update is best-effort
        result["warnings"].append(f".cursor/rules/trw-ceremony.mdc update skipped: {exc}")

    # FR07: Update .cursor/mcp.json (smart merge)
    try:
        mcp_result = generate_cursor_mcp_config(target_dir)
        result["created"].extend(mcp_result.get("created", []))
        result["updated"].extend(mcp_result.get("updated", []))
        result["errors"].extend(mcp_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, cursor mcp update is best-effort
        result["warnings"].append(f".cursor/mcp.json update skipped: {exc}")


# ---------------------------------------------------------------------------
# Config target_platforms update helper
# ---------------------------------------------------------------------------


def _update_config_target_platforms(
    target_dir: Path,
    ide_targets: list[str],
    result: dict[str, list[str]],
) -> None:
    """Update target_platforms in config.yaml to match detected/override IDE targets.

    Preserves all other config fields. Fail-open: errors go to result["warnings"].
    """
    import yaml

    config_path = target_dir / ".trw" / "config.yaml"
    if not config_path.exists():
        return

    try:
        content = config_path.read_text()
        data = yaml.safe_load(content) or {}
        current: list[str] = data.get("target_platforms", ["claude-code"])
        if sorted(current) == sorted(ide_targets):
            result["preserved"].append(str(config_path))
            return
        data["target_platforms"] = ide_targets
        config_path.write_text(
            yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
        )
        result["updated"].append(str(config_path))
    except Exception as exc:  # justified: fail-open, config update is best-effort
        result["warnings"].append(f"target_platforms config update skipped: {exc}")


# ---------------------------------------------------------------------------
# Main update_project entry point
# ---------------------------------------------------------------------------


def update_project(
    target_dir: Path,
    *,
    pip_install: bool = False,
    dry_run: bool = False,
    data_dir: Path | None = None,
    ide: str | None = None,
    on_progress: ProgressCallback = None,
) -> dict[str, list[str]]:
    """Update TRW framework files in *target_dir* while preserving user config.

    Always updates: hooks, skills, agents, FRAMEWORK.md, behavioral_protocol.yaml,
    claude_md template, settings.json.

    Never overwrites: config.yaml, learnings/.

    Smart merge: .mcp.json -- ensures ``trw`` server entry exists while preserving
    all other user-configured MCP servers.

    Smart update: CLAUDE.md -- replaces content between ``trw:start``/``trw:end``
    markers while preserving all user-written sections.

    Args:
        target_dir: Root of the target git repository.
        pip_install: If True, reinstall the trw-mcp package after file updates.
        dry_run: If True, report what would change without modifying files.
        data_dir: Optional override for the bundled data directory. When provided,
            artifact lookups use this path instead of the module-level ``_DATA_DIR``.
        ide: Target IDE override ("claude-code", "cursor", "opencode", "all").
            When None, auto-detect from existing IDE config directories.
        on_progress: Optional callback called as ``on_progress(action, path)``
            for each file processed. Enables real-time progress reporting.

    Returns:
        Dict with ``updated``, ``created``, ``preserved``, ``errors``,
        and ``warnings`` lists.
    """
    from . import _TRW_DIRS

    effective_data = data_dir or _DATA_DIR
    result: dict[str, list[str]] = {
        "updated": [],
        "created": [],
        "preserved": [],
        "errors": [],
        "warnings": [],
        "cleaned": [],
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
            _ensure_dir(target_dir / rel_dir, result, on_progress)

    # 2-6. Copy/update framework files, hooks, skills, agents
    if on_progress:
        on_progress("Phase", "Updating framework files...")
    _update_framework_files(target_dir, effective_data, result, dry_run, on_progress)

    # 7-8. Update .mcp.json and CLAUDE.md configuration
    if on_progress:
        on_progress("Phase", "Updating configuration files...")
    _update_mcp_config(target_dir, result, dry_run, on_progress)

    # 9a-9c. Remove stale and transient artifacts
    if on_progress:
        on_progress("Phase", "Cleaning stale artifacts...")
    _cleanup_stale_artifacts(target_dir, result, data_dir, dry_run)

    # 10. Check installed package version
    _check_package_version(result)

    # 11. Reinstall package if requested
    if pip_install and not dry_run:
        if on_progress:
            on_progress("Phase", "Reinstalling package...")
        _pip_install_package(target_dir, result)

    # 12. Write installer metadata + VERSION.yaml
    if not dry_run:
        if on_progress:
            on_progress("Phase", "Writing metadata...")
        _write_installer_metadata(target_dir, "update-project", result, on_progress)
        _write_version_yaml(target_dir, result, on_progress)

    # 12b. Update target_platforms in config.yaml based on detected IDEs
    if not dry_run:
        ide_targets = resolve_ide_targets(target_dir, ide_override=ide)
        _update_config_target_platforms(target_dir, ide_targets, result)

    # 13. Post-update verification
    if not dry_run:
        if on_progress:
            on_progress("Phase", "Verifying installation...")
        _verify_installation(target_dir, result)

    # 13b. Post-update CLAUDE.md sync (resolve placeholders, promote learnings)
    if not dry_run:
        if on_progress:
            on_progress("Phase", "Syncing CLAUDE.md...")
        _run_claude_md_sync(target_dir, result)

    # 13c. OpenCode updates (FR15: multi-IDE support)
    if not dry_run:
        if on_progress:
            on_progress("Phase", "Updating IDE configs...")
        _update_opencode_artifacts(target_dir, result, ide_override=ide)

    # 13d. Cursor updates (FR05, FR06, FR07: Cursor IDE support)
    if not dry_run:
        _update_cursor_artifacts(target_dir, result, ide_override=ide)

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
