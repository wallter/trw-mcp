"""``settings.json`` smart-merge helpers.

Belongs to the ``_template_updater.py`` facade. Re-exported there for
back-compat with callers/tests that import via
``trw_mcp.bootstrap._template_updater``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file

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


#: Claude Code's own default hook timeout, in seconds. A TRW hook's timeout above it can only be the legacy value that
#: was written in milliseconds (5000 = 83 min); E2E-HOOK-TIMEOUT-UNITS.
_LEGACY_TIMEOUT_FLOOR = 600
#: The millisecond-style values TRW itself used to ship. One is below the floor (``user-prompt-submit.sh`` at 500 =
#: 8.3 min on every prompt), so it read as a user's choice and survived the migration (FB-INSTALL-04). Matched by
#: TRW's own command, so a user's hook at the same number is never touched.
_LEGACY_SHIPPED_TIMEOUTS = frozenset({500, 3000, 5000, 10000})


def _migrate_legacy_timeouts(
    existing_list: list[object],
    bundled_list: list[object],
    *,
    root: Path | None = None,
    bundled_hooks: Path | None = None,
    result: dict[str, list[str]] | None = None,
) -> int:
    """Give a TRW hook (matched by command) the bundled timeout when its own is a legacy millisecond value.

    The only in-place rewrite the merge does: a value at or below 600 s is the user's choice and is kept, except
    the exact values TRW itself shipped (:data:`_LEGACY_SHIPPED_TIMEOUTS`). A hook whose file the update kept
    (given *root* and the *bundled_hooks* directory) keeps its registration too, and *result* says so. Returns how many it rewrote.
    """
    from ._kept_hook_registration import hook_file_kept, hook_name_of, note_registration_left

    changed = 0
    bundled_timeouts = {
        str(hook.get("command")): hook["timeout"]
        for entry in bundled_list
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list)
        for hook in entry["hooks"]
        if isinstance(hook, dict) and isinstance(hook.get("timeout"), int)
    }
    for entry in existing_list:
        if not (isinstance(entry, dict) and isinstance(entry.get("hooks"), list)):
            continue
        for hook in entry["hooks"]:
            if not isinstance(hook, dict):
                continue
            timeout, command = hook.get("timeout"), str(hook.get("command"))
            if (
                isinstance(timeout, int)
                and (timeout > _LEGACY_TIMEOUT_FLOOR or timeout in _LEGACY_SHIPPED_TIMEOUTS)
                and command in bundled_timeouts
            ):
                name = hook_name_of(command)
                if (
                    root is not None
                    and bundled_hooks is not None
                    and name
                    and hook_file_kept(root, name, (bundled_hooks,))
                ):
                    if result is not None:
                        note_registration_left(result, name)
                    continue
                hook["timeout"] = bundled_timeouts[command]
                changed += 1
    return changed


def _merge_settings_json(
    src: Path,
    dest: Path,
    result: dict[str, list[str]],
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
        _parent._update_or_report(src, dest, result)
        return

    bundled = read_json_object(src, context="settings_merge_bundled")
    if bundled is None:
        # Bundled source is itself invalid/non-object: refuse to clobber the
        # user's settings from a broken template. Structural reason only.
        result["errors"].append(f"Skipped settings.json merge: bundled template invalid or non-object: {dest}")
        return

    existing = read_json_object(dest, context="settings_merge_existing")
    if existing is None:
        # Unreadable / corrupt / non-object existing file: TRW cannot prove the
        # bytes on disk are its own, so it must not replace them with the
        # bundled template (that would discard whatever the user had there).
        # Leave the file byte-identical and surface the problem loudly — an
        # `errors` entry rolls the whole update-project transaction back
        # (PRD-INFRA-192 FR10), which is the correct tradeoff: better to block
        # an update than to silently destroy a user's settings.json.
        logger.warning("settings_json_merge_unreadable", path=str(dest), reason="unreadable_or_non_object")
        result["errors"].append(
            f"{dest} could not be parsed as a JSON object; TRW left it unchanged "
            "instead of replacing it with the bundled template. Fix or remove the "
            "file, then re-run update-project."
        )
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
    # idempotent, and never reorders an existing entry; the one rewrite is a TRW hook's legacy millisecond timeout.
    retimed = 0
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
            retimed += _migrate_legacy_timeouts(
                existing_list, hook_list, root=dest.parents[1], bundled_hooks=src.parent / "hooks", result=result
            )
            known = {_hook_entry_identity(entry) for entry in existing_list}
            for entry in hook_list:
                identity = _hook_entry_identity(entry)
                if identity not in known:
                    existing_list.append(entry)
                    known.add(identity)
        existing["hooks"] = existing_hooks
    if retimed:
        result.setdefault("notes", []).append(
            f".claude/settings.json: set {retimed} legacy hook timeout(s) to TRW's bundled value"
        )

    # statusLine is a single object, not a hook list: ownership (PRD-CORE-354 FR06)
    # decides whether TRW may add, rewrite or remove it.
    root = dest.parents[1]
    from ._kept_hook_registration import hook_file_kept

    note = _reconcile_statusline(
        existing,
        bundled.get("statusLine"),
        statusline_enabled(root),
        shadowed=statusline_shadowed(root),
        script_kept=hook_file_kept(root, "statusline.sh", (src.parent / "hooks",)),
        result=result,
    )
    if note:
        result.setdefault("notes", []).append(note)

    # No-op detection (aligns with _update_or_report's _files_identical): when
    # the merge output is byte-identical to what is already on disk there is
    # nothing to write (PRD-INFRA-190 FR03).
    merged_text = json.dumps(existing, indent=2) + "\n"
    try:
        current_text: str | None = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        current_text = None
    if current_text == merged_text:
        return

    try:
        # dest is <project>/.claude/settings.json; the project is the root the write walks from.
        write_checkout_file(dest.parents[1], dest, merged_text)
    except (OSError, UnsafeWriteError):
        # Structural reason only — never echo the raw exception text.
        result["errors"].append(f"Failed to write merged settings.json: {dest}")


def _set_hook_registration(
    settings: Path, event: str, entry: dict[str, object], *, present: bool, keep_existing: bool = False
) -> bool:
    """Add (``present``) or remove one hook entry in ``settings``; ``True`` when the file changed.

    ``keep_existing`` leaves an entry already registered exactly as it is (its hook file was kept); a missing one
    is still added.

    Identity is :func:`_hook_entry_identity`, the same key the merge uses, so the
    entry is never duplicated and a user's other entries for the event are untouched.
    An unreadable or non-object settings file is left alone.
    """
    data = read_json_object(settings, context="hook_registration")
    hooks = data.get("hooks", {}) if data is not None else None
    if data is None or not isinstance(hooks, dict) or not isinstance(hooks.get(event, []), list):
        return False
    entries: list[object] = hooks.get(event, [])
    identity = _hook_entry_identity(entry)
    if present and keep_existing and any(_hook_entry_identity(e) == identity for e in entries):
        return False
    wanted = [e for e in entries if _hook_entry_identity(e) != identity] + ([entry] if present else [])
    if wanted == entries:
        return False
    if wanted:
        hooks[event] = wanted
    else:
        hooks.pop(event, None)
    data["hooks"] = hooks
    write_checkout_file(settings.parents[1], settings, json.dumps(data, indent=2) + "\n")
    return True


# PRD-CORE-354 FR06: a ``statusLine`` is TRW-owned only when its command runs the
# project-relative script (``$CLAUDE_PROJECT_DIR/.claude/hooks/statusline.sh``, optionally
# braced/quoted and prefixed ``sh``/``bash``), followed only by flag-like (``-x``) arguments:
# ``... && my-thing`` is a user's own command. A user's own ``~/.claude/hooks/statusline.sh`` is not.
STATUSLINE_CONFIG_KEY = "claude_code_statusline"
_TRW_STATUSLINE_RE = re.compile(
    r"""^\s*(?:(?:sh|bash)\s+)?["']?\$\{?CLAUDE_PROJECT_DIR\}?/\.claude/hooks/statusline\.sh["']?(?:(?:[ \t]+-[^\s;&|<>`$()]*)*)[ \t]*$""",
    re.DOTALL,
)


def is_trw_statusline(value: object) -> bool:
    """``True`` when *value* is a ``statusLine`` object whose command runs TRW's script."""
    command = value.get("command") if isinstance(value, dict) else None
    return isinstance(command, str) and _TRW_STATUSLINE_RE.match(command) is not None


def _user_home() -> Path:
    return Path.home()


def statusline_shadowed(root: Path) -> bool:
    """``True`` when a non-TRW ``statusLine`` exists in a scope project settings would override.

    Project ``.claude/settings.json`` outranks ``~/.claude/settings.json``, so adding TRW's
    entry would hide the user's own; ``.claude/settings.local.json`` outranks the project file.
    """
    for path in (_user_home() / ".claude" / "settings.json", root / ".claude" / "settings.local.json"):
        data = read_json_object(path, context="statusline_shadow")
        value = data.get("statusLine") if data is not None else None
        if value is not None and not is_trw_statusline(value):
            return True
    return False


def statusline_enabled(root: Path) -> bool | None:
    """Three-state switch: ``claude_code_statusline`` in ``.trw/config.yaml``.

    ``True`` adds/rewrites TRW's statusLine, ``False`` removes a TRW-owned one, and
    ``None`` (absent, non-boolean or unreadable) leaves things as they are: the
    installer does not install the trw-ui mod, so silently removing an existing
    display would leave users with none. Read ad hoc (top-level key, owned outside
    ``TRWConfig`` like ``cc03_hook_enabled``).
    """
    try:
        import yaml

        raw = yaml.safe_load((root / ".trw" / "config.yaml").read_text(encoding="utf-8"))
    except Exception:  # trw-fail-silent-allow: a bad config must not block install; None = leave the statusLine as is
        return None
    value = raw.get(STATUSLINE_CONFIG_KEY) if isinstance(raw, dict) else None
    return value if isinstance(value, bool) else None


def _reconcile_statusline(
    data: dict[str, object],
    bundled: object,
    enabled: bool | None,
    *,
    shadowed: bool = False,
    script_kept: bool = False,
    result: dict[str, list[str]] | None = None,
) -> str:
    """Apply the FR06 ownership rule to ``data["statusLine"]`` in place; returns a note when TRW removes its own.

    ``script_kept``: the update kept an edited ``statusline.sh``, so an existing TRW statusLine object (which may
    carry the user's flags for that script) is not refreshed; the opt-out removals above it are unchanged.
    """
    current = data.get("statusLine")
    if current is not None and not is_trw_statusline(current):
        return ""  # the user's own statusLine is never touched
    refresh = enabled is None or (enabled and not shadowed)
    if script_kept and refresh and current is not None and is_trw_statusline(bundled):
        if current != bundled and result is not None:
            from ._kept_hook_registration import note_registration_left

            note_registration_left(result, "statusline.sh")
        return ""
    if enabled is None:
        # Absent key: never add, never remove; only bring an existing TRW entry current.
        if current is not None and is_trw_statusline(bundled):
            data["statusLine"] = bundled
    elif not enabled:
        if current is not None:
            data.pop("statusLine")
            return f"removed TRW statusLine: {STATUSLINE_CONFIG_KEY} is false"
    elif shadowed:
        # A user/local statusLine must stay visible: withdraw only an entry TRW itself wrote.
        if current is not None:
            data.pop("statusLine")
            return "removed TRW statusLine: a user-level or local statusLine takes precedence"
    elif is_trw_statusline(bundled):
        data["statusLine"] = bundled
    return ""


def apply_statusline_registration(target_dir: Path, result: dict[str, list[str]] | None = None) -> bool:
    """Idempotently reconcile ``.claude/settings.json``'s statusLine; ``True`` when it changed.

    Covers what the merge cannot: a fresh whole-template copy under opt-out, and
    the re-apply after the uncommitted-changes guard (same reason as CC-03).
    """
    from ._kept_hook_registration import hook_file_kept

    settings = target_dir / ".claude" / "settings.json"
    data = read_json_object(settings, context="statusline_registration")
    bundled = read_json_object(_data_dir() / "settings.json", context="statusline_bundled")
    if data is None or bundled is None:
        return False
    before = json.dumps(data, sort_keys=True)
    _reconcile_statusline(
        data,
        bundled.get("statusLine"),
        statusline_enabled(target_dir),
        shadowed=statusline_shadowed(target_dir),
        script_kept=hook_file_kept(target_dir, "statusline.sh", (_data_dir() / "hooks",)),
        result=result,
    )
    if json.dumps(data, sort_keys=True) == before:
        return False
    write_checkout_file(target_dir, settings, json.dumps(data, indent=2) + "\n")
    return True


def _data_dir() -> Path:
    from ._utils import _DATA_DIR

    return Path(_DATA_DIR)
