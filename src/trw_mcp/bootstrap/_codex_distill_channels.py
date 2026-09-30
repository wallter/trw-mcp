"""Codex distill channel bootstrap — install entry-point.

Installs the remaining Codex distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .codex/hooks/trw_post_edit_telemetry.py  (codex-posttooluse-telemetry)
  - .codex/hooks.json                         (PostToolUse group for distill hook)
  - .trw/channels/manifest.yaml              (two codex channel entries merged)

AGENTS.md segment (codex-agents-md-hotspots) is a runtime channel managed by
``render_and_inject()`` — no stub file is written at install time.

PRD-DIST-2402 FR41-FR43.

PRD-CORE-239 FR01 removed this client's instruction-file segment channel(s);
the counts above are the post-removal reality. Prose that outlives the code it
describes is defect pattern P7 — the class this whole removal was about.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file
from trw_mcp.bootstrap._codex_hooks import codex_hooks_review_warning
from trw_mcp.bootstrap._distill_channel_manifest import merge_distill_channel_manifest
from trw_mcp.bootstrap._file_ops import _new_result, read_json_object
from trw_mcp.channels._manifest_loader import ManifestValidationError

log = structlog.get_logger(__name__)

__all__ = [
    "bootstrap_codex_channel_manifest",
    "codex_pre_edit_hint_registered",
    "install_codex_distill_channels",
    "merge_distill_hook_into_hooks_json",
]

# Sentinel string used to detect idempotency — if this appears in the command
# of any existing PostToolUse hook, we skip the duplicate insertion.
_DISTILL_HOOK_SENTINEL = "trw_post_edit_telemetry"
_CODEX_HOOKS_JSON = ".codex/hooks.json"


_TELEMETRY_GROUP_DESCRIPTION = "TRW managed: trw-distill PostToolUse telemetry"
_TELEMETRY_STATUS = "Recording TRW distill telemetry"


def _telemetry_group(command: str) -> dict[str, Any]:
    return {
        "description": _TELEMETRY_GROUP_DESCRIPTION,
        "hooks": [{"type": "command", "command": command, "statusMessage": _TELEMETRY_STATUS}],
    }


def distill_hook_group(target_dir: Path) -> dict[str, Any]:
    """The PostToolUse group :func:`merge_distill_hook_into_hooks_json` writes; uninstall withdraws exactly this.

    ``.codex/hooks.json`` is committed, so the command names no machine path (E2E-CODEX-INIT-ARTIFACTS).
    Codex runs hook commands through a shell -- E2E-INC-032 observed the ``$(...)`` expansion -- so this is
    the root expression the PreToolUse hooks use (``_codex_hooks._trw_hook_group``).
    """
    git_root = "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
    return _telemetry_group(f'python3 "{git_root}/.codex/hooks/trw_post_edit_telemetry.py"')


def legacy_distill_hook_group(target_dir: Path) -> dict[str, Any]:
    """The absolute-path group every install before E2E-CODEX-INIT-ARTIFACTS wrote.

    ``update-project`` replaces it with :func:`distill_hook_group`; uninstall withdraws it from an install
    that was never updated.
    """
    script = target_dir.resolve() / ".codex" / "hooks" / "trw_post_edit_telemetry.py"
    return _telemetry_group(f'python3 "{script}"')


def merge_distill_hook_into_hooks_json(target_dir: Path) -> dict[str, Any]:
    """Merge the TRW distill PostToolUse hook group into .codex/hooks.json.

    Safe-merge semantics:
    - If .codex/hooks.json exists, load it and APPEND the distill group to
      hooks.PostToolUse (preserves all existing ceremony or user entries).
    - If the file does not exist, create it with only the distill group.
    - Idempotent: if a PostToolUse command containing ``trw_post_edit_telemetry``
      already exists, does not duplicate it.

    The hook command uses an absolute-git-root-relative path so the script
    resolves correctly regardless of the working directory when Codex fires the
    hook.

    Returns:
        Dict with keys: written (bool), path (str), skipped (bool),
        error (str | None).
    """
    hooks_json_path = target_dir / _CODEX_HOOKS_JSON

    distill_group = distill_hook_group(target_dir)

    # Load existing hooks.json if present, through the shared structural-safe
    # seam. read_json_object returns None for an unreadable / non-UTF-8 /
    # malformed / non-object file (a non-UTF-8 file raises UnicodeDecodeError, a
    # ValueError that is NOT an OSError, and so previously escaped uncaught) and
    # emits a content-free structural diagnostic. On any such failure ``existing``
    # stays ``{}`` so we start fresh rather than leave a corrupt file.
    existing: dict[str, Any] = {}
    if hooks_json_path.exists():
        parsed = read_json_object(hooks_json_path, context="codex_distill_hooks")
        if parsed is not None:
            existing = dict(parsed)

    # Idempotency: check if already registered
    hooks_section = existing.get("hooks", {})
    post_tool_groups: list[dict[str, Any]] = []
    if isinstance(hooks_section, dict):
        raw_groups = hooks_section.get("PostToolUse", [])
        if isinstance(raw_groups, list):
            post_tool_groups = list(raw_groups)

    # Current form present: nothing to do. TRW's old absolute-path group: replace it (migration). Any other
    # group naming the script is one the user edited, and stays as it is.
    legacy_group = legacy_distill_hook_group(target_dir)
    migrated = [group for group in post_tool_groups if group != legacy_group]
    already_registered = distill_group in migrated or (
        len(migrated) == len(post_tool_groups)
        and any(
            _DISTILL_HOOK_SENTINEL in str(cmd.get("command", ""))
            for group in post_tool_groups
            if isinstance(group, dict)
            for cmd in (group.get("hooks") or [])
            if isinstance(cmd, dict)
        )
    )

    if already_registered:
        log.debug(
            "codex_distill_hook_already_registered",
            path=str(hooks_json_path),
            outcome="skipped",
        )
        return {"written": False, "path": str(hooks_json_path), "skipped": True, "error": None}

    # Append the distill group
    if not isinstance(hooks_section, dict):
        hooks_section = {}
    hooks_section["PostToolUse"] = [*migrated, distill_group]
    existing["hooks"] = hooks_section

    try:
        write_checkout_file(target_dir, hooks_json_path, json.dumps(existing, indent=2) + "\n")
    except (OSError, UnsafeWriteError) as exc:
        log.warning(
            "codex_distill_hooks_json_write_failed",
            path=str(hooks_json_path),
            error=str(exc),
            outcome="error",
        )
        return {"written": False, "path": str(hooks_json_path), "skipped": False, "error": str(exc)}

    log.debug(
        "codex_distill_hook_registered",
        path=str(hooks_json_path),
        command=distill_group["hooks"][0]["command"],
        outcome="written",
    )
    return {"written": True, "path": str(hooks_json_path), "skipped": False, "error": None}


#: The CC-03 script Codex runs before apply_patch (PRD-CORE-336-FR04). Codex's
#: PreToolUse contract takes the same hookSpecificOutput.additionalContext shape
#: Claude Code does (developers.openai.com/codex/hooks), so one installed file
#: serves both clients.
_PRE_EDIT_HINT_SCRIPT = "pre-tool-distill-hint.sh"


def _pre_edit_hint_group() -> dict[str, Any]:
    """The TRW-managed Codex PreToolUse group that runs the CC-03 hint hook."""
    from trw_mcp.bootstrap._codex_hooks import _trw_hook_group

    return dict(
        _trw_hook_group(
            event="PreToolUse",
            script_name=_PRE_EDIT_HINT_SCRIPT,
            status_message="Loading TRW pre-edit hint",
            matcher="apply_patch",
            timeout=3,
        )
    )


def _is_pre_edit_hint_group(group: object) -> bool:
    return isinstance(group, dict) and any(
        isinstance(hook, dict) and f"/.claude/hooks/{_PRE_EDIT_HINT_SCRIPT}" in str(hook.get("command", ""))
        for hook in group.get("hooks") or []
    )


def codex_pre_edit_hint_registered(target_dir: Path) -> bool:
    """Whether the CC-03 PreToolUse group is ACTUALLY present in ``.codex/hooks.json``.

    Reads the file back rather than trusting ``cc03_hook_enabled`` (doctor
    ``hook_channel`` row, release-window fix 2026-09-27) -- the same
    false-PASS shape ``_claude_code_distill_channels.cc03_registered_in_settings``
    fixes for Claude Code's own registration file.
    """
    data = read_json_object(target_dir / _CODEX_HOOKS_JSON, context="doctor_hook_channel")
    hooks = data.get("hooks") if data is not None else None
    groups = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    return isinstance(groups, list) and any(_is_pre_edit_hint_group(group) for group in groups)


def set_pre_edit_hint_registration(target_dir: Path, *, present: bool) -> bool:
    """Add or remove the pre-edit hint group in ``.codex/hooks.json``; return whether the file changed.

    Registered only while ``cc03_hook_enabled`` is on, because the script it
    runs is shipped only then: a registration pointing at a withdrawn file would
    fail on every patch. An unreadable hooks.json is left untouched.
    """
    hooks_json_path = target_dir / _CODEX_HOOKS_JSON
    existing: dict[str, Any] = {}
    if hooks_json_path.exists():
        parsed = read_json_object(hooks_json_path, context="codex_pre_edit_hint")
        if parsed is None:
            return False
        existing = dict(parsed)
    elif not present:
        return False
    hooks_section = existing.get("hooks")
    hooks: dict[str, Any] = dict(hooks_section) if isinstance(hooks_section, dict) else {}
    raw_groups = hooks.get("PreToolUse")
    groups = (
        [group for group in raw_groups if not _is_pre_edit_hint_group(group)] if isinstance(raw_groups, list) else []
    )
    if present:
        groups.append(_pre_edit_hint_group())
    if groups == (raw_groups if isinstance(raw_groups, list) else []):
        return False
    if groups:
        hooks["PreToolUse"] = groups
    else:
        hooks.pop("PreToolUse", None)
    existing["hooks"] = hooks
    write_checkout_file(target_dir, hooks_json_path, json.dumps(existing, indent=2) + "\n")
    return True


# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data" / "codex" / "channels"
_MANIFEST_DATA = _DATA_DIR / "manifest-codex.yaml"


# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_codex_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Load manifest-codex.yaml and merge three ChannelEntry records.

    Merge is additive — existing entries for other clients are preserved.
    All-or-nothing: if any entry fails validation, raises ManifestValidationError.

    Args:
        repo_root: Repository root directory.

    Returns:
        Dict with ``status`` and ``count`` of entries added.
    """
    added, total = merge_distill_channel_manifest(repo_root, _MANIFEST_DATA, "codex")

    log.debug(
        "codex_manifest_bootstrapped",
        added=added,
        total=total,
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_codex_distill_channels(
    target_dir: Path,
    force: bool = False,
) -> dict[str, list[str]]:
    """Install all Codex distill channel artifacts.

    Installs the PostToolUse hook script and merges channel manifest entries.

    Args:
        target_dir: Repository root directory.
        force: When True, rewrite artifacts even when their content is already
            identical. A hook script whose content DIFFERS is refreshed either
            way — a stale registered hook is a security problem, not a
            preference.

    Returns:
        Dict with ``created``, ``updated``, ``preserved``, ``errors``, and
        ``warnings`` lists.

    Raises:
        ValueError: If ``target_dir`` resolves to the user's home directory.
            TRW does not write Codex distill channels to ``~/.codex/``.
    """
    # FR13: Reject global home directory — TRW does not write to ~/.codex/AGENTS.md.
    if target_dir.resolve() == Path.home().resolve():
        raise ValueError(
            "TRW does not write Codex distill channels to the home directory. "
            "Pass a project repository root, not Path.home()."
        )

    result = _new_result()
    result["warnings"] = []

    # 1. Install PostToolUse telemetry hook script
    try:
        from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

        # ``overwrite=True`` is deliberate and must not become ``force``: a
        # registered-but-stale hook script is the defect commit 1fc4be8850
        # fixed, so a differing hook is always refreshed. ``force`` means
        # "rewrite even when the bytes already match" — the only decision left
        # once security mandates the refresh. It used to read
        # ``overwrite=force or True``, which is ``True`` for every input, so
        # ``force`` decided nothing and the ``preserved`` bucket below was
        # unreachable from production.
        hook_result = install_hook_script(target_dir, overwrite=True, rewrite_unchanged=force)
        rel = ".codex/hooks/trw_post_edit_telemetry.py"
        outcome = hook_result.get("outcome")
        if outcome == "preserved" or hook_result.get("skipped"):
            result["preserved"].append(rel)
        elif outcome == "updated":
            result["updated"].append(rel)
        else:
            result["created"].append(rel)
    except Exception as exc:  # justified: fail-open, hook is best-effort
        log.warning("codex_hook_install_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Codex PostToolUse hook install failed: {exc}")

    # 1b. Register the distill hook in .codex/hooks.json so Codex actually invokes it.
    #     Codex only fires hooks listed in hooks.json; without this the script is orphaned.
    try:
        hooks_json_result = merge_distill_hook_into_hooks_json(target_dir)
        if hooks_json_result.get("error"):
            result["errors"].append(f"hooks.json merge failed: {hooks_json_result['error']}")
        elif hooks_json_result.get("skipped"):
            result["preserved"].append(_CODEX_HOOKS_JSON)
        else:
            result["updated"].append(_CODEX_HOOKS_JSON)
    except Exception as exc:  # justified: fail-open, hooks.json merge is best-effort
        log.warning("codex_hooks_json_merge_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"hooks.json merge failed: {exc}")

    # 1c. Pre-edit hint (PRD-CORE-336-FR04): ship the CC-03 script to .claude/hooks
    #     and register it for apply_patch while cc03_hook_enabled is on.
    try:
        from trw_mcp.bootstrap._claude_code_distill_channels import sync_cc03_hook_files

        enabled = sync_cc03_hook_files(target_dir, result)
        if set_pre_edit_hint_registration(target_dir, present=enabled):
            result["updated"].append(_CODEX_HOOKS_JSON)
    except Exception as exc:  # justified: fail-open, the hint is advisory
        log.warning("codex_pre_edit_hint_install_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Codex pre-edit hint hook install failed: {exc}")

    # 2. Bootstrap channel manifest (two codex channel entries)
    try:
        bootstrap_codex_channel_manifest(target_dir)
    except ManifestValidationError as exc:
        log.warning(
            "codex_manifest_validation_error",
            error=str(exc),
            outcome="warning",
        )
        result["errors"].append(f"Codex manifest bootstrap failed: {exc}")
    except Exception as exc:  # justified: fail-open, manifest is best-effort
        log.warning("codex_manifest_bootstrap_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Codex manifest bootstrap failed: {exc}")

    # FR18: Add gitignore entries for runtime state/lock/telemetry files (not hook scripts,
    # which are project config and SHOULD be git-tracked).
    try:
        from trw_mcp.channels._gitignore import add_gitignore_entry

        _GITIGNORE_ENTRIES = [
            ".trw/channels/codex-*.state.json",
            ".trw/channels/codex-*.lock",
            ".trw/telemetry/channel-events.jsonl*",
        ]
        for entry in _GITIGNORE_ENTRIES:
            add_gitignore_entry(target_dir, entry)
    except Exception as exc:  # justified: fail-open, gitignore is best-effort
        log.warning("codex_distill_gitignore_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"gitignore update failed: {exc}")

    # FR19: Emit hooks approval notice so the operator knows to run /hooks in Codex.
    result["warnings"].append(codex_hooks_review_warning(target_dir))

    log.debug(
        "codex_distill_channels_installed",
        repo_root=str(target_dir),
        created=len(result["created"]),
        updated=len(result["updated"]),
        errors=len(result["errors"]),
        outcome="ok",
    )
    return result
