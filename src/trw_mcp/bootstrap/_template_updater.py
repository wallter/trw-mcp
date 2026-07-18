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

import json
import os
import shutil
import stat
from pathlib import Path

import structlog

from trw_mcp.canons.registry import install_view, load_registry

from ._file_ops import read_json_object
from ._gitignore_merge import _ensure_credentials_gitignored as _ensure_credentials_gitignored
from ._ide_targets import _extract_trw_section_content as _extract_trw_section_content
from ._ide_targets import _run_claude_md_sync as _run_claude_md_sync
from ._ide_targets import _update_antigravity_artifacts as _update_antigravity_artifacts
from ._ide_targets import _update_codex_artifacts as _update_codex_artifacts
from ._ide_targets import _update_config_target_platforms as _update_config_target_platforms
from ._ide_targets import _update_copilot_artifacts as _update_copilot_artifacts
from ._ide_targets import _update_cursor_artifacts as _update_cursor_artifacts
from ._ide_targets import _update_opencode_artifacts as _update_opencode_artifacts
from ._template_claude_md import (
    _TRW_END_MARKER,
    _TRW_HEADER_MARKER,
    _TRW_START_MARKER,
    _minimal_claude_md_trw_block,
    _update_claude_md_trw_section,
)
from ._utils import (
    ProgressCallback,
    _ensure_dir,
    _files_identical,
    _merge_mcp_json,
    _minimal_claude_md,
)
from ._version_manifest import _framework_content_hashes as _framework_content_hashes
from ._version_manifest import _is_user_modified as _is_user_modified

logger = structlog.get_logger(__name__)

# Files that are always overwritten during update (framework-managed).
_ALWAYS_UPDATE: list[tuple[str, str]] = [
    *install_view(load_registry()),
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

# Credentials-gitignore merge-ensure extracted to ``_gitignore_merge.py``
# (PRD-SEC-005-FR02, 350-eLOC gate). Re-exported here for back-compat with
# callers/tests that patch/import ``_template_updater._ensure_credentials_gitignored``.
# ``.trw/.gitignore`` is intentionally NOT in ``_ALWAYS_UPDATE`` — blind-overwriting
# it would silently discard a user's custom ignores — so the single credentials
# rule is merge-ensured instead.

# CLAUDE.md markers + helpers extracted to ``_template_claude_md.py``
# (PRD-DIST-243 Phase 1 batch 4, cycle 32). Re-imported above.

# Markers re-exported for back-compat with imports / tests.
__all__ = [
    "_TRW_END_MARKER",
    "_TRW_HEADER_MARKER",
    "_TRW_START_MARKER",
    "_minimal_claude_md_trw_block",
    "_update_claude_md_trw_section",
]


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
    canon_destinations = {
        destination for _, destination in install_view(load_registry()) if destination.startswith(".trw/frameworks/")
    }
    for data_name, dest_rel in _ALWAYS_UPDATE:
        # Atomically promoted by _write_version_yaml after all ordinary files.
        if dest_rel in canon_destinations:
            continue
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

    Robustness / leak discipline: both reads go through the structural-safe
    :func:`read_json_object` seam, so a non-UTF-8, malformed, or non-object
    ``settings.json`` on either side never raises (``UnicodeDecodeError`` is a
    ``ValueError``, not an ``OSError``, and was previously uncaught) and never
    leaks the file's bytes — diagnostics carry a reason *category* only.

      - Bundled template invalid / non-object → the user's existing settings are
        left untouched and a structural error is recorded. We never overwrite a
        good user file from a broken bundled source.
      - Existing settings unreadable / corrupt / non-object → fall back to the
        module's long-standing recovery behavior (copy the valid bundled
        template), with a content-free warning.
    """
    if not src.is_file():
        return
    if not dest.exists():
        # New install — copy bundled template directly
        _update_or_report(src, dest, result, dry_run)
        return

    bundled = read_json_object(src, context="settings_merge_bundled")
    if bundled is None:
        # Bundled source is itself invalid/non-object: refuse to clobber the
        # user's settings from a broken template. Structural reason only.
        result["errors"].append(f"Skipped settings.json merge: bundled template invalid or non-object: {dest}")
        return

    existing = read_json_object(dest, context="settings_merge_existing")
    if existing is None:
        # Unreadable / corrupt / non-object existing file: recover by copying the
        # (valid) bundled template, mirroring the prior fallback semantics.
        logger.warning("settings_json_merge_fallback", path=str(dest), reason="unreadable_or_non_object")
        _update_or_report(src, dest, result, dry_run)
        return

    # Merge env block: add missing keys, preserve existing values. Guard the
    # nested types so a hand-edited non-object ``env``/``hooks`` is preserved
    # rather than crashing the merge.
    bundled_env = bundled.get("env", {})
    existing_env = existing.get("env", {})
    if isinstance(bundled_env, dict) and isinstance(existing_env, dict):
        for key, value in bundled_env.items():
            existing_env.setdefault(key, value)
        existing["env"] = existing_env

    # Merge hooks: add missing hook event types, preserve existing
    bundled_hooks = bundled.get("hooks", {})
    existing_hooks = existing.get("hooks", {})
    if isinstance(bundled_hooks, dict) and isinstance(existing_hooks, dict):
        for hook_event, hook_list in bundled_hooks.items():
            existing_hooks.setdefault(hook_event, hook_list)
        existing["hooks"] = existing_hooks

    # No-op detection (aligns with _update_or_report's _files_identical): when
    # the merge output is byte-identical to what is already on disk there is
    # nothing to change — report ``preserved`` and skip, so both dry-run and
    # real runs stop claiming "would merge"/"updated" on an unchanged file.
    merged_text = json.dumps(existing, indent=2) + "\n"
    try:
        current_text: str | None = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        current_text = None
    if current_text == merged_text:
        result.setdefault("preserved", []).append(str(dest))
        return

    if dry_run:
        result["updated"].append(f"would merge: {dest}")
        return

    try:
        dest.write_text(merged_text, encoding="utf-8")
        result["updated"].append(str(dest))
    except OSError:
        # Structural reason only — never echo the raw exception text.
        result["errors"].append(f"Failed to write merged settings.json: {dest}")


def _guarded_copy_update(
    src: Path,
    dest: Path,
    manifest_key: str,
    result: dict[str, list[str]],
    dry_run: bool,
    manifest_hashes: dict[str, str] | None,
    *,
    make_executable: bool = False,
    on_progress: ProgressCallback = None,
) -> None:
    """Copy *src*→*dest* unless the user edited *dest* since last install.

    PRD-FIX-068-FR05: extends the modified-file guard (previously agents-only)
    to any raw-copy artifact (hooks, skills). When *dest*'s current hash differs
    from the manifest hash under *manifest_key*, the file is preserved and
    reported in ``result['modified']`` instead of being clobbered.

    Hooks/skills are not tier-resolved, but they DO need a framework-content
    baseline: unlike agents they carried no baseline before, so when
    ``managed-artifacts.yaml`` is missing/corrupt/pre-hash (``manifest_hashes``
    is ``None``) a genuinely user-edited hook/skill would have been silently
    overwritten. We derive the baseline directly from the bundled source *src*
    (its shipped SHA256) so :func:`_is_user_modified` can decide "matches shipped
    content → safe to update" vs "diverged → preserve" without a manifest — and
    when neither baseline is available AND the dest differs from the bundled
    content, it fails toward preservation.
    """
    framework_hashes = _framework_content_hashes(src)
    if _is_user_modified(dest, manifest_key, manifest_hashes, framework_hashes=framework_hashes):
        logger.info("artifact_user_modified", path=str(dest))
        result.setdefault("modified", []).append(str(dest))
        return
    _update_or_report(src, dest, result, dry_run, make_executable=make_executable, on_progress=on_progress)


def _update_hooks(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Update hook ``.sh`` files (overwritten unless user-modified, made executable)."""
    hooks_source = effective_data / "hooks"
    if hooks_source.is_dir():
        for hook_file in sorted(hooks_source.iterdir()):
            if hook_file.suffix == ".sh":
                dest = target_dir / ".claude" / "hooks" / hook_file.name
                _guarded_copy_update(
                    hook_file,
                    dest,
                    hook_file.name,
                    result,
                    dry_run,
                    manifest_hashes,
                    make_executable=True,
                    on_progress=on_progress,
                )


def _update_skills(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Update skill directories (overwritten unless user-modified)."""
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
                        # Manifest only hashes each skill's SKILL.md
                        # (``{name}/SKILL.md``); other files fall through to
                        # update since they carry no baseline hash.
                        _guarded_copy_update(
                            skill_file,
                            dest,
                            f"{skill_dir.name}/{skill_file.name}",
                            result,
                            dry_run,
                            manifest_hashes,
                            on_progress=on_progress,
                        )


def _update_agents(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Update agent ``.md`` files, resolving the capability-tier ``model:`` line.

    sub_5ctrrLJ / PRD-INFRA-104: update-project MUST materialize each agent
    through the SAME resolve-and-write path as fresh install
    (:func:`trw_mcp.bootstrap._version_manifest._apply_agent_update`, which calls
    ``_install_one_agent``) so the bundled ``model: frontier`` tier token is
    rewritten to the client's model (``model: opus`` for claude-code). A raw copy
    re-materialized unresolvable tier tokens and broke agent spawns after every
    upgrade.

    PRD-FIX-068-FR05: genuinely user-edited agents are still preserved + reported
    (``result['modified']``); an agent matching either framework rendering (raw
    tier OR resolved) is treated as unmodified so a mis-materialized agent heals.
    """
    from ._version_manifest import _apply_agent_update

    agents_source = effective_data / "agents"
    if not agents_source.is_dir():
        return
    dest_root = target_dir / ".claude" / "agents"
    for agent_file in sorted(agents_source.iterdir()):
        if agent_file.suffix == ".md":
            _apply_agent_update(agent_file, dest_root / agent_file.name, result, dry_run, on_progress, manifest_hashes)


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
    - Hook ``.sh`` files (overwritten unless user-modified, made executable).
    - Skill directories (overwritten unless user-modified per PRD-FIX-068-FR05).
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
    # PRD-SEC-005-FR02: merge-ensure the credentials.yaml ignore rule on every
    # existing install (gitignore.txt is only deployed on INIT, so update-project
    # would otherwise never refresh a custom .trw/.gitignore).
    _ensure_credentials_gitignored(target_dir, result, dry_run, on_progress)
    _update_hooks(target_dir, effective_data, result, dry_run, on_progress, manifest_hashes)
    _update_skills(target_dir, effective_data, result, dry_run, on_progress, manifest_hashes)
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


# ---------------------------------------------------------------------------
# Artifact name discovery — extracted to ``_artifact_names.py`` (350-eLOC gate).
# Re-exported here for back-compat with callers/tests importing via this facade.
# ---------------------------------------------------------------------------


from ._artifact_names import _get_bundled_names as _get_bundled_names
from ._artifact_names import _get_custom_names as _get_custom_names
