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

import os
import shutil
from pathlib import Path

import structlog

from trw_mcp.agents._report_cap import project_report_cap

from trw_mcp.state.claude_md._write_guard import with_instruction_write_trigger

from ._client_integrations import run_update_integrations
from ._namespace_pin import pin_empty_checkout

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

# Effects outside the managed surface (PRD-INFRA-190 FR02: named, never diffed).
from ._update_external import _run_auto_maintenance as _run_auto_maintenance
from ._update_external import _update_git_hooks as _update_git_hooks


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
    resolve_client_write_targets,
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
from ._version_manifest import (
    _manifest_content_hashes as _manifest_content_hashes,
    manifest_refusal,
    preserve_uncommitted_changes,
)
from ._tombstones import enforce_and_write_manifest, prepare_update_manifest_state
from ._update_transaction import (
    _TRANSACTION_DIRS as _TRANSACTION_DIRS,
    _TRANSACTION_FILES as _TRANSACTION_FILES,
    _diff_transaction_paths,
    _remove_transaction_path as _remove_transaction_path,
    _restore_transaction_file,
    _restore_transaction_snapshot as _restore_transaction_snapshot,
    _snapshot_transaction_paths as _snapshot_transaction_paths,
    dirty_state,
    park_surface_links,
    run_in_scratch,
    unpark_surface_links,
)
from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH

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


#: Files that record THAT an update ran rather than what it installed. Written
#: only when the run changed something else, so a no-op update is a no-op
#: (PRD-INFRA-190 FR03).
_RUN_RECORDS: frozenset[str] = frozenset(
    {".trw/installer-meta.yaml", ".trw/frameworks/VERSION.yaml", str(DEPLOYMENT_RELATIVE_PATH)}
)


def _generate_behavioral_protocol_md(target_dir: Path, result: dict[str, list[str]]) -> None:
    """Generate .trw/context/behavioral_protocol.md from static sections.

    PRD-CORE-093 FR03: The session-start hook reads this file once per
    session event instead of injecting the full protocol via CLAUDE.md
    on every message.
    """
    dest = target_dir / ".trw" / "context" / "behavioral_protocol.md"
    try:
        from trw_mcp.state.claude_md._static_sections import generate_behavioral_protocol_md

        content = generate_behavioral_protocol_md()
        if dest.is_file() and dest.read_text(encoding="utf-8") == content:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    except Exception as exc:  # justified: fail-open — protocol file generation must not block update
        logger.warning("behavioral_protocol_md_generation_failed", error=str(exc))
        result["warnings"].append(f"behavioral_protocol.md generation failed: {exc}")


def _run_core_update_phases(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback,
    manifest_hashes: dict[str, str] | None = None,
    ide: str | None = None,
) -> None:
    """Execute core update phases (framework files, config, cleanup).

    PRD-FIX-068-FR05: the *prior* install/update manifest's content hashes
    (*manifest_hashes*, read in :func:`update_project` BEFORE any files are
    rewritten) are threaded into ``_update_framework_files`` → ``_update_agents``
    so genuinely user-edited agents are detected on the live update path and
    preserved (reported in ``result['modified']``) instead of being silently
    overwritten. The NEW manifest is written once, after every writer ran.

    *ide* (G1, installer refinement 5.1.0) is threaded the same way, into
    ``_update_framework_files`` → ``_update_agents`` → ``resolve_client_write_
    targets``, so a brand-new ``--ide <client>`` selection is visible to the
    agent-materialization phase in THIS run — it previously only registered
    the new client in ``target_platforms`` in the later post-update phase,
    so the first run wrote no agents for it and a second, identical run was
    required.
    """
    # PRD-INFRA-192 FR09 §3: the Claude Code scaffold dirs are ensured only
    # for a project whose recorded target_platforms (plus *ide*, if given)
    # actually own them -- an update on a ``[opencode]`` project must not
    # conjure ``.claude/skills``/``.claude/agents`` back into existence.
    from ._client_ownership import update_scaffold_dirs

    for rel_dir in update_scaffold_dirs(target_dir, ide):
        _ensure_dir(target_dir / rel_dir, result, on_progress)

    if on_progress:
        on_progress("Phase", "Updating framework files...")
    _update_framework_files(target_dir, effective_data, result, on_progress, manifest_hashes, ide=ide)

    # PRD-CORE-093 FR03: Generate behavioral_protocol.md for session-start hook
    _generate_behavioral_protocol_md(target_dir, result)

    if on_progress:
        on_progress("Phase", "Updating configuration files...")
    _update_mcp_config(target_dir, result, on_progress, ide=ide)

    if on_progress:
        on_progress("Phase", "Cleaning stale artifacts...")
    _cleanup_stale_artifacts(target_dir, result, effective_data, manifest_hashes=manifest_hashes)

    _check_package_version(result)


def _run_post_update_phases(
    target_dir: Path,
    ide: str | None,
    result: dict[str, list[str]],
    on_progress: ProgressCallback,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Execute post-update phases (metadata, instruction sync, client configs)."""
    # PRD-SEC-005-FR05: migrate any tracked config.yaml key into the ignored
    # credentials.yaml (idempotent, fail-open) before other post-update work.
    from trw_mcp.models.config._credentials import migrate_for_update_project

    migrate_for_update_project(target_dir / ".trw" / "config.yaml", result)

    if on_progress:
        on_progress("Phase", "Writing metadata...")
    _write_installer_metadata(target_dir, "update-project", result, on_progress)
    _write_version_yaml(target_dir, result, on_progress)

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide)
    # Do NOT feed raw detection into the append-only recorder when the project
    # already recorded its clients and the caller named none. `_update_config_
    # target_platforms` never narrows (PRD-FIX-076), and detection reports
    # claude-code for any project containing `.claude/` — which TRW itself
    # creates for EVERY client, since hooks and skills are universal artifacts.
    # So a bare `update-project` on a codex project appended claude-code
    # permanently, and the CLAUDE.md block came back on the next run. The
    # append-only rule exists to protect a USER's list, not to let our own
    # scaffolding vote itself into it.
    # ONE authority for both halves of the decision, and the SAME one the agent
    # update path consults. The record used to govern only what was RECORDED
    # while raw detection still governed what was WRITTEN, so a bare update on
    # a codex project scaffolded `.cursor/` off a binary on the developer's
    # PATH — and that directory then became the "evidence" the next bare update
    # adopted. Detection stays the answer only where there is no record to
    # honour (a pre-record install).
    write_targets = resolve_client_write_targets(target_dir, ide_override=ide)
    _update_config_target_platforms(target_dir, write_targets, result)

    if on_progress:
        on_progress("Phase", "Syncing CLAUDE.md...")
    # Pass the PRE-write baseline: by the time the sync runs, the on-disk
    # manifest has already been rewritten from current content, so a user's
    # hand-edited instruction file would look like TRW's own last write and be
    # silently overwritten.
    _run_claude_md_sync(target_dir, result, manifest_hashes=manifest_hashes)

    if on_progress:
        on_progress("Phase", "Updating IDE configs...")
    run_update_integrations(
        target_dir,
        write_targets,
        ide_override=ide,
        result=result,
        manifest_hashes=manifest_hashes,
    )

    # Claude Code distill channels — always update (claude-code is the default client)
    if "claude-code" in ide_targets or not ide_targets:
        try:
            from ._claude_code_distill_channels import install_claude_code_distill_channels

            cc_dc = install_claude_code_distill_channels(target_dir)
            for _key in ("preserved", "removed", "errors"):
                _items = cc_dc.get(_key)
                if isinstance(_items, list):
                    result.setdefault(_key, []).extend(_items)
        except Exception as exc:  # justified: fail-open, distill channels are additive
            result.setdefault("warnings", []).append(f"claude-code distill channels update skipped: {exc}")

    # PRD-CORE-149 FR04: rewrite .trw/runtime/hook-env.sh on every sync so
    # flag changes (hooks_enabled / nudge_enabled) propagate without re-init.
    _rewrite_hook_env_for_primary_profile(target_dir, ide_targets)


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


def _apply_update(
    root: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    *,
    ide: str | None,
    on_progress: ProgressCallback,
    dirty: set[str] | None,
    reprovision: list[str] | None,
) -> None:
    """Run every in-surface writer against *root* and report the surface diff.

    The same function serves both modes: a real run passes the target, a dry run
    passes a scratch copy (PRD-INFRA-190 FR02). ``updated``/``created``/``cleaned``
    come from the before/after diff, never from writer self-reports, so both
    modes answer the same question.
    """
    # PRD-INFRA-192 FR10: resolved against *root* before any writer runs (and
    # before the transaction even snapshots), so an unknown --reprovision path
    # errors out with literally nothing touched.
    if (prepared := prepare_update_manifest_state(root, reprovision, result)) is None:
        return
    manifest_hashes, tombstones, skill_dir_snapshot = prepared
    try:
        snapshot_root = _snapshot_transaction_paths(root)
    except OSError as exc:
        result["errors"].append(f"Failed to snapshot update targets: {exc}")
        return
    changes: dict[str, str] = {}
    # Every renderer that resolves "the project" (instruction sync, manifest
    # baselines, store counts) must resolve *root* — for a dry run the scratch
    # copy, never the caller's cwd or an inherited TRW_PROJECT_ROOT.
    inherited_root = os.environ.get("TRW_PROJECT_ROOT")
    os.environ["TRW_PROJECT_ROOT"] = str(root)
    # Set only when the whole writer phase finished: an interrupt (a BaseException
    # such as KeyboardInterrupt) bypasses the handler below and never records an
    # error, so the rollback below keys on this too — parked links come back on
    # EVERY exit, not only the normal one.
    completed = False
    try:
        parked = park_surface_links(root)
        _run_core_update_phases(root, effective_data, result, on_progress, manifest_hashes, ide=ide)
        _run_post_update_phases(root, ide, result, on_progress, manifest_hashes)
        unpark_surface_links(root, snapshot_root, parked, result)
        if dirty:
            preserve_uncommitted_changes(root, snapshot_root, dirty, manifest_hashes, result)
        from ._client_ownership import update_write_targets

        enforce_and_write_manifest(
            root, result, tombstones, effective_data, update_write_targets(root, ide), skill_dir_snapshot
        )
        if on_progress:
            on_progress("Phase", "Verifying installation...")
        _verify_installation(root, result)
        changes = _diff_transaction_paths(snapshot_root, root)
        if changes.keys() <= _RUN_RECORDS:
            for rel in changes:
                _restore_transaction_file(root, snapshot_root, rel)
            changes = {}
        completed = True
    except Exception as exc:  # justified: fail-open — errors captured here, rolled back in finally
        logger.exception("update_project_exception", project_root=str(root))
        result["errors"].append(f"update-project failed: {type(exc).__name__}: {exc}")
    finally:
        keep_snapshot = False
        if result["errors"] or not completed:
            changes = {}
            try:
                _restore_transaction_snapshot(root, snapshot_root)
                result["warnings"].append("update-project rolled back managed directories after write failure")
            except OSError as exc:
                # The snapshot is the only copy of what the rollback could not put
                # back (parked symlinks included) — keep it and say where it is.
                keep_snapshot = True
                logger.exception("update_snapshot_kept", snapshot=str(snapshot_root))
                result["errors"].append(
                    f"Failed to restore update snapshot: {exc}; recovery copy kept at {snapshot_root}"
                )
        if not keep_snapshot:
            shutil.rmtree(snapshot_root, ignore_errors=True)
        if inherited_root is None:
            os.environ.pop("TRW_PROJECT_ROOT", None)
        else:
            os.environ["TRW_PROJECT_ROOT"] = inherited_root
    for key, kind in (("updated", "updated"), ("created", "created"), ("cleaned", "deleted")):
        result[key] = [rel for rel, change in changes.items() if change == kind]


@with_instruction_write_trigger("bootstrap_update", "update-project")
def update_project(
    target_dir: Path,
    *,
    pip_install: bool = False,
    dry_run: bool = False,
    data_dir: Path | None = None,
    ide: str | None = None,
    on_progress: ProgressCallback = None,
    allow_dirty_bundle: bool = False,
    reprovision: list[str] | None = None,
) -> dict[str, list[str]]:
    """Update TRW framework files in *target_dir* while preserving user config.

    Always updates: hooks, skills, agents, FRAMEWORK.md, behavioral_protocol.yaml,
    claude_md template, settings.json.

    Never overwrites: config.yaml, learnings/, or a file git reports uncommitted
    whose bytes TRW did not record (PRD-INFRA-190 FR04).

    Smart merge: .mcp.json -- ensures ``trw`` server entry exists while preserving
    all other user-configured MCP servers.

    Smart update: CLAUDE.md -- replaces content between ``trw:start``/``trw:end``
    markers while preserving all user-written sections.

    Args:
        target_dir: Root of the target git repository.
        pip_install: If True, reinstall the trw-mcp package after file updates.
        dry_run: If True, run the update against a scratch copy of the managed
            files and report its diff; the target is not modified.
        data_dir: Optional override for the bundled data directory. When provided,
            artifact lookups use this path instead of the module-level ``_DATA_DIR``.
        ide: Target IDE override ("claude-code", "cursor-ide", "cursor-cli", "opencode", "all").
            When None, auto-detect from existing IDE config directories.
        on_progress: Optional callback called as ``on_progress(action, path)``
            for each file processed. Enables real-time progress reporting.
        allow_dirty_bundle: Project a bundled data directory that lies inside the
            target work tree even when git reports uncommitted changes in it (FR05).
        reprovision: Repo-relative paths (or ``["all"]``) whose tombstone this run
            should clear, so the writers recreate them (PRD-INFRA-192 FR10).

    Returns:
        Dict with ``updated``, ``created``, ``cleaned`` (repo-relative paths from
        the surface diff), ``preserved``, ``errors``, ``warnings``, and
        ``would_run``/``ran`` naming effects outside the managed surface.
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

    if refusal := manifest_refusal(target_dir):
        result["errors"].append(refusal)
        return result

    effective_data = data_dir or _DATA_DIR
    dirty, bundle_dirty = dirty_state(target_dir, effective_data, result)
    # TRW_ALLOW_DIRTY_BUNDLE=1 is the CLI's route to the flag until the
    # update-project argparse surface gains --allow-dirty-bundle.
    if bundle_dirty and not (allow_dirty_bundle or os.environ.get("TRW_ALLOW_DIRTY_BUNDLE") == "1"):
        result["errors"].append(
            "bundled data has uncommitted changes, refusing to project them: "
            + ", ".join(bundle_dirty)
            + " (commit or discard them, or set TRW_ALLOW_DIRTY_BUNDLE=1)"
        )
        return result

    external = ["pip_install"] if pip_install else []
    external += ["git_post_commit_hook", "memory_namespace_pin", "auto_maintenance", "context_transient_cleanup"]
    with project_report_cap(target_dir):  # PRD-CORE-290-FR04: the target's configured report cap
        if dry_run:
            run_in_scratch(
                target_dir,
                result,
                lambda scratch: _apply_update(
                    scratch, effective_data, result, ide=ide, on_progress=None, dirty=dirty, reprovision=reprovision
                ),
            )
            result["would_run"] = external
        else:
            _apply_update(
                target_dir,
                effective_data,
                result,
                ide=ide,
                on_progress=on_progress,
                dirty=dirty,
                reprovision=reprovision,
            )
            # Effects outside the managed surface run only once the transaction
            # committed; context files are live session state rollback never covers.
            if not result["errors"]:
                if pip_install:
                    if on_progress:
                        on_progress("Phase", "Reinstalling package...")
                    _pip_install_package(target_dir, result)
                _update_git_hooks(target_dir, result)
                pin_empty_checkout(target_dir, result)  # PRD-CORE-280 FR06: the real store, never the scratch copy
                if on_progress:
                    on_progress("Phase", "Running auto-maintenance...")
                _run_auto_maintenance(target_dir, result, on_progress=on_progress)
                context: dict[str, list[str]] = {"cleaned": [], "errors": [], "warnings": []}
                _cleanup_context_transients(target_dir, context)
                result.setdefault("info", []).extend(f"removed transient: {path}" for path in context["cleaned"])
                result["errors"].extend(context["errors"])
                result["warnings"].extend(context["warnings"])
                result["ran"] = external

    targets = resolve_ide_targets(target_dir, ide_override=ide)
    if "claude-code" in targets or not targets:
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
