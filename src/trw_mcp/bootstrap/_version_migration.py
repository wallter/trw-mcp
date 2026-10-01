# ruff: noqa: E402
"""Version migration — retired-artifact removal after an update.

Handles:
- ``trw-*`` skills and agents TRW no longer ships there (full bundle for ``.claude``; a client mirror
  follows that client's list; flag-gated skills never), each removal proof-gated
- Stale hooks and opencode commands, from the previous manifest's lists
- Context transient cleanup during update-project
"""

from __future__ import annotations

from pathlib import Path

import structlog

from ._utils import _result_action_key

logger = structlog.get_logger(__name__)

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
from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes
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
    clients: list[str] | None = None,
    tombstones: set[str] | None = None,
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

    PRD-INFRA-192 FR12: *clients* is this run's resolved client set (init: the
    targets init just recorded; update: ``update_write_targets`` evaluated by
    the caller, by which point ``target_platforms`` has already been rewritten
    for this run). ``None`` falls back to the recorded targets, for callers
    (tests, a manifest-repair path) that have no run-specific set of their own.
    """
    from ._manifest_recorders import collect_manifest_content_hashes, dropped_manifest_keys
    from ._template_updater import _get_bundled_names, _get_custom_names
    from ._version_manifest import MANIFEST_VERSION, _manifest_content_hashes, resolved_package_versions

    bundled = _get_bundled_names(data_dir)
    custom = _get_custom_names(target_dir, data_dir)
    prev_manifest_raw = _read_manifest(target_dir)
    prev_hashes = _manifest_content_hashes(prev_manifest_raw)
    content_hashes = collect_manifest_content_hashes(target_dir, prev_hashes, data_dir)
    result.setdefault("warnings", []).extend(dropped_manifest_keys(target_dir, prev_hashes, content_hashes))
    from ._client_ownership import owners_for_content_hashes, update_write_targets

    run_clients = clients if clients is not None else update_write_targets(target_dir, None)
    owners = owners_for_content_hashes(content_hashes, run_clients)
    # PRD-INFRA-192 FR10/FR12: *tombstones* is this run's resolved set (init/
    # update already ran detection + enforcement before calling here). ``None``
    # preserves whatever the prior manifest recorded, for callers (tests, a
    # manifest-repair path) with no run-specific set of their own.
    _prev_tombstones = (prev_manifest_raw or {}).get("tombstones")
    tombstones_value = sorted(tombstones) if tombstones is not None else _coerce_manifest_list(_prev_tombstones)
    manifest = {
        "version": MANIFEST_VERSION,
        "tombstones": tombstones_value,
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
        # PRD-INFRA-192 FR12: the client(s) whose catalog surfaces cover each
        # content_hashes key -- the ownership baseline a per-client uninstall
        # reads instead of re-deriving it (and risking drift) at removal time.
        "owners": owners,
        # PRD-INFRA-192 FR12: the ONE record of resolved package
        # versions this install/update wrote. version-status compares
        # importlib versions against this map rather than a VERSION.yaml stamp.
        "packages": resolved_package_versions(),
        "custom_skills": custom["skills"],
        "custom_agents": custom["agents"],
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


# Per-client stale cleanup + codex content hashes (FIX A/B) extracted to
# _version_migration_clients (350-eLOC gate). Re-exported for back-compat.
from trw_mcp.bootstrap._ownership_proof import preserve_unowned, remove_proven
from trw_mcp.bootstrap._version_migration_clients import (
    _codex_manifest_hashes as _codex_manifest_hashes,
)
from trw_mcp.bootstrap._version_migration_clients import (
    _remove_stale_client_artifacts as _remove_stale_client_artifacts,
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
    manifest_hashes: dict[str, str] | None,
    project_root: Path,
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
        manifest_hashes: Pre-run ``content_hashes``; an artifact is deleted only
            when they prove TRW wrote it (PRD-INFRA-190-FR06).
        project_root: Repository root the manifest keys are relative to.
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
        if preserve_unowned(stale, manifest_hashes, project_root, result):
            continue
        remove_proven(stale, manifest_hashes, project_root, result)


def _remove_stale_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None = None,
) -> None:
    """Remove ``.claude`` and ``.opencode`` artifacts TRW no longer ships, each only with proof TRW wrote it.

    Skills and agents are swept from disk (REMOVE-S8a): a ``trw-*`` skill dir or agent file whose name TRW no
    longer ships on that surface is retired, and goes only when the pre-run manifest's ``content_hashes``
    prove TRW wrote it; anything else is kept and reported ``not_installer_owned``. ``.claude`` judges
    against the full bundle; each client mirror against what that client ships. A flag-gated skill is never
    this sweep's (``retire_disabled_skills`` owns it). A future retirement therefore needs no
    edit beyond dropping the file from ``data/``. Hooks and opencode commands keep the previous-manifest
    diff (they carry no ``trw-`` namespace).

    On the first update after manifest support is added, no stale cleanup
    is performed (nothing is provably TRW's yet).

    The manifest itself is written once, by ``update_project``, after every
    writer has run.
    """
    from ._artifact_names import _opencode_skill_names
    from ._optional_skills import CONDITIONAL_SKILLS
    from ._template_updater import _get_bundled_names
    from ._utils import _DATA_DIR
    from ._version_migration_clients import ClientArtifactSurface, _remove_stale_client_surface

    prev_manifest = _read_manifest(target_dir)
    if prev_manifest is None:
        return
    bundled = _get_bundled_names(data_dir)
    hashes = _manifest_content_hashes(prev_manifest)

    def _manifest_set(key: str) -> set[str]:
        val = prev_manifest.get(key)
        return set(_coerce_manifest_list(val)) if val else set()

    # .claude/skills is judged against the FULL bundle (flag-gated included), as doctor is. A client mirror is
    # judged against what that client ships: a skill dropped from its curated list is stale FOR THAT CLIENT
    # (release-verify 2026-07-17). Flag-gated skills are never this sweep's (_remove_stale_client_surface).
    skills = set(bundled["skills"]) | set(CONDITIONAL_SKILLS)
    agents = set(bundled["agents"])
    opencode_agents = set(bundled.get("opencode_agents", []))
    # No opencode inventory means its curated list is unknown, not empty: sweep nothing there (fail safe). One read
    # decides both, so an inventory appearing mid-update never reads as an empty list (codex S8a r3).
    opencode_listed = _opencode_skill_names((data_dir or _DATA_DIR) / "opencode", (data_dir or _DATA_DIR) / "skills")
    opencode_skills = set(opencode_listed or ())
    surfaces = (
        # .claude/agents keys are bare ``<agent>.md``: the suffix rule is how its own record matches.
        ClientArtifactSurface(
            ".claude/skills", True, lambda: skills, "stale_skill_removal_failed", follows_canonical=False
        ),
        ClientArtifactSurface(".claude/agents", False, lambda: agents, "stale_agent_removal_failed", exact_proof=False),
        *(
            (
                ClientArtifactSurface(
                    ".opencode/skills", True, lambda: opencode_skills, "stale_opencode_skill_removal_failed"
                ),
            )
            if opencode_listed is not None
            else ()
        ),
        ClientArtifactSurface(
            ".opencode/agents", False, lambda: opencode_agents, "stale_opencode_agent_removal_failed"
        ),
    )
    for surface in surfaces:
        _remove_stale_client_surface(surface, target_dir, result, manifest_hashes=hashes, shipped_skills=skills)

    _remove_stale_set(
        stale_names=_manifest_set("hooks") - set(bundled["hooks"]),
        target_dir=target_dir / ".claude" / "hooks",
        prev_custom=_manifest_set("custom_hooks"),
        result=result,
        is_dir_artifact=False,
        log_event="stale_hook_removal_failed",
        valid_prefixes=None,
        manifest_hashes=hashes,
        project_root=target_dir,
    )
    _remove_stale_set(
        stale_names=_manifest_set("opencode_commands") - set(bundled.get("opencode_commands", [])),
        target_dir=target_dir / ".opencode" / "commands",
        prev_custom=_manifest_set("custom_opencode_commands"),
        result=result,
        is_dir_artifact=False,
        log_event="stale_opencode_command_removal_failed",
        manifest_hashes=hashes,
        project_root=target_dir,
    )


# ---------------------------------------------------------------------------
# Stale artifact cleanup orchestrator
# ---------------------------------------------------------------------------


def _cleanup_stale_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None,
    *,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Remove stale artifacts after a framework update.

    Runs two cleanup passes in order:

    1. ``.claude`` and ``.opencode``: ``trw-*`` skills and agents no longer in the bundle, plus stale hooks
       and opencode commands from the previous manifest's lists.
    2. Every client mirror surface, with the same ``trw-*`` predicate.

    Every deletion needs manifest proof of TRW authorship (PRD-INFRA-190-FR06).
    Context transients are not swept here: they are live session state outside
    the update transaction, cleaned by ``update_project`` after it commits.

    Args:
        target_dir: Root of the target git repository.
        result: Mutable result dict accumulating ``preserved`` and ``errors``.
        data_dir: Optional override for the bundled data directory; passed
            through to ``_remove_stale_artifacts``.
        manifest_hashes: The PRE-run manifest content hashes, threaded from
            ``update_project``: the proof of TRW authorship every sweep requires.
    """
    # Remove stale hooks/skills/agents no longer in bundled data.
    _remove_stale_artifacts(target_dir, result, data_dir)

    # FIX A: sweep codex/cursor/copilot mirror dirs for dropped bundled
    # artifacts (trw- prefixed names no longer in the current bundle).
    _remove_stale_client_artifacts(target_dir, result, manifest_hashes=manifest_hashes)
