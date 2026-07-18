"""update_project flow — selectively updates TRW framework files.

``trw-mcp update-project`` selectively updates framework files (hooks,
skills, agents, FRAMEWORK.md) while preserving user-customized files
(config.yaml, learnings, CLAUDE.md user sections).

This module is a thin orchestrator.  Implementation lives in:
- ``_template_updater`` — file copying, CLAUDE.md management, IDE configs
- ``_version_migration`` — predecessor cleanup, stale artifact removal, manifest I/O
"""
# ruff: noqa: I001 - backward-compat re-exports stay grouped for LOC ratchet.

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

from ._client_integrations import run_update_integrations

# ---------------------------------------------------------------------------
# Re-exports from sub-modules — REQUIRED for backward compatibility.
#
# Tests and external consumers patch ``trw_mcp.bootstrap._update_project.X``
# directly.  These re-exports ensure those patch paths continue to resolve
# to the canonical implementation in the sub-module.
# ---------------------------------------------------------------------------
# --- from _template_updater ---
from ._template_updater import (
    _ALWAYS_UPDATE as _ALWAYS_UPDATE,
    _extract_trw_section_content as _extract_trw_section_content,
    _get_bundled_names as _get_bundled_names,
    _get_custom_names as _get_custom_names,
    _minimal_claude_md_trw_block as _minimal_claude_md_trw_block,
    _NEVER_OVERWRITE as _NEVER_OVERWRITE,
    _report_preserved_files as _report_preserved_files,
    _run_claude_md_sync as _run_claude_md_sync,
    _TRW_END_MARKER as _TRW_END_MARKER,
    _TRW_HEADER_MARKER as _TRW_HEADER_MARKER,
    _TRW_START_MARKER as _TRW_START_MARKER,
)

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
        get_config()
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


from ._template_updater import (
    _update_agents as _update_agents,
    _update_always_overwrite_files as _update_always_overwrite_files,
    _update_antigravity_artifacts as _update_antigravity_artifacts,
    _update_claude_md_trw_section as _update_claude_md_trw_section,
    _update_codex_artifacts as _update_codex_artifacts,
    _update_config_target_platforms as _update_config_target_platforms,
    _update_copilot_artifacts as _update_copilot_artifacts,
    _update_cursor_artifacts as _update_cursor_artifacts,
    _update_framework_files as _update_framework_files,
    _update_hooks as _update_hooks,
    _update_mcp_config as _update_mcp_config,
    _update_opencode_artifacts as _update_opencode_artifacts,
    _update_or_report as _update_or_report,
    _update_skills as _update_skills,
)
from ._utils import (
    _DATA_DIR,
    ProgressCallback,
    _check_package_version,
    _ensure_dir,
    _pip_install_package,
    _verify_installation,
    _write_installer_metadata,
    _write_version_yaml,
    is_git_repo,
    resolve_ide_targets,
)

# --- from _version_migration ---
from ._version_migration import (
    _cleanup_context_transients as _cleanup_context_transients,
    _cleanup_stale_artifacts as _cleanup_stale_artifacts,
    _coerce_manifest_list as _coerce_manifest_list,
    _CONTEXT_ALLOWLIST as _CONTEXT_ALLOWLIST,
    _MANIFEST_FILE as _MANIFEST_FILE,
    _migrate_predecessor_set as _migrate_predecessor_set,
    _migrate_prefix_predecessors as _migrate_prefix_predecessors,
    PREDECESSOR_MAP as PREDECESSOR_MAP,
    _read_manifest as _read_manifest,
    _remove_stale_artifacts as _remove_stale_artifacts,
    _remove_stale_set as _remove_stale_set,
    _write_manifest as _write_manifest,
)
from ._version_manifest import _manifest_content_hashes as _manifest_content_hashes
from ._update_transaction import (
    _TRANSACTION_DIRS as _TRANSACTION_DIRS,
    _TRANSACTION_FILES as _TRANSACTION_FILES,
    _remove_transaction_path as _remove_transaction_path,
    _restore_transaction_snapshot as _restore_transaction_snapshot,
    _snapshot_transaction_paths as _snapshot_transaction_paths,
)

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
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Execute core update phases (framework files, config, cleanup).

    PRD-FIX-068-FR05: the *prior* install/update manifest's content hashes
    (*manifest_hashes*, read in :func:`update_project` BEFORE any files are
    rewritten) are threaded into ``_update_framework_files`` → ``_update_agents``
    so genuinely user-edited agents are detected on the live update path and
    preserved (reported in ``result['modified']``) instead of being silently
    overwritten. The NEW manifest is still written later in the post-update phase.
    """
    if not dry_run:
        from . import _TRW_DIRS

        for rel_dir in _TRW_DIRS:
            _ensure_dir(target_dir / rel_dir, result, on_progress)

    if on_progress:
        on_progress("Phase", "Updating framework files...")
    _update_framework_files(target_dir, effective_data, result, dry_run, on_progress, manifest_hashes)

    # PRD-CORE-093 FR03: Generate behavioral_protocol.md for session-start hook
    _generate_behavioral_protocol_md(target_dir, result, dry_run)

    if on_progress:
        on_progress("Phase", "Updating configuration files...")
    _update_mcp_config(target_dir, result, dry_run, on_progress)

    if on_progress:
        on_progress("Phase", "Cleaning stale artifacts...")
    _cleanup_stale_artifacts(
        target_dir,
        result,
        effective_data,
        dry_run,
        cleanup_context=dry_run,
    )

    _check_package_version(result)


def _run_post_update_phases(
    target_dir: Path,
    pip_install: bool,
    ide: str | None,
    result: dict[str, list[str]],
    on_progress: ProgressCallback,
    data_dir: Path | None = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Execute post-update phases (package install, verification, IDE configs)."""
    # PRD-SEC-005-FR05: migrate any tracked config.yaml key into the ignored
    # credentials.yaml (idempotent, fail-open) before other post-update work.
    from trw_mcp.models.config._credentials import migrate_for_update_project

    migrate_for_update_project(target_dir / ".trw" / "config.yaml", result)

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

    if on_progress:
        on_progress("Phase", "Updating IDE configs...")
    run_update_integrations(
        target_dir,
        ide_targets,
        ide_override=ide,
        result=result,
        manifest_hashes=manifest_hashes,
    )

    # Claude Code distill channels — always update (claude-code is the default client)
    if "claude-code" in ide_targets or not ide_targets:
        try:
            from ._claude_code_distill_channels import install_claude_code_distill_channels

            cc_dc = install_claude_code_distill_channels(target_dir)
            for _key in ("created", "updated", "preserved", "errors"):
                _items = cc_dc.get(_key)
                if isinstance(_items, list):
                    result.setdefault(_key, []).extend(_items)
        except Exception as exc:  # justified: fail-open, distill channels are additive
            result.setdefault("warnings", []).append(f"claude-code distill channels update skipped: {exc}")

    # PRD-CORE-149 FR04: rewrite .trw/runtime/hook-env.sh on every sync so
    # flag changes (hooks_enabled / nudge_enabled) propagate without re-init.
    _rewrite_hook_env_for_primary_profile(target_dir, ide_targets)

    _write_manifest(target_dir, result, data_dir)

    if on_progress:
        on_progress("Phase", "Verifying installation...")
    _verify_installation(target_dir, result)


def _rewrite_hook_env_for_primary_profile(target_dir: Path, ide_targets: list[str]) -> None:
    """PRD-CORE-149 FR04: refresh ``.trw/runtime/hook-env.sh`` on every sync.

    Fail-open: hook-env rewrite never aborts an update.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile

    from ._file_ops import _write_hook_env_file

    primary = ide_targets[0] if ide_targets else "claude-code"
    try:
        profile = resolve_client_profile(primary)
        _write_hook_env_file(target_dir / ".trw", profile)
    except Exception as exc:  # justified: fail-open
        logger.warning("hook_env_rewrite_failed", error=str(exc), primary=primary)


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

    # Symmetry with init_project: refuse to scaffold into a non-repo / wrong dir.
    # is_git_repo is symlink-safe (a plain .exists() follows symlinks).
    if not is_git_repo(target_dir):
        result["errors"].append(f"{target_dir} is not a git repository (.git/ not found)")
        logger.error(
            "project_update_failed",
            project_root=str(target_dir),
            error="not a git repository",
        )
        return result

    if not (target_dir / ".trw").exists():
        result["errors"].append(
            f"{target_dir} does not have TRW installed (.trw/ not found). Run `trw-mcp init-project` first."
        )
        return result

    manifest_hashes = _manifest_content_hashes(_read_manifest(target_dir))
    snapshot_root: Path | None = None
    if not dry_run:
        try:
            snapshot_root = _snapshot_transaction_paths(target_dir)
        except OSError as exc:
            result["errors"].append(f"Failed to snapshot update targets: {exc}")
            return result

    effective_data = data_dir or _DATA_DIR
    try:
        _run_core_update_phases(target_dir, effective_data, result, dry_run, on_progress, manifest_hashes)

        if not dry_run:
            _run_post_update_phases(target_dir, pip_install, ide, result, on_progress, effective_data, manifest_hashes)
    except Exception as exc:  # justified: fail-open — errors captured here, rolled back in finally
        logger.exception("update_project_exception", project_root=str(target_dir))
        result["errors"].append(f"update-project failed: {type(exc).__name__}: {exc}")
    finally:
        if snapshot_root is not None:
            if result["errors"]:
                try:
                    _restore_transaction_snapshot(target_dir, snapshot_root)
                    result["warnings"].append("update-project rolled back managed directories after write failure")
                except OSError as exc:
                    result["errors"].append(f"Failed to restore update snapshot: {exc}")
            shutil.rmtree(snapshot_root, ignore_errors=True)

    # Context files can be written by live sessions throughout an update and
    # are intentionally excluded from rollback snapshots. Clean transients
    # only after the managed-file transaction commits successfully.
    if not dry_run and not result["errors"]:
        _cleanup_context_transients(target_dir, result, dry_run=False)

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
