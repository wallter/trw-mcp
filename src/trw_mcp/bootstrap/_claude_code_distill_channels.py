"""Claude Code distill channel bootstrap — install entry-point.

Installs the remaining Claude Code distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .claude/agents/trw-distill-explorer.md    (CC-05)
  - .claude/hooks/pre-tool-distill-hint.sh    (CC-03, only while cc03_hook_enabled)
  - .claude/hooks/lib-distill-hint.sh         (CC-03 shared library, same gate)
  - .trw/channels/manifest.yaml               (two CC channel entries merged)

A bundled hook ships only when it is registered, or sourced by a registered hook
(PRD-INFRA-192). So the CC-03 pair ships AND registers in .claude/settings.json
while ``cc03_hook_enabled`` is on, and both are withdrawn when it is turned off:
an unedited copy is removed, an edited one is kept.

PRD-DIST-2405 FR41-FR43.

PRD-CORE-239 FR01 removed this client's instruction-file segment channel(s);
the counts above are the post-removal reality. Prose that outlives the code it
describes is defect pattern P7 — the class this whole removal was about.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import structlog

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file
from trw_mcp.bootstrap._distill_channel_manifest import merge_distill_channel_manifest
from trw_mcp.bootstrap._file_ops import _new_result
from trw_mcp.bootstrap._safe_remove import path_refusal, remove_if_hash
from trw_mcp.bootstrap._settings_merge import _hook_entry_identity, _set_hook_registration
from trw_mcp.channels._manifest_loader import ManifestValidationError
from trw_mcp.channels.claude_code._explorer_subagent import (
    EXPLORER_AGENT_RELPATH,
    cc05_explorer_user_edited,
    install_cc05_subagent,
    withdraw_cc05_subagent_if_unedited,
)
from trw_mcp.channels.claude_code._hook_helpers import read_cc03_config

log = structlog.get_logger(__name__)

__all__ = [
    "apply_cc03_hook_registration",
    "bootstrap_cc_channel_manifest",
    "cc03_hook_scripts_present",
    "cc03_registered_in_settings",
    "install_claude_code_distill_channels",
]

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data" / "claude_code" / "channels"
_MANIFEST_DATA = _DATA_DIR / "manifest-claude-code.yaml"

# Hook scripts are stored in the dev repo's .claude/hooks/ and are
# bundled here as static strings for distribution to target projects.
_HOOKS_DATA_DIR = Path(__file__).parent.parent / "data" / "claude_code" / "hooks"


# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_cc_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Add Claude Code channel entries while preserving other clients."""
    added, total = merge_distill_channel_manifest(repo_root, _MANIFEST_DATA, "claude-code")
    log.debug(
        "cc_manifest_bootstrapped",
        added=added,
        total=total,
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# Hook script content (shipped with the package, installed to target project)
# ---------------------------------------------------------------------------


def _get_hook_content(hook_name: str) -> str | None:
    """Return bundled hook script content from data directory, or None if absent."""
    hook_path = _HOOKS_DATA_DIR / hook_name
    if hook_path.exists():
        return hook_path.read_text(encoding="utf-8")
    return None


_CC03_HOOKS = ("pre-tool-distill-hint.sh", "lib-distill-hint.sh")
_CC03_ENTRY: dict[str, object] = {
    "matcher": "Write|Edit|MultiEdit",
    "hooks": [
        {
            "type": "command",
            "command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/pre-tool-distill-hint.sh"',
            # SECONDS (Claude Code's unit). 3000 was meant as 3000 ms but reads as 50 min stalling every Write/Edit.
            # The hook bounds itself at 2.5 s (SIGALRM + _trw_bounded_python), so this is only the outer backstop:
            # 5 s is the floor the other hooks use, and a hint that times out fails open.
            "timeout": 5,
        }
    ],
}


def _withdraw_hook(repo_root: Path, hook_name: str, result: dict[str, list[str]]) -> None:
    """Remove the installed copy of *hook_name* if it is still the bundled bytes."""
    dest = repo_root / ".claude" / "hooks" / hook_name
    rel = str(dest.relative_to(repo_root))
    refusal = path_refusal(dest, repo_root)  # a symlinked hook (or parent) is never followed or unlinked
    if refusal:
        result.setdefault("warnings", []).append(f"{rel}: left untouched ({refusal})")
        return
    if not dest.is_file():
        return
    content = _get_hook_content(hook_name)
    if content is None:  # no bundled bytes to prove ownership against: keep it
        return
    bundled = content.encode("utf-8")
    try:
        unedited = dest.read_bytes() == bundled
    except OSError as exc:  # trw-fail-silent-allow: reported as a warning; the hook is kept, never guessed at
        result.setdefault("warnings", []).append(f"{rel}: left untouched (could not read it: {exc})")
        return
    if not unedited:
        result["preserved"].append(rel)
        return
    # remove_if_hash captures into .trw/trash, re-verifies there and links back on a mismatch; it never
    # unlinks, so an edit or open-fd write racing this step keeps its bytes (HB-2).
    outcome = remove_if_hash(dest, repo_root, hashlib.sha256(bundled).hexdigest(), key=rel)
    if outcome.status == "removed":
        result.setdefault("removed", []).append(rel)
        result.setdefault("trashed", []).append(rel)
    elif outcome.status == "kept" and outcome.published is not None:
        result["preserved"].append(rel)  # changed at the act and put back where it was
    elif outcome.status == "retained" or outcome.retained_at is not None:
        # The bytes were captured but are not back at their name (could not restore, or the folder moved).
        where = outcome.retained_at or ".trw/trash (exact folder unknown)"
        result.setdefault("warnings", []).append(
            f"{rel}: moved to .trw/trash and not put back ({outcome.reason}); your copy is in {where}"
        )
    else:  # kept before any capture: nothing was moved
        result.setdefault("warnings", []).append(f"{rel}: left untouched ({outcome.reason})")


def _restore_exec_bit(dest: Path, hook_name: str, rel: str, result: dict[str, list[str]]) -> bool:
    """Give a kept shell script back the exec bit it lost; True when it did (only the mode changes).

    Doctor's ``hook_channel`` row FAILs a CC-03 script that is not executable and names ``update-project``,
    which used to leave an identical present script untouched, so the row stayed red.
    """
    if not hook_name.endswith(".sh") or os.access(dest, os.X_OK):
        return False
    dest.chmod(dest.stat().st_mode | 0o111)
    result["updated"].append(f"{rel} (exec bit restored)")
    return True


def _install_hook(
    repo_root: Path,
    hook_name: str,
    result: dict[str, list[str]],
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Install a bundled hook script to .claude/hooks/ if the source exists.

    An existing copy is replaced only when it is bytes TRW wrote (the bundled script, or the manifest's
    recorded hash); a hand edit, or a file that cannot be read, is kept and named in ``warnings`` (HB-2).
    """
    from trw_mcp.bootstrap._managed_client_artifacts import artifact_user_edited_against

    content = _get_hook_content(hook_name)
    if content is None:
        # Source not present in this distribution — skip silently.
        log.debug("cc_hook_source_absent", hook=hook_name, outcome="skipped")
        return

    dest = repo_root / ".claude" / "hooks" / hook_name
    rel = dest.relative_to(repo_root).as_posix()

    try:
        existed = os.path.lexists(dest)
        # A symlink at the name is never read through: write_checkout_file refuses it as symlink_leaf.
        if existed and not dest.is_symlink():
            refusal = path_refusal(dest, repo_root)  # a symlinked parent is never read through either
            if refusal or not stat.S_ISREG(dest.lstat().st_mode):  # a FIFO would block the read
                result.setdefault("warnings", []).append(f"{rel}: left untouched ({refusal or 'not a regular file'})")
                return
            if dest.read_text(encoding="utf-8") == content:
                if not _restore_exec_bit(dest, hook_name, rel, result):
                    result["preserved"].append(rel)
                return
            bundled = {hashlib.sha256(content.encode("utf-8")).hexdigest()}
            # The CC-03 pair is recorded under its repo-relative path; a bare hook name is the legacy key.
            key = rel if manifest_hashes and rel in manifest_hashes else hook_name
            if artifact_user_edited_against(dest, key, bundled, manifest_hashes):
                result["preserved"].append(rel)
                # A deleted recorded file is tombstoned, so the fresh copy needs --reprovision.
                result.setdefault("warnings", []).append(
                    f"{rel}: kept because it was edited "
                    f"(for the fresh copy, delete it and run update-project --reprovision {rel})"
                )
                if hook_name.endswith(".sh") and not os.access(dest, os.X_OK):
                    result.setdefault("warnings", []).append(
                        f"{rel}: your edited copy is not executable (chmod +x {rel})"
                    )
                return
        write_checkout_file(repo_root, dest, content)
        # Make shell scripts executable
        if hook_name.endswith(".sh"):
            dest.chmod(dest.stat().st_mode | 0o111)
        result["updated" if existed else "created"].append(rel)
    except (OSError, UnicodeDecodeError, UnsafeWriteError) as exc:
        result["errors"].append(f"Failed to install {hook_name}: {exc}")


def cc03_hook_scripts_present(target_dir: Path) -> bool:
    """Whether both CC-03 hook scripts exist under ``.claude/hooks/`` and are executable.

    Doctor's ``hook_channel`` row (PRD-CORE-336-FR04) needs this: registering
    the PreToolUse entry proves nothing if the script it names is missing or
    not executable, and the two used to be checked separately (or not at all).
    """

    return all(
        (target_dir / ".claude" / "hooks" / name).is_file()
        and os.access(target_dir / ".claude" / "hooks" / name, os.X_OK)
        for name in _CC03_HOOKS
    )


def cc03_registered_in_settings(target_dir: Path) -> bool:
    """Whether the CC-03 PreToolUse entry is ACTUALLY present in ``.claude/settings.json``.

    Reads the file back rather than trusting ``cc03_hook_enabled``: the flag
    being on and the registration existing used to be treated as the same fact,
    which is exactly how ``preserve_uncommitted_changes`` silently dropping the
    entry (see :func:`apply_cc03_hook_registration`) produced a doctor ``PASS``
    for a hook that was never wired (release-window fix, 2026-09-27).
    """
    from trw_mcp.bootstrap._file_ops import read_json_object

    data = read_json_object(target_dir / ".claude" / "settings.json", context="doctor_hook_channel")
    hooks = data.get("hooks") if data is not None else None
    entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    if not isinstance(entries, list):
        return False
    identity = _hook_entry_identity(_CC03_ENTRY)
    return any(_hook_entry_identity(entry) == identity for entry in entries)


def apply_cc03_hook_registration(target_dir: Path) -> bool:
    """(Re)write ``.claude/settings.json``'s PreToolUse entry to match ``cc03_hook_enabled``.

    Idempotent: a no-op when the entry already matches. Split out of
    :func:`install_claude_code_distill_channels` so ``update_project`` can call
    it a SECOND time, after ``preserve_uncommitted_changes`` runs -- that guard
    reverts any write this run made to a dirty (uncommitted) path whose bytes it
    cannot prove TRW owns, and ``settings.json`` carries no manifest content
    hash of its own (it is a per-entry smart-merge target, not a whole-file
    artifact), so a fresh, not-yet-committed install had its hook registration
    silently discarded on the very next ``update-project`` (release-window fix,
    2026-09-27). Calling this again after that guard re-applies TRW's own entry
    on top of whatever the guard restored, without touching any other entry.
    """
    enabled = bool(read_cc03_config(target_dir)["cc03_hook_enabled"])
    settings = target_dir / ".claude" / "settings.json"
    return settings.is_file() and _set_hook_registration(settings, "PreToolUse", _CC03_ENTRY, present=enabled)


def sync_cc03_hook_files(
    target_dir: Path,
    result: dict[str, list[str]],
    manifest_hashes: dict[str, str] | None = None,
) -> bool:
    """Ship the CC-03 hook pair to ``.claude/hooks/`` while enabled, withdraw it otherwise.

    Shared by every client that runs the installed CC-03 file (claude-code and
    codex, PRD-CORE-336-FR04). Returns whether ``cc03_hook_enabled`` is on, so
    the caller registers or deregisters its own hook entry to match. *manifest_hashes* is the manifest as it
    stood before this run; without it the on-disk manifest is the fallback baseline for the edit guard.
    """
    enabled = bool(read_cc03_config(target_dir)["cc03_hook_enabled"])
    if enabled and manifest_hashes is None:
        from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest

        manifest_hashes = _manifest_content_hashes(_read_manifest(target_dir))
    for hook_name in _CC03_HOOKS:
        try:
            if enabled:
                _install_hook(target_dir, hook_name, result, manifest_hashes)
            else:
                _withdraw_hook(target_dir, hook_name, result)
        except Exception as exc:  # justified: fail-open, hook install is best-effort
            log.warning("cc_hook_install_failed", hook=hook_name, error=str(exc), outcome="warning")
            result["errors"].append(f"CC-03 hook {hook_name} install failed: {exc}")
    return enabled


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_claude_code_distill_channels(
    target_dir: Path,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Install all Claude Code distill channel artifacts.

    Called from ``_init_project_ide._install_opencode_artifacts`` and
    ``_ide_targets._update_opencode_artifacts`` equivalents.

    Args:
        target_dir: Repository root directory.
        force: Reserved; never overrides the user-edit guard on the explorer agent (HB-2).
        manifest_hashes: ``content_hashes`` of the manifest as it stood before this run (None on a
            first install); an explorer agent whose bytes are not TRW's own is kept.

    Returns:
        Dict with ``created``, ``updated``, ``preserved``, ``errors`` lists.
    """
    result = _new_result()

    # 1. Install CC-05 subagent (.claude/agents/trw-distill-explorer.md).
    #    PRD-CORE-239: gated on licence. The subagent is "powered by
    #    trw-distill" and is useless without it, so an unlicensed project used
    #    to receive an agent it could never run.
    try:
        from trw_mcp.bootstrap._distill_entitlement import distill_artifacts_entitled

        if distill_artifacts_entitled(artifact="cc-05-distill-explorer", repo_root=target_dir):
            if cc05_explorer_user_edited(target_dir, manifest_hashes):
                result.setdefault("warnings", []).append(
                    f"{EXPLORER_AGENT_RELPATH}: kept because it was edited (delete it and re-run update-project for the fresh render)"
                )
            written = install_cc05_subagent(target_dir, manifest_hashes)
            result["created" if written else "preserved"].append(EXPLORER_AGENT_RELPATH)
        else:
            # 2026-09-27 audit (touchpoint #6): a project that LOST entitlement
            # (licence expired, or the checkout was copied to a distill-free
            # machine) used to keep this agent file forever — install-only
            # gating never removed anything. Withdraw only an unedited copy.
            # The caller's pre-run manifest is the baseline: by now the on-disk manifest may already be
            # rewritten from current content, which would make a hand edit look recorded. Re-read only as a fallback.
            if manifest_hashes is None:
                from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest

                manifest_hashes = _manifest_content_hashes(_read_manifest(target_dir))
            if withdraw_cc05_subagent_if_unedited(target_dir, manifest_hashes):
                result["updated"].append(EXPLORER_AGENT_RELPATH)
                # Captured into .trw/trash: the uncommitted-changes guard must not restore it.
                result.setdefault("trashed", []).append(EXPLORER_AGENT_RELPATH)
    except Exception as exc:  # justified: fail-open, subagent is best-effort
        log.warning("cc05_subagent_install_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"CC-05 subagent install failed: {exc}")

    # 2. CC-03 hook pair: shipped and registered while enabled, withdrawn otherwise.
    sync_cc03_hook_files(target_dir, result, manifest_hashes)
    if apply_cc03_hook_registration(target_dir):
        result["updated"].append(".claude/settings.json")

    # 3. Bootstrap channel manifest (two CC channel entries)
    try:
        bootstrap_cc_channel_manifest(target_dir)
    except ManifestValidationError as exc:
        log.warning(
            "cc_manifest_validation_error",
            error=str(exc),
            outcome="warning",
        )
        result["errors"].append(f"CC manifest bootstrap failed: {exc}")
    except Exception as exc:  # justified: fail-open, manifest is best-effort
        log.warning("cc_manifest_bootstrap_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"CC manifest bootstrap failed: {exc}")

    log.debug(
        "claude_code_distill_channels_installed",
        repo_root=str(target_dir),
        created=len(result["created"]),
        updated=len(result["updated"]),
        errors=len(result["errors"]),
        outcome="ok",
    )
    return result
