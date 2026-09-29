# ruff: noqa: E402
"""init_project flow — bootstraps TRW framework in a target directory.

PRD-INFRA-006: ``trw-mcp init-project`` CLI command that copies all
required framework files into a target git repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import structlog

from trw_mcp._checkout_write import write_checkout_file
from trw_mcp.bootstrap._client_integrations import run_install_integrations
from trw_mcp.state.claude_md._write_guard import with_instruction_write_trigger

from ._utils import (
    _DATA_DIR,
    ProgressCallback,
    _copy_file,
    _default_config,
    _merge_mcp_json,
    _minimal_review_md,
    _write_if_missing,
    _write_installer_metadata,
    _write_version_yaml,
    is_git_repo,
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
    _install_antigravity_artifacts as _install_antigravity_artifacts,
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
    _install_opencode_artifacts as _install_opencode_artifacts,
)
from trw_mcp.bootstrap._init_project_ide import (
    _run_copilot_installer as _run_copilot_installer,
)

# Client-aware scaffold sinks extracted to _init_project_scaffold (PRD-CORE-262
# FR05, 350-eLOC gate). Re-exported so existing importers keep resolving.
from trw_mcp.bootstrap._init_project_scaffold import (
    _copy_bundled_data_files as _copy_bundled_data_files,
)
from trw_mcp.bootstrap._init_project_scaffold import (
    _create_directory_structure as _create_directory_structure,
)


def _harden_trw_permissions(target_dir: Path) -> None:
    """Tighten the just-created ``.trw`` tree to 0700 (PRD-QUAL-110-FR02).

    Without this, ``init-project`` left ``.trw`` + its state subdirs
    (``learnings/``, ``logs/``, ``context/`` …) at the default umask
    (group/other-readable), contradicting the README security claim that
    "``.trw/`` dirs are 0700". Only ``.trw/memory`` was hardened previously
    (by the memory-backend path), so a fresh install leaked the learning
    corpus, logs, and context to group/other.

    Reuses the shared :func:`harden_trw_tree` helper (well-known state
    subdirs) and additionally hardens the init-created scaffold dirs that
    are not in the well-known list (``frameworks/``, ``templates/``,
    ``scripts/``, ``learnings/entries/``). Best-effort: chmod failures on
    non-POSIX platforms degrade to a WARNING and never abort init (NFR02).
    """
    from trw_mcp.state._paths_permissions import harden_dir_mode, harden_trw_tree

    trw_dir = target_dir / ".trw"
    if not trw_dir.exists():
        return
    # Harden root + well-known state subdirs (runs/, learnings/, logs/,
    # runtime/, memory/, context/, reflections/, knowledge/, security/).
    harden_trw_tree(trw_dir, create_subdirs=True)
    # init-project also scaffolds non-well-known dirs (frameworks/, templates/,
    # scripts/, learnings/entries/) and some are created lazily by sub-installers
    # AFTER the well-known set (e.g. channels/, telemetry/). Walk the whole tree
    # so EVERY directory under .trw is owner-only — the README claims "`.trw/`
    # dirs are 0700" with no exceptions. Best-effort per-dir (NFR02).
    for sub in trw_dir.rglob("*"):
        if sub.is_dir() and not sub.is_symlink():
            harden_dir_mode(sub, create=False)


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
        "pool_cooldowns": {},
        "tool_call_counter": 0,
        "last_nudge_pool": "",
        # PRD-FIX-076 sentinel — flipped to False on first session_start.
        "mcp_never_connected_yet": True,
    }
    write_checkout_file(target_dir, state_path, _json.dumps(skeleton, separators=(",", ":")))
    result["created"].append(str(state_path.relative_to(target_dir)))
    if on_progress is not None:
        on_progress("created", str(state_path))


def _write_initial_config(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    *,
    runs_root: str = ".trw/runs",
    target_platforms: list[str] | None = None,
    on_progress: ProgressCallback = None,
) -> None:
    """Write generated config.yaml and learnings index seed files."""
    _write_if_missing(
        target_dir / ".trw" / "config.yaml",
        _default_config(
            runs_root=runs_root,
            target_platforms=target_platforms,
        ),
        force,
        result,
        on_progress,
        root=target_dir,
    )
    index = target_dir / ".trw" / "learnings" / "index.yaml"
    _write_if_missing(index, "entries: []\n", force, result, on_progress, root=target_dir)


def _install_hooks(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
    *,
    clients: Sequence[str] = ("claude-code",),
    explicit: bool = False,
) -> None:
    """Copy bundled hook scripts to ``.claude/hooks/`` and install git hooks.

    ``.claude/hooks/`` is the Claude Code TOOL-LIFECYCLE surface — it has no
    ``post-commit`` event — so the git-hook family is installed separately into
    ``.git/hooks/`` (PRD-CORE-231 FR01/FR02).

    The ``.claude/hooks`` copy is gated on *clients* (PRD-CORE-262-FR05,
    generalized under PRD-INFRA-192 FR09): the directory is shared by
    claude-code, codex and copilot (their own hook commands run scripts from
    it too — see ``bootstrap/_codex_hooks.py`` / ``bootstrap/_copilot.py``),
    so any explicit selection containing one of those three keeps the copy;
    only an explicit selection with none of them (e.g. cursor-ide alone)
    drops it. CORE262-13: *explicit* distinguishes a user ``--ide`` choice
    from ``detect_ide`` resolving off an on-disk marker on a bare install,
    which must NOT drop it. The git-hook family and the intent-hook
    re-blessing stay unconditional — both are client-neutral.

    Only scripts a written configuration runs are copied
    (``_hook_closure.deployable_hook_files``); codex registers its five only
    when ``[features].hooks`` is on (PRD-CORE-301 FR07).
    """
    from ._client_ownership import writes_surface
    from ._hook_closure import init_hook_files

    hooks_source = _DATA_DIR / "hooks"
    if writes_surface(".claude/hooks", clients, explicit=explicit) and hooks_source.is_dir():
        names = init_hook_files(clients, hooks_source, target_dir, explicit=explicit)
        if names:
            (target_dir / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
        for name in names:
            _copy_file(
                hooks_source / name,
                target_dir / ".claude" / "hooks" / name,
                force,
                result,
                on_progress,
            )

    # A re-init over an ALREADY-enrolled project rewrites the same bundled hooks
    # the update path does, so it carries the same brick-the-project hazard (see
    # _template_updater._rebless_intent_hook_digest). No-op when no marker exists.
    from trw_mcp.bootstrap._template_updater import _rebless_intent_hook_digest

    _rebless_intent_hook_digest(target_dir, result)

    _install_git_hooks(target_dir, force, result, on_progress)


def _install_git_hooks(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Install the TRW ``post-commit`` git hook (PRD-CORE-231 FR01/FR02).

    Fail-open: a hook-install problem is recorded as a warning, never an abort —
    a repo without the hook simply keeps the pre-FR01 behavior.
    """
    try:
        from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook

        hook_result = install_git_post_commit_hook(target_dir, force=force)
        for path in hook_result["created"] + hook_result["updated"]:
            result["created"].append(path)
            if on_progress:
                on_progress("Created", path)
        result.setdefault("skipped", []).extend(hook_result["skipped"])
        result.setdefault("warnings", []).extend(hook_result["errors"])
    except Exception as exc:  # justified: fail-open, bootstrap must not abort
        logger.warning("git_hook_install_failed", error=str(exc))
        result.setdefault("warnings", []).append(f"git post-commit hook skipped: {exc}")


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


def _recordable_targets(target_dir: Path, ide_targets: list[str], *, explicit: bool) -> list[str]:
    """What ``target_platforms`` may record at install time.

    The record is consulted for the rest of the project's life as "what this
    project uses", and it is append-only, so anything written here is permanent.
    That makes it the wrong place for a guess.

    An explicit ``--ide`` is the user speaking: recorded verbatim. Without one,
    ``resolve_ide_targets`` falls through to ``detect_ide``, which fires on
    machine-global signals — ``shutil.which("cursor")`` and friends — so a bare
    ``init-project`` on a machine that merely HAS Cursor would permanently record
    cursor-ide for a project that never chose it. Detected clients are therefore
    kept only when the project carries evidence TRW could not have fabricated
    (``.opencode/``, ``.codex/`` and the like), from ``_CLIENT_EVIDENCE_MARKERS``.

    An empty result means nothing identified the project, which is the default
    scaffold — not "no clients".
    """
    if explicit:
        return ide_targets

    from ._template_claude_md import clients_with_markers_on_disk

    # At install nothing has been scaffolded yet, so a `.claude/` on disk is the
    # user's own; only the machine-global half of detection is dropped.
    on_disk = set(clients_with_markers_on_disk(target_dir))
    kept = [client for client in ide_targets if client in on_disk]
    # PRD-INFRA-192 FR09 §2: a bare init-project (explicit=False) still writes
    # every Claude Code surface unconditionally (CORE262-13), so it must
    # record claude-code too — `kept or ["claude-code"]` dropped claude-code
    # from the record whenever detection found ANY other on-disk client,
    # though the scaffold write happened regardless. Deduped, order-preserving.
    return list(dict.fromkeys([*kept, "claude-code"]))


def _record_installed_clients(target_dir: Path, ide_targets: list[str], result: dict[str, list[str]]) -> None:
    """Record each detected client whose artifacts this init wrote and TRW hashed.

    ``_recordable_targets`` deliberately keeps a machine-global guess out of the record; this is the
    other half, run AFTER the installers: a detected client that now has TRW-hashed artifacts on disk
    was installed, so it is recorded exactly as an explicit ``--ide`` is (append-only, same recorder).
    """
    from ._client_adoption import adopt_hash_proven_clients
    from ._manifest_recorders import collect_manifest_content_hashes
    from ._version_manifest import _manifest_content_hashes, _read_manifest

    prev_hashes = _manifest_content_hashes(_read_manifest(target_dir))
    hashes = collect_manifest_content_hashes(target_dir, prev_hashes)
    adopt_hash_proven_clients(target_dir, hashes, ide_targets, result)


def _generate_root_files(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    ide_targets: list[str] | None = None,
    on_progress: ProgressCallback = None,
    *,
    ide_explicit: bool = False,
) -> None:
    """Generate root-level configuration files (``.mcp.json``, ``AGENTS.md``, ``REVIEW.md``).

    *ide_explicit* says whether *ide_targets* came from a user ``--ide`` choice
    or from detection. It decides nothing else, but it decides this: a detected
    list must never be treated as authoritative, because ``detect_ide`` reports
    cursor-ide from ``shutil.which("cursor")``. Passing a detected list as if the
    user had chosen it made a plain ``init-project`` on any machine with Cursor
    installed withhold the claude-code protocol from every new project.
    """
    from ._client_ownership import writes_surface

    if writes_surface(".mcp.json", ide_targets or [], explicit=ide_explicit):
        _merge_mcp_json(target_dir, result, on_progress)
    # TRW 8.0: claude-code's carrier is AGENTS.md (Claude Code reads it
    # natively); TRW writes no CLAUDE.md. A detected list is never authoritative
    # here, because `detect_ide` reports cursor-ide from `shutil.which("cursor")`.
    from ._template_claude_md import claude_code_is_claimed, retire_claude_md, write_claude_code_agents_md

    errors_before = len(result.get("errors", []))
    if claude_code_is_claimed(target_dir, ide_targets if ide_explicit else None):
        write_claude_code_agents_md(target_dir, result)
    if len(result.get("errors", [])) == errors_before:  # never leave the protocol in neither file
        retire_claude_md(target_dir, result)
    _write_if_missing(target_dir / "REVIEW.md", _minimal_review_md(), force, result, on_progress, root=target_dir)


def _write_hook_env_for_installed_profiles(
    target_dir: Path, ide_targets: list[str], result: dict[str, list[str]] | None = None
) -> None:
    """PRD-CORE-149 FR04 / R8 sol round 1 P1: emit hook-env.d files for EVERY resolved client.

    ``run_install_integrations`` installs for every entry in ``ide_targets``,
    not just the first, so a multi-client init that wrote only
    ``ide_targets[0]``'s file left every other installed client's hooks either
    stale or (for a client sharing ``.claude/hooks``) silently governed by
    whichever profile happened to be first. ``result``, when given, collects
    the operator-facing warnings :func:`write_hook_env_for_clients` raises.
    """
    from ._file_ops import write_hook_env_for_clients

    write_hook_env_for_clients(
        target_dir / ".trw",
        ide_targets,
        warnings=result.setdefault("warnings", []) if result is not None else None,
    )


@with_instruction_write_trigger("bootstrap_init", "init-project")
def init_project(
    target_dir: Path,
    *,
    force: bool = False,
    runs_root: str = ".trw/runs",
    ide: str | None = None,
    on_progress: ProgressCallback = None,
) -> dict[str, list[str]]:
    """Bootstrap TRW framework in *target_dir*.

    Args:
        target_dir: Root of the target git repository.
        force: If ``True``, overwrite existing files.
        ide: Target IDE override ("claude-code", "cursor-ide", "cursor-cli", "opencode", "all").
            When None, auto-detect from existing IDE config directories.
        on_progress: Optional callback called as ``on_progress(action, path)``
            for each file processed. Enables real-time progress reporting.

    Returns:
        Dict with ``created``, ``skipped``, ``errors`` lists.
    """
    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    logger.info("project_init_started", project_root=str(target_dir), ide=ide)

    # PRD-INFRA-170-FR06 / OQ-1: a non-git target must NOT be left as the
    # reproduced config-present, framework-bodies-absent half-install. The
    # framework-body deploy (step 9, ``_write_version_yaml`` ->
    # ``repair_framework_runtime``) only writes files under ``.trw/frameworks/``
    # and is git-independent + idempotent, so we no longer bail when ``.git`` is
    # absent. Git is now an informational nicety, not a gate — we surface a loud,
    # non-silent warning and then run the full, usable install. Behavior for real
    # git repos is unchanged (no warning emitted, identical phases).
    #
    # is_git_repo is symlink-safe — a plain ``.exists()`` follows symlinks, so a
    # symlinked ``.git`` could otherwise fool this detection.
    if not is_git_repo(target_dir):
        warning = (
            f"{target_dir} is not a git repository — installing the framework anyway; "
            "run 'git init' to enable git-based features."
        )
        result.setdefault("warnings", []).append(warning)
        logger.warning("project_init_non_git", project_root=str(target_dir))

    from trw_mcp.agents._report_cap import project_report_cap
    from trw_mcp.state._project_root_binding import installing_into

    try:
        # PRD-CORE-290-FR04: the target's configured report cap; B71-117: the target is "the project".
        with project_report_cap(target_dir), installing_into(target_dir):
            _run_init_phases(
                target_dir,
                result,
                force=force,
                runs_root=runs_root,
                ide=ide,
                on_progress=on_progress,
            )
    except Exception as exc:  # justified: honor the dict-contract return, never raise a raw traceback
        logger.exception("project_init_exception", project_root=str(target_dir))
        result["errors"].append(f"init-project failed: {type(exc).__name__}: {exc}")

    if result["errors"]:
        logger.warning("project_init_partial", project_root=str(target_dir), errors=result["errors"][:3])
    logger.info(
        "project_init_ok",
        project_root=str(target_dir),
        dirs_created=len([p for p in result["created"] if p.endswith("/")]),
        files_created=len(result["created"]),
        skipped=len(result["skipped"]),
        errors=len(result["errors"]),
    )
    return result


def _run_init_phases(
    target_dir: Path,
    result: dict[str, list[str]],
    *,
    force: bool,
    runs_root: str,
    ide: str | None,
    on_progress: ProgressCallback,
) -> None:
    """Run the ordered init-project phases (steps 1-10).

    Extracted from :func:`init_project` so its body sits behind a single
    top-level exception boundary: any failure here is captured into
    ``result['errors']`` by the caller rather than escaping as a raw traceback
    that would violate the documented dict-contract return.
    """
    from ._skill_tombstone_prune import snapshot_skill_dir_siblings
    from ._tombstones import detect_tombstones, enforce_tombstones
    from ._update_project import _write_manifest
    from ._version_manifest import _read_manifest

    # PRD-INFRA-192 FR10: a path the user deleted from a PRIOR install stays
    # deleted on a re-run over the same project; computed before any writer
    # below runs, from the manifest as it stood at the start of this run.
    tombstones = detect_tombstones(target_dir, _read_manifest(target_dir))
    skill_dir_snapshot = snapshot_skill_dir_siblings(target_dir, tombstones)

    # Resolve IDE targets before creating any provider-specific directories.
    # Otherwise new scaffold directories can pollute auto-detection.
    ide_targets = resolve_ide_targets(target_dir, ide_override=ide)
    # CORE262-13: whether the caller EXPLICITLY chose these targets (a user
    # ``--ide``) versus ``detect_ide`` resolving them off on-disk markers. A
    # pre-existing ``.codex/`` makes a bare init resolve to ``["codex"]`` too,
    # and that auto-detected case must not take the codex-only suppression
    # path the FR05 scaffold gates apply to an EXPLICIT codex-only request.
    ide_explicit = ide is not None

    # 1. Create directory structure (client-owned dirs gated on ide_targets)
    _create_directory_structure(target_dir, result, on_progress, clients=ide_targets, explicit=ide_explicit)

    # 1b. PRD-FIX-076: Write ceremony-state.json skeleton with mcp_never_connected
    # sentinel so trw-eval can detect runs where MCP never connected.
    _write_ceremony_state_skeleton(target_dir, result, on_progress)

    # 2. Copy bundled data files
    _copy_bundled_data_files(target_dir, force, result, on_progress, clients=ide_targets, explicit=ide_explicit)

    # 3. Write generated config and seed files (includes target_platforms). A
    # checkout whose own store holds rows keeps its config byte-for-byte, even
    # under --force (PRD-CORE-280 FR06).
    # --force never re-keys an existing pin: a moved checkout's migrated rows live
    # under the pinned namespace, not the one its new location derives.
    from trw_mcp.state._store_migration import _set_pin

    from ._namespace_pin import pin_empty_checkout, store_holds_data, written_pin

    rewrite_config, kept_pin = force and not store_holds_data(target_dir), written_pin(target_dir)
    _write_initial_config(
        target_dir,
        rewrite_config,
        result,
        runs_root=runs_root,
        target_platforms=_recordable_targets(target_dir, ide_targets, explicit=ide_explicit),
        on_progress=on_progress,
    )
    if rewrite_config and kept_pin:
        _set_pin(target_dir / ".trw", kept_pin)
    # 3a. A new checkout has nothing to move: pin project_namespace and mint its grant (PRD-CORE-280 FR06)
    pin_empty_checkout(target_dir, result)

    # 4. Copy hook scripts
    _install_hooks(target_dir, force, result, on_progress, clients=ide_targets, explicit=ide_explicit)
    # 4a. The interpreter those hooks start (PRD-FIX-155); rewritten even without --force
    from ._update_external import write_hook_interpreter

    write_hook_interpreter(target_dir, result)

    # 5. Copy skills
    _install_skills(target_dir, force, result, on_progress, clients=ide_targets, explicit=ide_explicit)

    # 6. Materialize agents for every selected client (PRD-CORE-252-FR03).
    # ``ide_targets`` is the resolved selection and is never empty
    # (``resolve_ide_targets`` defaults to claude-code), so passing it is what
    # makes the client-parameterised installer reachable in production at all.
    _install_agents(target_dir, force, result, on_progress, clients=ide_targets)

    # 7. Generate root-level files (Claude Code: .mcp.json, AGENTS.md)
    _generate_root_files(target_dir, force, result, ide_targets, on_progress, ide_explicit=ide_explicit)

    # 7a. Claude Code distill channels (always installed — claude-code is the default)
    if "claude-code" in ide_targets or not ide_targets:
        try:
            from ._claude_code_distill_channels import install_claude_code_distill_channels

            cc_dc = install_claude_code_distill_channels(target_dir, force=force)
            result["created"].extend(cc_dc.get("created", []))
            result.setdefault("skipped", []).extend(cc_dc.get("preserved", []))
            result["errors"].extend(cc_dc.get("errors", []))
            for _key in ("removed", "warnings", "trashed"):  # CC-03 withdrawal outcomes
                result.setdefault(_key, []).extend(cc_dc.get(_key, []))
        except Exception as _exc:  # justified: fail-open, distill channels are additive
            result.setdefault("warnings", []).append(f"claude-code distill channels skipped: {_exc}")

        # 7a-1. Install .claude/loop.md — TRW-ceremony-aware /loop customization.
        # Uses _DATA_DIR / "claude_code" / "loop.md" as source so the file is
        # bundled with the package and distributed to all claude-code installs.
        _loop_src = _DATA_DIR / "claude_code" / "loop.md"
        _copy_file(
            _loop_src,
            target_dir / ".claude" / "loop.md",
            force,
            result,
            on_progress,
        )

    # 7b-7g. Registry-ordered client integrations (PRD-CORE-148).
    run_install_integrations(target_dir, ide_targets, force=force, result=result)

    # 7g. PRD-CORE-149 FR04: write every installed client's
    # .trw/runtime/hook-env.d/<key>.sh so hook scripts can honor per-profile
    # hooks_enabled / nudge_enabled without re-reading config on every fire.
    _write_hook_env_for_installed_profiles(target_dir, ide_targets, result)

    # 7h. Record what detection installed. The record is what update, the uninstall planner and the
    # manifest owners read; a client whose artifacts were written but not recorded is orphaned.
    if not ide_explicit and not result["errors"]:
        _record_installed_clients(target_dir, ide_targets, result)

    # 8. The manifest records a successful install (PRD-INFRA-192 FR12). An init
    # that reported errors writes none, so a later update refuses and names the remedy.
    if not result["errors"]:
        enforce_tombstones(target_dir, tombstones, result, skill_dir_snapshot)
    if not result["errors"]:
        _write_manifest(target_dir, result, tombstones=tombstones)

    # 9. Write installer metadata + VERSION.yaml
    _write_installer_metadata(target_dir, "init-project", result, on_progress)
    _write_version_yaml(target_dir, result, on_progress)

    # 10. Harden the .trw tree to 0700 (PRD-QUAL-110-FR02). Done LAST so every
    # directory created above (including those created lazily by file writes)
    # is tightened. Makes the README "`.trw/` dirs are 0700" claim true on the
    # install path, not just the run-scaffold path.
    _harden_trw_permissions(target_dir)
