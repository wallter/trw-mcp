"""Template updater — file copying, CLAUDE.md management, and IDE config updates.

Handles:
- Copying/updating framework-managed files (hooks, skills, agents, etc.)
- CLAUDE.md auto-generated section management (marker-based replacement)
- MCP config smart-merge
- Artifact name discovery (bundled vs. custom)
- CLAUDE.md sync (learnings promotion, placeholder resolution)
- IDE-specific artifact updates (opencode, cursor)
- Config target_platforms patching
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
    _ensure_dir,
    _files_identical,
    _merge_mcp_json,
    _minimal_claude_md,
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
    return {
        "skills": sorted(d.name for d in skills_source.iterdir() if d.is_dir()) if skills_source.is_dir() else [],
        "agents": sorted(f.name for f in agents_source.iterdir() if f.suffix == ".md")
        if agents_source.is_dir()
        else [],
        "hooks": sorted(f.name for f in hooks_source.iterdir() if f.suffix == ".sh") if hooks_source.is_dir() else [],
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

    return result


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

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(_do_sync)
            sync_result = future.result(timeout=timeout)
        finally:
            # shutdown(wait=False) so a hung worker thread (e.g. LLMClient
            # blocking on network) doesn't block the installer indefinitely.
            pool.shutdown(wait=False, cancel_futures=True)

        learnings_promoted = sync_result.get("learnings_promoted", 0)
        logger.info(
            "claude_md_sync_completed",
            learnings_promoted=learnings_promoted,
            target_dir=str(target_dir),
        )
        result["updated"].append(f"CLAUDE.md synced (learnings promoted: {learnings_promoted})")
    except concurrent.futures.TimeoutError:
        logger.warning(
            "claude_md_sync_timeout",
            timeout_seconds=timeout,
            target_dir=str(target_dir),
        )
        result["warnings"].append(
            f"CLAUDE.md sync timed out ({timeout}s) \u2014 will complete on next trw_session_start()"
        )
    except Exception as exc:  # justified: fail-open, CLAUDE.md sync is best-effort
        logger.warning(
            "claude_md_sync_failed",
            error=str(exc),
            target_dir=str(target_dir),
        )
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
        from trw_mcp.models.config import get_config
        from trw_mcp.state.claude_md._static_sections import (
            render_agents_trw_section,
            render_minimal_protocol,
        )

        _cfg = get_config()
        if _cfg.effective_ceremony_mode == "light":
            agents_section = render_minimal_protocol()
        else:
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
        content = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        current: list[str] = data.get("target_platforms", ["claude-code"])
        if sorted(current) == sorted(ide_targets):
            result["preserved"].append(str(config_path))
            return
        data["target_platforms"] = ide_targets
        config_path.write_text(
            yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        result["updated"].append(str(config_path))
    except Exception as exc:  # justified: fail-open, config update is best-effort
        result["warnings"].append(f"target_platforms config update skipped: {exc}")
