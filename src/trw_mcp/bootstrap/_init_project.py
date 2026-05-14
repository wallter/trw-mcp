# ruff: noqa: E402
"""init_project flow — bootstraps TRW framework in a target directory.

PRD-INFRA-006: ``trw-mcp init-project`` CLI command that copies all
required framework files into a target git repository.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from ._utils import (
    _DATA_DIR,
    ProgressCallback,
    _copy_file,
    _default_config,
    _ensure_dir,
    _merge_mcp_json,
    _minimal_claude_md,
    _minimal_review_md,
    _write_if_missing,
    _write_installer_metadata,
    _write_version_yaml,
    resolve_ide_targets,
)

logger = structlog.get_logger(__name__)



# IDE installers extracted to _init_project_ide (PRD-DIST-243 batch 21).
# Re-exported for back-compat with _client_integrations.py imports.
from trw_mcp.bootstrap._init_project_ide import (
    _CopilotInstaller as _CopilotInstaller,
)
from trw_mcp.bootstrap._init_project_ide import (
    _extend_result as _extend_result,
)
from trw_mcp.bootstrap._init_project_ide import (
    _install_codex_artifacts as _install_codex_artifacts,
)
from trw_mcp.bootstrap._init_project_ide import (
    _install_copilot_artifacts as _install_copilot_artifacts,
)
from trw_mcp.bootstrap._init_project_ide import (
    _install_cursor_artifacts as _install_cursor_artifacts,
)
from trw_mcp.bootstrap._init_project_ide import (
    _install_cursor_cli_artifacts as _install_cursor_cli_artifacts,
)
from trw_mcp.bootstrap._init_project_ide import (
    _install_gemini_artifacts as _install_gemini_artifacts,
)
from trw_mcp.bootstrap._init_project_ide import (
    _install_opencode_artifacts as _install_opencode_artifacts,
)
from trw_mcp.bootstrap._init_project_ide import (
    _load_model_family as _load_model_family,
)
from trw_mcp.bootstrap._init_project_ide import (
    _run_copilot_installer as _run_copilot_installer,
)


def _create_directory_structure(
    target_dir: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Create the TRW directory scaffold inside *target_dir*."""
    from . import _TRW_DIRS

    for rel_dir in _TRW_DIRS:
        _ensure_dir(target_dir / rel_dir, result, on_progress)


def _write_ceremony_state_skeleton(
    target_dir: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """PRD-FIX-076: Write a ceremony-state.json skeleton with a
    ``mcp_never_connected_yet=true`` sentinel.

    The sentinel distinguishes "MCP never connected at all" from "MCP connected
    but didn't complete ceremony" in trw-eval's ceremony-state fallback scorer.
    When the MCP server's session_start tool runs, it flips this flag to
    ``false`` (see ``mark_session_started``).

    Written idempotently — if the file already exists, we leave it alone so a
    re-run of init-project against an established project does not wipe real
    ceremony state.
    """
    import json as _json

    state_path = target_dir / ".trw" / "context" / "ceremony-state.json"
    if state_path.exists():
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    skeleton: dict[str, object] = {
        "session_started": False,
        "checkpoint_count": 0,
        "last_checkpoint_ts": None,
        "files_modified_since_checkpoint": 0,
        "build_check_result": None,
        "last_build_check_ts": None,
        "deliver_called": False,
        "learnings_this_session": 0,
        "nudge_counts": {},
        "phase": "early",
        "previous_phase": "",
        "review_called": False,
        "review_verdict": None,
        "review_p0_count": 0,
        "nudge_history": {},
        "pool_nudge_counts": {},
        "pool_ignore_counts": {},
        "pool_cooldown_until": {},
        "tool_call_counter": 0,
        "last_nudge_pool": "",
        # PRD-FIX-076 sentinel — flipped to False on first session_start.
        "mcp_never_connected_yet": True,
    }
    state_path.write_text(_json.dumps(skeleton, separators=(",", ":")), encoding="utf-8")
    result["created"].append(str(state_path.relative_to(target_dir)))
    if on_progress is not None:
        on_progress("created", str(state_path))


def _copy_bundled_data_files(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Copy all bundled data files from ``_DATA_FILE_MAP`` to *target_dir*."""
    from . import _DATA_FILE_MAP

    for data_name, dest_rel in _DATA_FILE_MAP:
        _copy_file(_DATA_DIR / data_name, target_dir / dest_rel, force, result, on_progress)


def _write_initial_config(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    *,
    source_package: str = "",
    test_path: str = "",
    runs_root: str = ".trw/runs",
    target_platforms: list[str] | None = None,
    on_progress: ProgressCallback = None,
) -> None:
    """Write generated config.yaml and learnings index seed files."""
    _write_if_missing(
        target_dir / ".trw" / "config.yaml",
        _default_config(
            source_package=source_package,
            test_path=test_path,
            runs_root=runs_root,
            target_platforms=target_platforms,
        ),
        force,
        result,
        on_progress,
    )
    _write_if_missing(
        target_dir / ".trw" / "learnings" / "index.yaml",
        "entries: []\n",
        force,
        result,
        on_progress,
    )


def _install_hooks(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Copy bundled hook scripts to ``.claude/hooks/``."""
    hooks_source = _DATA_DIR / "hooks"
    if hooks_source.is_dir():
        for hook_file in sorted(hooks_source.iterdir()):
            if hook_file.suffix == ".sh":
                _copy_file(
                    hook_file,
                    target_dir / ".claude" / "hooks" / hook_file.name,
                    force,
                    result,
                    on_progress,
                )



# Skill/agent installers extracted to _init_project_skills (PRD-DIST-243 batch 21b).
# Re-exported for back-compat — _copilot.py + _codex.py import _validate_skill;
# bootstrap/__init__.py exports all 3.
from trw_mcp.bootstrap._init_project_skills import (
    _install_agents as _install_agents,
)
from trw_mcp.bootstrap._init_project_skills import (
    _install_skills as _install_skills,
)
from trw_mcp.bootstrap._init_project_skills import (
    _validate_skill as _validate_skill,
)


def _generate_root_files(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Generate root-level configuration files (``.mcp.json``, ``CLAUDE.md``, ``REVIEW.md``)."""
    _merge_mcp_json(target_dir, result, on_progress)
    _write_if_missing(target_dir / "CLAUDE.md", _minimal_claude_md(), force, result, on_progress)
    _write_if_missing(target_dir / "REVIEW.md", _minimal_review_md(), force, result, on_progress)


def _write_hook_env_for_primary_profile(target_dir: Path, ide_targets: list[str]) -> None:
    """PRD-CORE-149 FR04: resolve the primary profile and emit hook-env.sh.

    Picks the first target from ``ide_targets`` as primary and falls back to
    ``claude-code`` when no targets resolved. Fail-open: any error is logged
    and swallowed so bootstrap never aborts because of hook-env propagation.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile

    from ._file_ops import _write_hook_env_file

    primary = ide_targets[0] if ide_targets else "claude-code"
    try:
        profile = resolve_client_profile(primary)
        trw_dir = target_dir / ".trw"
        _write_hook_env_file(trw_dir, profile)
    except Exception as exc:  # justified: fail-open, hook-env is best-effort
        logger.warning("hook_env_write_failed", error=str(exc), primary=primary)


def init_project(
    target_dir: Path,
    *,
    force: bool = False,
    source_package: str = "",
    test_path: str = "",
    runs_root: str = ".trw/runs",
    ide: str | None = None,
    on_progress: ProgressCallback = None,
) -> dict[str, list[str]]:
    """Bootstrap TRW framework in *target_dir*.

    Args:
        target_dir: Root of the target git repository.
        force: If ``True``, overwrite existing files.
        source_package: Pre-populate ``source_package_name`` in config.
        test_path: Pre-populate ``tests_relative_path`` in config.
        ide: Target IDE override ("claude-code", "cursor-ide", "cursor-cli", "opencode", "all").
            When None, auto-detect from existing IDE config directories.
        on_progress: Optional callback called as ``on_progress(action, path)``
            for each file processed. Enables real-time progress reporting.

    Returns:
        Dict with ``created``, ``skipped``, ``errors`` lists.
    """
    from ._update_project import _write_manifest

    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    logger.info("project_init_started", project_root=str(target_dir), ide=ide)

    # Validate target is a git repo
    if not (target_dir / ".git").exists():
        result["errors"].append(f"{target_dir} is not a git repository (.git/ not found)")
        logger.error(
            "project_init_failed",
            project_root=str(target_dir),
            error="not a git repository",
        )
        return result

    # Resolve IDE targets before creating any provider-specific directories.
    # Otherwise new scaffold directories can pollute auto-detection.
    ide_targets = resolve_ide_targets(target_dir, ide_override=ide)

    # 1. Create directory structure
    _create_directory_structure(target_dir, result, on_progress)

    # 1b. PRD-FIX-076: Write ceremony-state.json skeleton with mcp_never_connected
    # sentinel so trw-eval can detect runs where MCP never connected.
    _write_ceremony_state_skeleton(target_dir, result, on_progress)

    # 2. Copy bundled data files
    _copy_bundled_data_files(target_dir, force, result, on_progress)

    # 3. Write generated config and seed files (includes target_platforms)
    _write_initial_config(
        target_dir,
        force,
        result,
        source_package=source_package,
        test_path=test_path,
        runs_root=runs_root,
        target_platforms=ide_targets,
        on_progress=on_progress,
    )

    # 4. Copy hook scripts
    _install_hooks(target_dir, force, result, on_progress)

    # 5. Copy skills
    _install_skills(target_dir, force, result, on_progress)

    # 6. Copy agents
    _install_agents(target_dir, force, result, on_progress)

    # 7. Generate root-level files (Claude Code: .mcp.json, CLAUDE.md)
    _generate_root_files(target_dir, force, result, on_progress)

    # 7b. OpenCode artifacts (FR15: multi-IDE support)
    if "opencode" in ide_targets:
        _install_opencode_artifacts(target_dir, force=force, result=result)

    # 7c. Cursor artifacts (FR05, FR06, FR07: cursor-ide and cursor-cli support)
    if "cursor-ide" in ide_targets or "cursor-cli" in ide_targets:
        _install_cursor_artifacts(target_dir, force=force, result=result, ide_targets=ide_targets)

    # 7d. Codex artifacts
    if "codex" in ide_targets:
        _install_codex_artifacts(target_dir, force=force, result=result)

    # 7e. Copilot artifacts (PRD-CORE-127)
    if "copilot" in ide_targets:
        _install_copilot_artifacts(target_dir, force=force, result=result)

    # 7f. Gemini CLI artifacts
    if "gemini" in ide_targets:
        _install_gemini_artifacts(target_dir, force=force, result=result)

    # 7g. PRD-CORE-149 FR04: write .trw/runtime/hook-env.sh so hook scripts
    # can honor per-profile hooks_enabled / nudge_enabled without re-reading
    # config on every fire.
    _write_hook_env_for_primary_profile(target_dir, ide_targets)

    # 8. Write managed-artifacts manifest
    _write_manifest(target_dir, result)

    # 9. Write installer metadata + VERSION.yaml
    _write_installer_metadata(target_dir, "init-project", result, on_progress)
    _write_version_yaml(target_dir, result, on_progress)

    if result["errors"]:
        logger.warning(
            "project_init_partial",
            project_root=str(target_dir),
            errors=result["errors"][:3],
        )
    logger.info(
        "project_init_ok",
        project_root=str(target_dir),
        dirs_created=len([p for p in result["created"] if p.endswith("/")]),
        files_created=len(result["created"]),
        skipped=len(result["skipped"]),
        errors=len(result["errors"]),
    )
    return result
