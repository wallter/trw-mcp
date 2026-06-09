"""Codex distill channel bootstrap — install entry-point.

Installs all three Codex distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .codex/hooks/trw_post_edit_telemetry.py  (codex-posttooluse-telemetry)
  - .codex/hooks.json                         (PostToolUse group for distill hook)
  - .trw/channels/manifest.yaml              (three codex channel entries merged)

AGENTS.md segment (codex-agents-md-hotspots) is a runtime channel managed by
``render_and_inject()`` — no stub file is written at install time.

PRD-DIST-2402 FR41-FR43.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from ruamel.yaml import YAML

from trw_mcp.bootstrap._codex_hooks import codex_hooks_review_warning
from trw_mcp.bootstrap._file_ops import read_json_object
from trw_mcp.channels._manifest_loader import (
    ManifestValidationError,
    auto_recreate_empty,
    load,
    write,
)
from trw_mcp.channels._manifest_models import ChannelEntry
from trw_mcp.channels._provenance import now_utc_iso8601

log = structlog.get_logger(__name__)

__all__ = [
    "bootstrap_codex_channel_manifest",
    "install_codex_distill_channels",
    "merge_distill_hook_into_hooks_json",
]

# Sentinel string used to detect idempotency — if this appears in the command
# of any existing PostToolUse hook, we skip the duplicate insertion.
_DISTILL_HOOK_SENTINEL = "trw_post_edit_telemetry"
_CODEX_HOOKS_JSON = ".codex/hooks.json"


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

    # Use the absolute path to the hook script (computed at install time).
    # The hooks.json command string is NOT run through a shell by Codex, so
    # $(git rev-parse --show-toplevel) shell expansion is NOT available here.
    # We use the absolute path so the script resolves correctly when Codex
    # spawns the hook process.
    hook_script_abs = target_dir.resolve() / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    # Build the distill PostToolUse group in the hooks.json format:
    # hooks.PostToolUse is an array of groups; each group has description + hooks array.
    hook_command = f'python3 "{hook_script_abs}"'

    distill_group: dict[str, Any] = {
        "description": "TRW managed: trw-distill PostToolUse telemetry",
        "hooks": [
            {
                "type": "command",
                "command": hook_command,
                "statusMessage": "Recording TRW distill telemetry",
            }
        ],
    }

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

    already_registered = any(
        _DISTILL_HOOK_SENTINEL in str(cmd.get("command", ""))
        for group in post_tool_groups
        if isinstance(group, dict)
        for cmd in (group.get("hooks") or [])
        if isinstance(cmd, dict)
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
    if "PostToolUse" not in hooks_section or not isinstance(hooks_section["PostToolUse"], list):
        hooks_section["PostToolUse"] = []
    hooks_section["PostToolUse"].append(distill_group)
    existing["hooks"] = hooks_section

    try:
        hooks_json_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_json_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
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
        command=hook_command,
        outcome="written",
    )
    return {"written": True, "path": str(hooks_json_path), "skipped": False, "error": None}


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
    yaml = YAML(typ="safe")
    raw: Any = yaml.load(_MANIFEST_DATA.read_text(encoding="utf-8")) or {}
    raw_channels: list[dict[str, Any]] = raw.get("channels", [])

    # Validate all entries first (all-or-nothing)
    validated: list[ChannelEntry] = []
    for entry_dict in raw_channels:
        try:
            validated.append(ChannelEntry.model_validate(entry_dict))
        except Exception as exc:
            raise ManifestValidationError(f"codex manifest entry validation failed: {exc}") from exc

    # Load or recreate target manifest
    manifest_path = repo_root / ".trw" / "channels" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        manifest = load(manifest_path)
    except Exception:
        auto_recreate_empty(manifest_path)
        manifest = load(manifest_path)

    # Merge: add new entries, preserve existing
    existing_ids = {e.id for e in manifest.channels}
    added = 0
    for entry in validated:
        if entry.id not in existing_ids:
            manifest.channels.append(entry)
            existing_ids.add(entry.id)
            added += 1

    manifest.generated_at = now_utc_iso8601()
    write(manifest, manifest_path)

    log.debug(
        "codex_manifest_bootstrapped",
        added=added,
        total=len(manifest.channels),
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
        force: When True, overwrite existing hook script unconditionally.

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

    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
        "warnings": [],
    }

    # 1. Install PostToolUse telemetry hook script
    try:
        from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

        hook_result = install_hook_script(target_dir, overwrite=force or True)
        rel = ".codex/hooks/trw_post_edit_telemetry.py"
        if hook_result.get("skipped"):
            result["preserved"].append(rel)
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

    # 2. Bootstrap channel manifest (three codex channel entries)
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
    result["warnings"].append(codex_hooks_review_warning())

    log.debug(
        "codex_distill_channels_installed",
        repo_root=str(target_dir),
        created=len(result["created"]),
        updated=len(result["updated"]),
        errors=len(result["errors"]),
        outcome="ok",
    )
    return result
