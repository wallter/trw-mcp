"""update_project flow — selectively updates TRW framework files.

``trw-mcp update-project`` selectively updates framework files (hooks,
skills, agents, FRAMEWORK.md) while preserving user-customized files
(config.yaml, learnings, CLAUDE.md user sections).

This module is a thin orchestrator.  Implementation lives in:
- ``_template_updater`` — file copying, CLAUDE.md management, IDE configs
- ``_version_migration`` — predecessor cleanup, stale artifact removal, manifest I/O
"""

from __future__ import annotations

from pathlib import Path

import structlog

# ---------------------------------------------------------------------------
# Re-exports from sub-modules — REQUIRED for backward compatibility.
#
# Tests and external consumers patch ``trw_mcp.bootstrap._update_project.X``
# directly.  These re-exports ensure those patch paths continue to resolve
# to the canonical implementation in the sub-module.
# ---------------------------------------------------------------------------
# --- from _template_updater ---
from ._template_updater import _ALWAYS_UPDATE as _ALWAYS_UPDATE
from ._template_updater import _NEVER_OVERWRITE as _NEVER_OVERWRITE
from ._template_updater import _TRW_END_MARKER as _TRW_END_MARKER
from ._template_updater import _TRW_HEADER_MARKER as _TRW_HEADER_MARKER
from ._template_updater import _TRW_START_MARKER as _TRW_START_MARKER
from ._template_updater import (
    _extract_trw_section_content as _extract_trw_section_content,
)
from ._template_updater import _get_bundled_names as _get_bundled_names
from ._template_updater import _get_custom_names as _get_custom_names
from ._template_updater import (
    _minimal_claude_md_trw_block as _minimal_claude_md_trw_block,
)
from ._template_updater import _report_preserved_files as _report_preserved_files
from ._template_updater import _run_claude_md_sync as _run_claude_md_sync

_logger = structlog.get_logger(__name__)


def _run_auto_maintenance(
    target_dir: Path,
    result: dict[str, list[str]],
    timeout: int = 120,
    on_progress: ProgressCallback = None,
) -> None:
    """Run auto-maintenance (embeddings backfill, stale run close) after update.

    All operations are local (no API key required).  Fail-open — errors are
    logged as warnings but never break the update.
    """
    import concurrent.futures
    import os

    original_cwd = Path.cwd()
    try:
        os.chdir(target_dir)

        from trw_mcp.models.config import _reset_config, get_config
        from trw_mcp.state._memory_connection import (
            backfill_embeddings,
            check_embeddings_status,
        )

        _reset_config()
        config = get_config()
        trw_dir = target_dir / ".trw"

        # Check embeddings status and backfill if available
        emb_status = check_embeddings_status()
        if emb_status.get("enabled") and emb_status.get("available"):
            if on_progress:
                on_progress("Phase", "Backfilling embeddings (this may take 30-60s on first run)...")
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(backfill_embeddings, trw_dir)
                backfill = future.result(timeout=timeout)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            embedded = backfill.get("embedded", 0)
            if embedded > 0:
                result["updated"].append(f"Embeddings backfilled: {embedded} entries")
        elif emb_status.get("enabled") and not emb_status.get("available"):
            hint = emb_status.get("advisory", "pip install sentence-transformers")
            result["warnings"].append(f"Embeddings enabled but unavailable \u2014 {hint}")

        result["updated"].append("Auto-maintenance complete")
    except concurrent.futures.TimeoutError:
        result["warnings"].append(
            f"Embeddings backfill timed out ({timeout}s) \u2014 will complete on next trw_session_start()"
        )
    except Exception as exc:  # justified: boundary — auto-maintenance failure must not block update
        _logger.warning("auto_maintenance_failed", error=str(exc), target_dir=str(target_dir), exc_info=True)
        result["warnings"].append(f"Auto-maintenance skipped: {exc}")
    finally:
        os.chdir(original_cwd)
        try:
            from trw_mcp.models.config import _reset_config

            _reset_config()
        except Exception:  # justified: cleanup, config reset is best-effort during finally
            _logger.debug("auto_maintenance_config_reset_failed", exc_info=True)


from ._template_updater import _update_agents as _update_agents
from ._template_updater import (
    _update_always_overwrite_files as _update_always_overwrite_files,
)
from ._template_updater import (
    _update_claude_md_trw_section as _update_claude_md_trw_section,
)
from ._template_updater import (
    _update_codex_artifacts as _update_codex_artifacts,
)
from ._template_updater import (
    _update_config_target_platforms as _update_config_target_platforms,
)
from ._template_updater import (
    _update_copilot_artifacts as _update_copilot_artifacts,
)
from ._template_updater import _update_cursor_artifacts as _update_cursor_artifacts
from ._template_updater import _update_framework_files as _update_framework_files
from ._template_updater import (
    _update_gemini_artifacts as _update_gemini_artifacts,
)
from ._template_updater import _update_hooks as _update_hooks
from ._template_updater import _update_mcp_config as _update_mcp_config
from ._template_updater import (
    _update_opencode_artifacts as _update_opencode_artifacts,
)
from ._template_updater import _update_or_report as _update_or_report
from ._template_updater import _update_skills as _update_skills
from ._utils import (
    _DATA_DIR,
    ProgressCallback,
    _check_package_version,
    _ensure_dir,
    _pip_install_package,
    _verify_installation,
    _write_installer_metadata,
    _write_version_yaml,
    resolve_ide_targets,
)

# --- from _version_migration ---
from ._version_migration import _CONTEXT_ALLOWLIST as _CONTEXT_ALLOWLIST
from ._version_migration import _MANIFEST_FILE as _MANIFEST_FILE
from ._version_migration import PREDECESSOR_MAP as PREDECESSOR_MAP
from ._version_migration import (
    _cleanup_context_transients as _cleanup_context_transients,
)
from ._version_migration import (
    _cleanup_stale_artifacts as _cleanup_stale_artifacts,
)
from ._version_migration import _coerce_manifest_list as _coerce_manifest_list
from ._version_migration import (
    _migrate_predecessor_set as _migrate_predecessor_set,
)
from ._version_migration import (
    _migrate_prefix_predecessors as _migrate_prefix_predecessors,
)
from ._version_migration import _read_manifest as _read_manifest
from ._version_migration import (
    _remove_stale_artifacts as _remove_stale_artifacts,
)
from ._version_migration import _remove_stale_set as _remove_stale_set
from ._version_migration import _write_manifest as _write_manifest

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Main update_project entry point
# ---------------------------------------------------------------------------


def _init_result_dict(dry_run: bool) -> dict[str, list[str]]:
    """Initialize result dict with optional dry-run warning."""
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
    return result


def _generate_behavioral_protocol_md(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool,
) -> None:
    """Generate .trw/context/behavioral_protocol.md from static sections.

    PRD-CORE-093 FR03: The session-start hook reads this file once per
    session event instead of injecting the full protocol via CLAUDE.md
    on every message.
    """
    dest = target_dir / ".trw" / "context" / "behavioral_protocol.md"
    if dry_run:
        result["updated" if dest.exists() else "created"].append(
            f"would {'update' if dest.exists() else 'create'}: {dest}"
        )
        return
    try:
        from trw_mcp.state.claude_md._static_sections import (
            generate_behavioral_protocol_md,
        )

        content = generate_behavioral_protocol_md()
        dest.parent.mkdir(parents=True, exist_ok=True)
        existed = dest.exists()
        dest.write_text(content, encoding="utf-8")
        result["updated" if existed else "created"].append(str(dest))
    except Exception as exc:  # justified: fail-open — protocol file generation must not block update
        _logger.warning("behavioral_protocol_md_generation_failed", error=str(exc))
        result["warnings"].append(f"behavioral_protocol.md generation failed: {exc}")


def _run_core_update_phases(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback,
) -> None:
    """Execute core update phases (framework files, config, cleanup)."""
    if not dry_run:
        from . import _TRW_DIRS

        for rel_dir in _TRW_DIRS:
            _ensure_dir(target_dir / rel_dir, result, on_progress)

    if on_progress:
        on_progress("Phase", "Updating framework files...")
    _update_framework_files(target_dir, effective_data, result, dry_run, on_progress)

    # PRD-CORE-093 FR03: Generate behavioral_protocol.md for session-start hook
    _generate_behavioral_protocol_md(target_dir, result, dry_run)

    if on_progress:
        on_progress("Phase", "Updating configuration files...")
    _update_mcp_config(target_dir, result, dry_run, on_progress)

    if on_progress:
        on_progress("Phase", "Cleaning stale artifacts...")
    _cleanup_stale_artifacts(target_dir, result, effective_data, dry_run)

    _check_package_version(result)


def _run_post_update_phases(
    target_dir: Path,
    pip_install: bool,
    ide: str | None,
    result: dict[str, list[str]],
    on_progress: ProgressCallback,
    data_dir: Path | None = None,
    prev_manifest: dict[str, object] | None = None,
) -> None:
    """Execute post-update phases (package install, verification, IDE configs)."""
    if pip_install:
        if on_progress:
            on_progress("Phase", "Reinstalling package...")
        _pip_install_package(target_dir, result)

    if on_progress:
        on_progress("Phase", "Writing metadata...")
    _write_installer_metadata(target_dir, "update-project", result, on_progress)
    _write_version_yaml(target_dir, result, on_progress)

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide)
    _update_config_target_platforms(target_dir, ide_targets, result)

    if on_progress:
        on_progress("Phase", "Syncing CLAUDE.md...")
    _run_claude_md_sync(target_dir, result)

    if on_progress:
        on_progress("Phase", "Running auto-maintenance...")
    _run_auto_maintenance(target_dir, result, on_progress=on_progress)

    manifest_hashes = prev_manifest.get("content_hashes") if isinstance(prev_manifest, dict) else None
    if not isinstance(manifest_hashes, dict):
        manifest_hashes = None

    if on_progress:
        on_progress("Phase", "Updating IDE configs...")
    _update_opencode_artifacts(target_dir, result, ide_override=ide, manifest_hashes=manifest_hashes)
    _update_cursor_artifacts(target_dir, result, ide_override=ide)
    _update_codex_artifacts(target_dir, result, ide_override=ide, manifest_hashes=manifest_hashes)
    _update_copilot_artifacts(target_dir, result, ide_override=ide, manifest_hashes=manifest_hashes)
    _update_gemini_artifacts(target_dir, result, ide_override=ide, manifest_hashes=manifest_hashes)
    _write_manifest(target_dir, result, data_dir)

    if on_progress:
        on_progress("Phase", "Verifying installation...")
    _verify_installation(target_dir, result)


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
        ide: Target IDE override ("claude-code", "cursor-ide", "cursor-cli", "opencode", "all").
            When None, auto-detect from existing IDE config directories.
        on_progress: Optional callback called as ``on_progress(action, path)``
            for each file processed. Enables real-time progress reporting.

    Returns:
        Dict with ``updated``, ``created``, ``preserved``, ``errors``,
        and ``warnings`` lists.
    """
    result = _init_result_dict(dry_run)

    logger.info(
        "project_update_started",
        project_root=str(target_dir),
        dry_run=dry_run,
        pip_install=pip_install,
    )

    if not (target_dir / ".trw").exists():
        result["errors"].append(
            f"{target_dir} does not have TRW installed (.trw/ not found). Run `trw-mcp init-project` first."
        )
        return result

    prev_manifest = _read_manifest(target_dir)

    effective_data = data_dir or _DATA_DIR
    _run_core_update_phases(target_dir, effective_data, result, dry_run, on_progress)

    if not dry_run:
        _run_post_update_phases(target_dir, pip_install, ide, result, on_progress, effective_data, prev_manifest)

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
