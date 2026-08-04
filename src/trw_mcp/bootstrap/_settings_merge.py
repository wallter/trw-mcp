"""``settings.json`` smart-merge helpers.

Belongs to the ``_template_updater.py`` facade. Re-exported there for
back-compat with callers/tests that import via
``trw_mcp.bootstrap._template_updater``.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from ._file_ops import read_json_object

logger = structlog.get_logger(__name__)


def _hook_entry_identity(entry: object) -> str:
    """Stable identity for one settings.json hook entry (PRD-SEC-013 FR09).

    The hook command(s) are the identity — each hook script path is unique in
    this repo's convention, so a matcher rename never duplicates an entry and two
    different scripts never collide. Non-object entries fall back to their JSON
    form so a hand-edited settings file still merges deterministically.
    """
    if not isinstance(entry, dict):
        return json.dumps(entry, sort_keys=True)
    hooks = entry.get("hooks")
    commands = (
        [str(hook.get("command", "")) for hook in hooks if isinstance(hook, dict)] if isinstance(hooks, list) else []
    )
    if commands:
        return "|".join(sorted(commands))
    return json.dumps(entry, sort_keys=True)


def _merge_settings_json(
    src: Path,
    dest: Path,
    result: dict[str, list[str]],
    dry_run: bool = False,
) -> None:
    """Smart-merge bundled settings.json into existing user settings.

    PRD-INFRA-044-FR04: Preserves user opt-out of ENABLE_TOOL_SEARCH
    while adding missing env keys from the bundled template. All
    non-env top-level keys (hooks, permissions, etc.) from the existing
    file are preserved.

    Robustness / leak discipline: both reads go through the structural-safe
    :func:`read_json_object` seam, so a non-UTF-8, malformed, or non-object
    ``settings.json`` on either side never raises (``UnicodeDecodeError`` is a
    ``ValueError``, not an ``OSError``, and was previously uncaught) and never
    leaks the file's bytes — diagnostics carry a reason *category* only.

      - Bundled template invalid / non-object → the user's existing settings are
        left untouched and a structural error is recorded. We never overwrite a
        good user file from a broken bundled source.
      - Existing settings unreadable / corrupt / non-object → fall back to the
        module's long-standing recovery behavior (copy the valid bundled
        template), with a content-free warning.
    """
    # Lazy + module-object import (not `from ... import _update_or_report`):
    # keeps this sibling free of a module-level circular import against the
    # facade that imports us, and means an existing `monkeypatch.setattr(
    # _template_updater, "_update_or_report", ...)` still propagates here.
    from trw_mcp.bootstrap import _template_updater as _parent

    if not src.is_file():
        return
    if not dest.exists():
        # New install — copy bundled template directly
        _parent._update_or_report(src, dest, result, dry_run)
        return

    bundled = read_json_object(src, context="settings_merge_bundled")
    if bundled is None:
        # Bundled source is itself invalid/non-object: refuse to clobber the
        # user's settings from a broken template. Structural reason only.
        result["errors"].append(f"Skipped settings.json merge: bundled template invalid or non-object: {dest}")
        return

    existing = read_json_object(dest, context="settings_merge_existing")
    if existing is None:
        # Unreadable / corrupt / non-object existing file: recover by copying the
        # (valid) bundled template, mirroring the prior fallback semantics.
        logger.warning("settings_json_merge_fallback", path=str(dest), reason="unreadable_or_non_object")
        _parent._update_or_report(src, dest, result, dry_run)
        return

    # Merge env block: add missing keys, preserve existing values. Guard the
    # nested types so a hand-edited non-object ``env``/``hooks`` is preserved
    # rather than crashing the merge.
    bundled_env = bundled.get("env", {})
    existing_env = existing.get("env", {})
    if isinstance(bundled_env, dict) and isinstance(existing_env, dict):
        for key, value in bundled_env.items():
            existing_env.setdefault(key, value)
        existing["env"] = existing_env

    # Merge hooks per ENTRY, not per event (PRD-SEC-013 FR09). The previous
    # ``existing_hooks.setdefault(hook_event, hook_list)`` only inserted when the
    # EVENT KEY was entirely absent, so any project that already had, say, one
    # PreToolUse entry silently lost the whole bundled PreToolUse list — including
    # newly bundled hooks. Identity is the entry's hook command(s), which are
    # unique per hook script in this repo's convention; the merge is additive and
    # idempotent, and never rewrites or reorders an existing entry.
    bundled_hooks = bundled.get("hooks", {})
    existing_hooks = existing.get("hooks", {})
    if isinstance(bundled_hooks, dict) and isinstance(existing_hooks, dict):
        for hook_event, hook_list in bundled_hooks.items():
            existing_list = existing_hooks.get(hook_event)
            if not isinstance(existing_list, list):
                existing_hooks[hook_event] = hook_list
                continue
            if not isinstance(hook_list, list):
                continue
            known = {_hook_entry_identity(entry) for entry in existing_list}
            for entry in hook_list:
                identity = _hook_entry_identity(entry)
                if identity not in known:
                    existing_list.append(entry)
                    known.add(identity)
        existing["hooks"] = existing_hooks

    # No-op detection (aligns with _update_or_report's _files_identical): when
    # the merge output is byte-identical to what is already on disk there is
    # nothing to change — report ``preserved`` and skip, so both dry-run and
    # real runs stop claiming "would merge"/"updated" on an unchanged file.
    merged_text = json.dumps(existing, indent=2) + "\n"
    try:
        current_text: str | None = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        current_text = None
    if current_text == merged_text:
        result.setdefault("preserved", []).append(str(dest))
        return

    if dry_run:
        result["updated"].append(f"would merge: {dest}")
        return

    try:
        dest.write_text(merged_text, encoding="utf-8")
        result["updated"].append(str(dest))
    except OSError:
        # Structural reason only — never echo the raw exception text.
        result["errors"].append(f"Failed to write merged settings.json: {dest}")
