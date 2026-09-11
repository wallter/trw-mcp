# ruff: noqa: E402
"""Version migration — predecessor cleanup and stale artifact removal.

Handles:
- PRD-FIX-032 predecessor skill/agent migration (non-prefixed -> trw- prefixed)
- Stale artifact removal based on manifest diffs
- Context transient cleanup during update-project
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

from ._utils import _result_action_key

logger = structlog.get_logger(__name__)

# PRD-FIX-032: Maps old non-prefixed skill/agent names to their trw- successors.
# Used by _migrate_prefix_predecessors() to remove stale predecessors during
# update-project when the trw- prefixed successor is already installed.
PREDECESSOR_MAP: dict[str, dict[str, str | None]] = {
    "skills": {
        # PRD-FIX-032: Non-prefixed → trw- prefixed migration
        "audit": "trw-audit",
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
        "review-pr": None,
        # Retired 2026-07-19 (operator: internal dev-only skill, must not ship in
        # the install). Archived at docs/archive/retired/2026-07-19-trw-release-verify/.
        "trw-release-verify": None,
        "security-check": "trw-security-check",
        # Retired 2026-07-19 (operator direction). The skill is archived at
        # docs/archive/retired/2026-07-19-trw-simplify/. Both the trw- name and
        # the legacy non-prefixed predecessor map DIRECTLY to None (retirement
        # chains must collapse — see test_retirement_chains_collapse_to_direct_
        # deletion) so update-project removes the materialized copy from any
        # existing install regardless of which name it carries.
        "simplify": None,
        "trw-simplify": None,
        "sprint-finish": "trw-sprint-finish",
        "sprint-init": "trw-sprint-init",
        "sprint-team": "trw-sprint-team",
        "team-playbook": "trw-team-playbook",
        "test-strategy": "trw-test-strategy",
        # PRD-CORE-092: Dropped skill post-consolidation
        "trw-review-pr": None,
    },
    "agents": {
        # PRD-FIX-032: Non-prefixed → trw- prefixed migration
        # Retired 2026-07-19 with the trw-simplify skill it ran (operator
        # direction). Archived at docs/archive/retired/2026-07-19-trw-simplify/.
        # Both names map directly to None (chain must collapse).
        "code-simplifier.md": None,
        "trw-code-simplifier.md": None,
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
        # Non-prefixed reviewers: local-only, never bundled
        "reviewer-correctness.md": None,
        "reviewer-integration.md": None,
        "reviewer-performance.md": None,
        "reviewer-security.md": None,
        "reviewer-spec-compliance.md": None,
        "reviewer-style.md": None,
        "reviewer-test-quality.md": None,
        # PRD-CORE-252: retired outright. Both names existed ONLY as per-client
        # stubs (a short original body sharing a filename with nothing in the
        # bundle) and neither was ever a bundled specialist. The stub sets are
        # gone, so the names go with them rather than being promoted into the
        # bundle — bundled-or-discard.
        "trw-explorer.md": None,
        "trw-docs-researcher.md": None,
    },
}

#: Per-client agent directories TRW no longer writes to, mapped to the exact
#: filenames it used to write there (PRD-CORE-252-FR04).
#:
#: Enumerated rather than swept, because these directories are not exclusively
#: TRW's — a blanket "remove every trw-* file here" could delete a live
#: artifact of another subsystem. ``trw-distill-explorer.md`` (the AG-02
#: dynamically-rendered explorer subagent) joined this list once its own
#: writer moved to the same ``.agents/agents`` destination the bundled
#: specialists use (PRD-CORE-252 follow-up); before that it was excluded here
#: for the reason this comment used to state. Antigravity's own subagent
#: reference documents ``.agents/agents``, which is where every antigravity
#: agent — bundled or dynamically rendered — now lands.
RELOCATED_CLIENT_AGENTS: dict[str, tuple[str, ...]] = {
    ".antigravitycli/agents": (
        "trw-explorer.md",
        "trw-implementer.md",
        "trw-lead.md",
        "trw-reviewer.md",
        "trw-distill-explorer.md",
    ),
}


# Context-cleanup policy extracted to _version_migration_context (PRD-FIX-120,
# 350-eLOC gate). Re-exported here so _update_project.py, bootstrap/__init__.py,
# and existing tests keep a single import point.
# Manifest read/hash helpers extracted to _version_manifest (PRD-DIST-243 batch 14).
# Re-exported here for backward compatibility with callers that import via
# this facade (_update_project.py, bootstrap/__init__.py, test modules).
from trw_mcp.bootstrap._version_manifest import (
    _MANIFEST_FILE as _MANIFEST_FILE,
)
from trw_mcp.bootstrap._version_manifest import (
    _coerce_manifest_list as _coerce_manifest_list,
)
from trw_mcp.bootstrap._version_manifest import (
    _compute_content_hashes as _compute_content_hashes,
)
from trw_mcp.bootstrap._version_manifest import (
    _read_manifest as _read_manifest,
)
from trw_mcp.bootstrap._version_migration_context import (
    _CONTEXT_ALLOWLIST as _CONTEXT_ALLOWLIST,
)
from trw_mcp.bootstrap._version_migration_context import (
    _SUPPORTS_PINNED_CONTEXT_CLEANUP as _SUPPORTS_PINNED_CONTEXT_CLEANUP,
)
from trw_mcp.bootstrap._version_migration_context import (
    _TRANSIENT_PATTERNS as _TRANSIENT_PATTERNS,
)
from trw_mcp.bootstrap._version_migration_context import (
    _cleanup_context_transients as _cleanup_context_transients,
)
from trw_mcp.bootstrap._version_migration_context import (
    _is_transient_context_artifact as _is_transient_context_artifact,
)


def _write_manifest(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None = None,
) -> None:
    """Write the managed-artifacts manifest to the target project.

    The manifest records which skills, agents, and hooks were installed
    by TRW so that ``_remove_stale_artifacts`` can distinguish
    TRW-managed artifacts from user-created custom ones.

    PRD-FIX-068-FR04: Manifest version 2 includes SHA256 content hashes.

    PRD-FIX-121-FR01/FR05: ``content_hashes`` is built by the declared recorder
    registry in ``_manifest_recorders.py`` and by nothing else. Every recorder is
    handed the PRE-run manifest and declines to record an artifact the user has
    edited, so a preserved edit is never laundered into TRW's ownership baseline
    and overwritten on the following update. Inlining a fourth key producer here
    is what the FR05 totality test exists to catch — add it to the registry.
    """
    from ._manifest_recorders import collect_manifest_content_hashes
    from ._template_updater import _get_bundled_names, _get_custom_names
    from ._version_manifest import _manifest_content_hashes

    bundled = _get_bundled_names(data_dir)
    custom = _get_custom_names(target_dir, data_dir)
    # PRD-FIX-032-FR05: Exclude predecessor names from custom lists so they
    # are not permanently protected as false-custom entries.
    predecessor_skills = set(PREDECESSOR_MAP["skills"].keys())
    predecessor_agents = set(PREDECESSOR_MAP["agents"].keys())
    prev_hashes = _manifest_content_hashes(_read_manifest(target_dir))
    content_hashes = collect_manifest_content_hashes(target_dir, prev_hashes, data_dir)
    manifest = {
        "version": 2,
        "skills": bundled["skills"],
        "agents": bundled["agents"],
        "hooks": bundled["hooks"],
        "opencode_commands": [
            n for n in bundled.get("opencode_commands", []) if (target_dir / ".opencode" / "commands" / n).is_file()
        ],
        "opencode_agents": [
            n for n in bundled.get("opencode_agents", []) if (target_dir / ".opencode" / "agents" / n).is_file()
        ],
        "opencode_skills": [
            n for n in bundled.get("opencode_skills", []) if (target_dir / ".opencode" / "skills" / n).is_dir()
        ],
        "content_hashes": content_hashes,
        "custom_skills": [s for s in custom["skills"] if s not in predecessor_skills],
        "custom_agents": [a for a in custom["agents"] if a not in predecessor_agents],
        "custom_hooks": custom["hooks"],
        "custom_opencode_commands": custom.get("opencode_commands", []),
        "custom_opencode_agents": custom.get("opencode_agents", []),
        "custom_opencode_skills": custom.get("opencode_skills", []),
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


# _MANIFEST_FILE moved to _version_manifest (re-exported above)


# ---------------------------------------------------------------------------
# Context cleanup
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Predecessor migration
# ---------------------------------------------------------------------------


# Per-client stale cleanup + codex content hashes (FIX A/B) extracted to
# _version_migration_clients (350-eLOC gate). Re-exported for back-compat.
from trw_mcp.bootstrap._version_migration_clients import (
    _codex_manifest_hashes as _codex_manifest_hashes,
)
from trw_mcp.bootstrap._version_migration_clients import (
    _remove_stale_client_artifacts as _remove_stale_client_artifacts,
)

# Predecessor-migration helpers extracted to _version_migration_predecessors
# (PRD-DIST-243 batch 17). Re-exported here for back-compat with callers
# (bootstrap/__init__.py + test_bootstrap_branches_migration_cleanup.py).
from trw_mcp.bootstrap._version_migration_predecessors import (
    _migrate_predecessor_set as _migrate_predecessor_set,
)
from trw_mcp.bootstrap._version_migration_predecessors import (
    _migrate_prefix_predecessors as _migrate_prefix_predecessors,
)

# ---------------------------------------------------------------------------
# Stale artifact removal
# ---------------------------------------------------------------------------


def _remove_stale_set(
    stale_names: set[str],
    target_dir: Path,
    prev_custom: set[str],
    result: dict[str, list[str]],
    *,
    is_dir_artifact: bool,
    log_event: str,
    valid_prefixes: tuple[str, ...] | None = ("trw-",),
    dry_run: bool = False,
) -> None:
    """Remove a set of stale artifacts from *target_dir*.

    Skips names that are in *prev_custom* (user-created).  When
    *valid_prefixes* is not ``None``, also skips names that do not start
    with at least one of the specified prefixes (defense-in-depth for
    skills and agents).  Pass ``None`` to disable prefix filtering.

    Args:
        stale_names: Artifact names to consider for removal.
        target_dir: Directory containing the artifacts.
        prev_custom: Names from the previous manifest's custom list.
        result: Mutable result dict.
        is_dir_artifact: ``True`` to use ``shutil.rmtree``, ``False`` to use ``unlink``.
        log_event: structlog event name on removal failure.
        valid_prefixes: Tuple of allowed prefixes for stale removal.
            ``None`` disables prefix filtering entirely.
        dry_run: When ``True``, report ``would remove:<path>`` without deleting.
    """
    if not target_dir.is_dir():
        return
    for name in stale_names:
        if name in prev_custom:
            continue
        if valid_prefixes is not None and not name.startswith(valid_prefixes):
            continue
        stale = target_dir / name
        exists = stale.is_dir() if is_dir_artifact else stale.is_file()
        if not exists:
            continue
        if dry_run:
            result["updated"].append(f"would remove:{stale}")
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
    dry_run: bool = False,
) -> None:
    """Remove hooks/skills/agents that no longer exist in bundled data.

    Uses a manifest file (``.trw/managed-artifacts.yaml``) to track which
    artifacts were previously installed by TRW.  Only artifacts listed in
    the manifest are candidates for removal -- custom user-created
    artifacts are never touched.

    On the first update after manifest support is added, no stale cleanup
    is performed (the manifest is written for future updates).

    When *dry_run* is ``True`` the pending removals are reported
    (``would remove:<path>``) and NO files are deleted or written — including
    the manifest, which must stay untouched in preview mode.
    """
    from ._template_updater import _get_bundled_names

    prev_manifest = _read_manifest(target_dir)
    bundled = _get_bundled_names(data_dir)
    bundled_skills = set(bundled["skills"])
    bundled_agents = set(bundled["agents"])
    bundled_hooks = set(bundled["hooks"])
    bundled_opencode_commands = set(bundled.get("opencode_commands", []))
    bundled_opencode_agents = set(bundled.get("opencode_agents", []))
    bundled_opencode_skills = set(bundled.get("opencode_skills", []))

    if prev_manifest is None:
        # First run with manifest support -- write manifest, skip cleanup.
        # In dry-run mode leave the manifest unwritten (report nothing to remove).
        if not dry_run:
            _write_manifest(target_dir, result, data_dir)
        return

    def _manifest_set(key: str) -> set[str]:
        val = prev_manifest.get(key)
        return set(_coerce_manifest_list(val)) if val else set()

    prev_skills = _manifest_set("skills")
    prev_agents = _manifest_set("agents")
    prev_hooks = _manifest_set("hooks")
    prev_custom_skills = _manifest_set("custom_skills")
    prev_custom_agents = _manifest_set("custom_agents")
    prev_custom_hooks = _manifest_set("custom_hooks")
    prev_opencode_commands = _manifest_set("opencode_commands")
    prev_opencode_agents = _manifest_set("opencode_agents")
    prev_opencode_skills = _manifest_set("opencode_skills")
    prev_custom_opencode_commands = _manifest_set("custom_opencode_commands")
    prev_custom_opencode_agents = _manifest_set("custom_opencode_agents")
    prev_custom_opencode_skills = _manifest_set("custom_opencode_skills")

    # Remove stale artifacts per category
    # Defense-in-depth: only remove trw-prefixed items to protect custom artifacts
    _remove_stale_set(
        stale_names=prev_skills - bundled_skills,
        target_dir=target_dir / ".claude" / "skills",
        prev_custom=prev_custom_skills,
        result=result,
        is_dir_artifact=True,
        log_event="stale_skill_removal_failed",
        dry_run=dry_run,
    )
    _remove_stale_set(
        stale_names=prev_agents - bundled_agents,
        target_dir=target_dir / ".claude" / "agents",
        prev_custom=prev_custom_agents,
        result=result,
        is_dir_artifact=False,
        log_event="stale_agent_removal_failed",
        valid_prefixes=("trw-", "reviewer-"),
        dry_run=dry_run,
    )
    _remove_stale_set(
        stale_names=prev_hooks - bundled_hooks,
        target_dir=target_dir / ".claude" / "hooks",
        prev_custom=prev_custom_hooks,
        result=result,
        is_dir_artifact=False,
        log_event="stale_hook_removal_failed",
        valid_prefixes=None,
        dry_run=dry_run,
    )
    _remove_stale_set(
        stale_names=prev_opencode_commands - bundled_opencode_commands,
        target_dir=target_dir / ".opencode" / "commands",
        prev_custom=prev_custom_opencode_commands,
        result=result,
        is_dir_artifact=False,
        log_event="stale_opencode_command_removal_failed",
        dry_run=dry_run,
    )
    _remove_stale_set(
        stale_names=prev_opencode_agents - bundled_opencode_agents,
        target_dir=target_dir / ".opencode" / "agents",
        prev_custom=prev_custom_opencode_agents,
        result=result,
        is_dir_artifact=False,
        log_event="stale_opencode_agent_removal_failed",
        dry_run=dry_run,
    )
    _remove_stale_set(
        stale_names=prev_opencode_skills - bundled_opencode_skills,
        target_dir=target_dir / ".opencode" / "skills",
        prev_custom=prev_custom_opencode_skills,
        result=result,
        is_dir_artifact=True,
        log_event="stale_opencode_skill_removal_failed",
        dry_run=dry_run,
    )

    # Write updated manifest (never on a dry-run preview).
    if not dry_run:
        _write_manifest(target_dir, result, data_dir)


# ---------------------------------------------------------------------------
# Stale artifact cleanup orchestrator
# ---------------------------------------------------------------------------


def _cleanup_stale_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None,
    dry_run: bool,
    *,
    cleanup_context: bool = True,
    manifest_hashes: dict[str, str] | None = None,
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
        manifest_hashes: The PRE-run manifest content hashes, threaded from
            ``update_project``. Pass 2's file-granular sweep uses them as proof
            of TRW authorship before deleting anything inside a kept skill dir.
    """
    # PRD-FIX-032: Remove non-prefixed predecessors before stale cleanup
    _migrate_prefix_predecessors(target_dir, result, dry_run=dry_run)

    # Remove stale hooks/skills/agents no longer in bundled data. dry_run is
    # threaded down so --dry-run REPORTS the pending removals ("would remove:
    # <path>") instead of silently skipping the whole pass — otherwise the
    # preview under-reports what a real run would delete.
    _remove_stale_artifacts(target_dir, result, data_dir, dry_run=dry_run)

    # FIX A: sweep codex/cursor/copilot mirror dirs for dropped bundled
    # artifacts (trw- prefixed names no longer in the current bundle). Runs the
    # same dry_run-aware "would remove:" reporting as the .claude/.opencode pass.
    _remove_stale_client_artifacts(target_dir, result, dry_run=dry_run, manifest_hashes=manifest_hashes)

    # Context cleanup can run post-transaction so a failed update never
    # deletes live session state that rollback deliberately does not snapshot.
    if cleanup_context:
        _cleanup_context_transients(target_dir, result, dry_run=dry_run)
