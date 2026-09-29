"""Template updater — file copying, instruction-file management, artifact discovery.

Handles:
- Copying/updating framework-managed files (hooks, skills, agents, etc.)
- claude-code AGENTS.md block + legacy CLAUDE.md retirement
- MCP config smart-merge
- Artifact name discovery (bundled vs. custom)

IDE-specific logic (opencode, cursor, config target_platforms, instruction sync)
lives in ``_ide_targets.py`` and is re-exported here for backward compatibility.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import structlog

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file
from trw_mcp.canons.registry import install_view, load_registry

from ._gitignore_merge import _ensure_credentials_gitignored as _ensure_credentials_gitignored
from ._ide_targets import _extract_trw_section_content as _extract_trw_section_content
from ._ide_targets import _run_claude_md_sync as _run_claude_md_sync
from ._ide_targets import _update_antigravity_artifacts as _update_antigravity_artifacts
from ._ide_targets import _update_codex_artifacts as _update_codex_artifacts
from ._ide_targets import _update_config_target_platforms as _update_config_target_platforms
from ._ide_targets import _update_copilot_artifacts as _update_copilot_artifacts
from ._ide_targets import _update_cursor_artifacts as _update_cursor_artifacts
from ._ide_targets import _update_opencode_artifacts as _update_opencode_artifacts
from ._safe_remove import path_refusal, remove_if_hash
from ._settings_merge import _merge_settings_json as _merge_settings_json
from ._template_claude_md import (
    _TRW_END_MARKER,
    _TRW_HEADER_MARKER,
    _TRW_START_MARKER,
)
from ._utils import (
    ProgressCallback,
    _ensure_dir,
    _files_identical,
    _merge_mcp_json,
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

# Instruction-file markers live in ``_template_claude_md.py``. Re-imported above.

# ``settings.json`` smart-merge extracted to ``_settings_merge.py`` (350-eLOC
# gate). Re-exported here for back-compat with callers/tests that import
# ``_template_updater._merge_settings_json``.

# Markers re-exported for back-compat with imports / tests.
__all__ = [
    "_TRW_END_MARKER",
    "_TRW_HEADER_MARKER",
    "_TRW_START_MARKER",
]


# ---------------------------------------------------------------------------
# Update helpers
# ---------------------------------------------------------------------------


def _update_or_report(
    src: Path,
    dest: Path,
    result: dict[str, list[str]],
    *,
    make_executable: bool = False,
    on_progress: ProgressCallback = None,
) -> None:
    """Copy *src* to *dest* unless *dest* already holds the same bytes and mode.

    Write-if-different (PRD-INFRA-190 FR03): an identical destination is not
    touched, so a no-op update changes nothing — not even the exec bit.

    Args:
        src: Source file to copy from.
        dest: Destination path to copy to.
        result: Mutable result dict (errors only; changes are reported from the diff).
        make_executable: When ``True``, set executable bits on *dest*.
        on_progress: Optional callback for real-time progress reporting.
    """
    executable = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH if make_executable else 0
    try:
        if dest.is_file() and _files_identical(src, dest):
            mode = os.stat(dest).st_mode
            if mode & executable != executable:
                os.chmod(dest, mode | executable)
            return
        existed = dest.exists()
        write_checkout_file(
            dest.parent, dest, src.read_bytes()
        )  # the destination's own directory is the root: a symlinked file is refused, not written through
        if executable:
            os.chmod(dest, os.stat(dest).st_mode | executable)
        if on_progress:
            on_progress("Updated" if existed else "Created", str(dest))
    except (OSError, UnsafeWriteError) as exc:
        result["errors"].append(f"Failed to copy {src} -> {dest}: {exc}")
        if on_progress:
            on_progress("Error", str(dest))


def _update_always_overwrite_files(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
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
        _update_or_report(src, dest, result, on_progress=on_progress)


def _report_preserved_files(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Report create-only files in ``_NEVER_OVERWRITE`` that already exist."""
    for rel_path in _NEVER_OVERWRITE:
        dest = target_dir / rel_path
        if dest.exists():
            result["preserved"].append(str(dest))


def _guarded_copy_update(
    src: Path,
    dest: Path,
    manifest_key: str,
    result: dict[str, list[str]],
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
    _update_or_report(src, dest, result, make_executable=make_executable, on_progress=on_progress)


def _update_hooks(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
    manifest_hashes: dict[str, str] | None = None,
    ide: str | None = None,
) -> None:
    """Update hook ``.sh`` files (overwritten unless user-modified, made executable).

    PRD-INFRA-192 FR09 §3: ``.claude/hooks`` is refreshed only when some
    recorded/``--ide`` client for *target_dir* still owns it (claude-code,
    codex, or copilot) — a project recorded as ``[opencode]`` must not have
    a leftover ``.claude/hooks`` tree from an older install byte-refreshed.

    PRD-INFRA-192 FR10: the deployed set is the registered-hook-plus-helper
    closure for the *resolved* client set, not every bundled ``.sh`` file — a
    codex-only or copilot-only project no longer receives claude-code-only
    hooks (e.g. the intent-guard pair) it never registers.
    """
    from ._client_ownership import update_owns_surface, update_write_targets
    from ._hook_closure import deployable_hook_files

    if not update_owns_surface(".claude/hooks", target_dir, ide):
        return
    # A symlinked .claude or .claude/hooks would carry every read, copy and unlink below out of the project.
    refusal = path_refusal(target_dir / ".claude" / "hooks", target_dir)
    if refusal:
        result.setdefault("warnings", []).append(f".claude/hooks: left untouched ({refusal})")
        return
    hooks_source = effective_data / "hooks"
    clients = update_write_targets(target_dir, ide)
    shipped = deployable_hook_files(clients, hooks_source, target_dir) if hooks_source.is_dir() else set()
    _withdraw_retired_hooks(target_dir, shipped, manifest_hashes, result)
    if shipped:
        (target_dir / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    for name in sorted(shipped):
        dest = target_dir / ".claude" / "hooks" / name
        _guarded_copy_update(
            hooks_source / name,
            dest,
            name,
            result,
            manifest_hashes,
            make_executable=True,
            on_progress=on_progress,
        )
    _rebless_intent_hook_digest(target_dir, result)


def _withdraw_retired_hooks(
    target_dir: Path, shipped: set[str], manifest_hashes: dict[str, str] | None, result: dict[str, list[str]]
) -> None:
    """Remove a hook TRW recorded installing and no longer ships, so an update leaves no orphan.

    Only an unedited copy goes: its bytes must equal what the manifest recorded TRW
    writing. An edited copy is kept with a warning. The CC-03 pair is owned by its
    channel installer, which ships or withdraws it by config, so it is skipped here.
    """
    from ._claude_code_distill_channels import _CC03_HOOKS

    for name, recorded in sorted((manifest_hashes or {}).items()):
        dest = target_dir / ".claude" / "hooks" / name
        if "/" in name or not name.endswith(".sh") or name in shipped or name in _CC03_HOOKS:
            continue
        rel = f".claude/hooks/{name}"
        # Rechecked per file, before the read and the unlink: the manifest key is data, not a trusted path.
        refusal = path_refusal(dest, target_dir)
        if refusal:
            result.setdefault("warnings", []).append(f"{rel}: left untouched ({refusal})")
            continue
        if not dest.is_file():
            continue
        # The pre-check keeps an edited hook out of trash; remove_if_hash re-verifies after capture and
        # never unlinks, so an edit or open-fd write racing this step keeps its bytes (HB-2).
        try:
            unedited = hashlib.sha256(dest.read_bytes()).hexdigest() == recorded
        except OSError as exc:  # unreadable: keep it and say so, never abort the update
            result.setdefault("warnings", []).append(f"{rel}: left untouched (could not read it: {exc})")
            continue
        if unedited:
            outcome = remove_if_hash(dest, target_dir, recorded, key=rel)
            where = outcome.retained_at or ".trw/trash (exact folder unknown)"
            if outcome.status == "removed":
                result.setdefault("removed", []).append(rel)
                result.setdefault("trashed", []).append(rel)
            elif outcome.status == "retained":
                result.setdefault("warnings", []).append(
                    f"{rel}: kept your version in {where} ({outcome.reason}); nothing was overwritten"
                )
            elif outcome.status == "kept" and outcome.published is None and outcome.retained_at is not None:
                result.setdefault("warnings", []).append(f"{rel}: kept ({outcome.reason}); a copy is in {where}")
            elif outcome.status == "kept":
                result.setdefault("warnings", []).append(f"{rel}: kept ({outcome.reason})")
        else:
            result.setdefault("warnings", []).append(f"{rel}: no longer shipped by TRW; kept because it was edited")


def _rebless_intent_hook_digest(target_dir: Path, result: dict[str, list[str]]) -> None:
    """Re-bless the PRD-SEC-013 enrollment marker's HOOK half after a vendor resync.

    Without this, shipping a new bundled hook bricks every enrolled project:
    ``expected_hook_digest`` covers the intent hooks and the shared lib they
    source, so the marker reads ``stale``, both control points fail closed, and
    every Edit/Write is blocked though the user did nothing. The installer is the
    only component that can tell "the vendor just wrote these exact bytes" from
    "someone tampered with them", so the re-bless belongs here and nowhere else.

    Only the hook half moves. ``refresh_hook_digest`` leaves the contract digest
    alone and never mints a marker, so an unenrolled project stays inert and a
    contract edit still fails closed until an operator re-enrolls.

    Fail-open: a re-bless problem is a warning, never an aborted update.
    """
    try:
        from trw_mcp.security.intent_contract.enrollment import refresh_hook_digest

        if refresh_hook_digest(target_dir):
            logger.info("intent_enrollment_hook_digest_refreshed", path=str(target_dir))
    except Exception as exc:  # justified: fail-open, an update must never abort here
        logger.warning("intent_enrollment_refresh_failed", error=str(exc))
        result.setdefault("warnings", []).append(f"intent-contract enrollment hook digest not refreshed: {exc}")


def _update_skills(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
    manifest_hashes: dict[str, str] | None = None,
    ide: str | None = None,
) -> None:
    """Update skill directories (overwritten unless user-modified).

    PRD-INFRA-192 FR09 §3: ``.claude/skills`` is claude-code's own surface —
    refreshed only when claude-code is among the recorded/``--ide`` clients.
    """
    from ._client_ownership import update_owns_surface

    if not update_owns_surface(".claude/skills", target_dir, ide):
        return
    from ._optional_skills import retire_disabled_skills, skill_enabled

    skills_source = effective_data / "skills"
    if skills_source.is_dir():
        # Same order as init's _install_skills: retire disabled optional skills, then refresh only enabled ones,
        # so an update neither leaves a disabled skill live nor re-deploys it.
        retire_disabled_skills(
            target_dir / ".claude" / "skills", skills_source, result, ".claude/skills", project_root=target_dir
        )
        for skill_dir in sorted(skills_source.iterdir()):
            if skill_dir.is_dir() and skill_enabled(skill_dir.name, target_dir):
                dest_skill = target_dir / ".claude" / "skills" / skill_dir.name
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
                            manifest_hashes,
                            on_progress=on_progress,
                        )


def _update_agents(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
    manifest_hashes: dict[str, str] | None = None,
    ide: str | None = None,
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

    PRD-CORE-252-FR03: the update runs once per selected client, into that
    client's own destination from the FR01 registry, with that client's own
    materialization. Install and update therefore produce identical bytes for
    the same client and bundle — the property whose absence made the earlier
    install-versus-update asymmetry regress on every upgrade. A client with no
    agent surface is recorded once and creates no directory.

    *ide* (G1, installer refinement 5.1.0): forwarded to
    ``resolve_client_write_targets`` as ``ide_override``. Without it, a brand
    new ``--ide <client>`` run only sees clients already RECORDED in
    ``.trw/config.yaml`` — target_platforms registration
    (``_update_config_target_platforms``) runs later in the same invocation,
    in the post-update phase — so the new client's agents (and every other
    per-client artifact resolved through this same read) were silently
    skipped on the very first run and only materialized on the second.
    """
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.exceptions import AgentFormatError

    from ._utils import resolve_client_write_targets
    from ._version_manifest import _apply_agent_update

    agents_source = effective_data / "agents"
    if not agents_source.is_dir():
        return
    for client in dict.fromkeys(resolve_client_write_targets(target_dir, ide_override=ide)):
        try:
            fmt = agent_format_for(client)
        except AgentFormatError as exc:
            result.setdefault("info", []).append(f"agents: {client} — {exc}")
            continue
        if not fmt.supports_agents:
            result.setdefault("info", []).append(f"agents: {client} — {fmt.unsupported_reason}")
            continue
        for agent_file in sorted(agents_source.iterdir()):
            if agent_file.suffix != ".md":
                continue
            try:
                rel = fmt.destination_for(agent_file.stem)
            except AgentFormatError as exc:
                result["errors"].append(f"Rejected agent name {agent_file.stem!r} for {client}: {exc}")
                continue
            _apply_agent_update(
                agent_file,
                target_dir / rel,
                result,
                on_progress,
                manifest_hashes,
                client=client,
                manifest_key=rel,
            )


def _update_framework_files(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
    manifest_hashes: dict[str, str] | None = None,
    ide: str | None = None,
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
        on_progress: Optional callback for real-time progress reporting.
        manifest_hashes: SHA256 content hashes from prior manifest for
            user-modification detection (PRD-FIX-068-FR05).
        ide: Target IDE override, forwarded to ``_update_agents`` (G1).
    """
    from ._client_ownership import update_owns_surface

    _update_always_overwrite_files(target_dir, effective_data, result, on_progress)
    _report_preserved_files(target_dir, result)
    # PRD-INFRA-044-FR04: Smart-merge settings.json (preserves user ENABLE_TOOL_SEARCH opt-out).
    # PRD-INFRA-192 FR09 §3: only when claude-code still owns this project's settings.json.
    if update_owns_surface(".claude/settings.json", target_dir, ide):
        _merge_settings_json(
            effective_data / "settings.json",
            target_dir / ".claude" / "settings.json",
            result,
        )
    # PRD-SEC-005-FR02: merge-ensure the credentials.yaml ignore rule on every
    # existing install (gitignore.txt is only deployed on INIT, so update-project
    # would otherwise never refresh a custom .trw/.gitignore).
    _ensure_credentials_gitignored(target_dir, result, on_progress)
    _update_hooks(target_dir, effective_data, result, on_progress, manifest_hashes, ide=ide)
    _update_skills(target_dir, effective_data, result, on_progress, manifest_hashes, ide=ide)
    _update_agents(target_dir, effective_data, result, on_progress, manifest_hashes, ide=ide)


# ---------------------------------------------------------------------------
# MCP config + instruction-file update
# ---------------------------------------------------------------------------


def _update_mcp_config(
    target_dir: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
    ide: str | None = None,
) -> None:
    """Update ``.mcp.json`` and the claude-code ``AGENTS.md`` block.

    Handles the smart-merge of ``.mcp.json`` (ensures the ``trw`` server entry
    is present while preserving all other user-configured MCP servers), the
    TRW block in ``AGENTS.md`` for claude-code (user content outside the
    markers is preserved), and retirement of a TRW-only legacy ``CLAUDE.md``
    (one with user content is reported, never touched).

    Args:
        target_dir: Root of the target git repository.
        result: Mutable result dict accumulating ``updated``, ``created``,
            ``preserved``, and ``errors`` entries.
        on_progress: Optional callback for real-time progress reporting.
        ide: ``--ide`` override, forwarded to the PRD-INFRA-192 FR09 §3
            ownership check so ``.mcp.json`` is merged only for a project
            whose recorded clients (plus *ide*) include claude-code.
    """
    from ._client_ownership import update_owns_surface

    # Smart-merge .mcp.json (ensure trw entry, preserve user entries) — only
    # for a project claude-code still owns; a `[opencode]` project's leftover
    # `.mcp.json` from an older install must stay byte-identical.
    if update_owns_surface(".mcp.json", target_dir, ide):
        _merge_mcp_json(target_dir, result, on_progress)

    from trw_mcp.exceptions import StateError
    from trw_mcp.state.claude_md._orphan_strip import strip_orphaned_agents_md_block

    from ._template_claude_md import (
        _recorded_or_detected_targets,
        claude_code_is_claimed,
        retire_claude_md,
        write_claude_code_agents_md,
    )

    # Recorded, NOT resolved: `resolve_ide_targets` falls through to
    # detection, which reports claude-code for every project TRW has ever
    # installed into (we create `.claude/` ourselves). Passing it here
    # would answer "who reads this file?" with our own artifacts.
    ide_targets = _recorded_or_detected_targets(target_dir)

    # A project installed before a client's AGENTS.md was withdrawn still
    # carries that block, and nothing refreshes it any more.
    #
    # CORE262-14: the strip's write raises StateError on a genuine failure
    # instead of silently returning False, so it is recorded here rather than
    # either swallowed or left to escape.
    try:
        agents_removed = strip_orphaned_agents_md_block(target_dir, ide_targets)
    except StateError as exc:
        result["errors"].append(f"Failed to remove orphaned TRW block from {target_dir / 'AGENTS.md'}: {exc}")
    else:
        if agents_removed:
            result.setdefault("updated", []).append(str(target_dir / "AGENTS.md"))

    # TRW 8.0: claude-code's carrier is AGENTS.md, which Claude Code reads
    # natively. Write it BEFORE retiring the legacy CLAUDE.md so the protocol
    # is never absent from both files.
    errors_before = len(result.get("errors", []))
    if claude_code_is_claimed(target_dir):
        write_claude_code_agents_md(target_dir, result)
        if on_progress and str(target_dir / "AGENTS.md") in result.get("updated", []):
            on_progress("Updated", str(target_dir / "AGENTS.md"))
    # A failed AGENTS.md write keeps the legacy file: the protocol must not
    # end up in neither.
    if len(result.get("errors", [])) == errors_before:
        retire_claude_md(target_dir, result)


# ---------------------------------------------------------------------------
# Artifact name discovery — extracted to ``_artifact_names.py`` (350-eLOC gate).
# Re-exported here for back-compat with callers/tests importing via this facade.
# ---------------------------------------------------------------------------


from ._artifact_names import _get_bundled_names as _get_bundled_names
from ._artifact_names import _get_custom_names as _get_custom_names
