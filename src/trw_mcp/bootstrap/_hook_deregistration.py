"""Deregister dangling hook entries for a tombstoned hook script (PRD-INFRA-192 FR10).

Deleting a hook script is not enough: a registration file that still invokes
it makes the client call a missing script. This module removes, from every
registration surface TRW knows about, the entries whose command references a
given (now-deleted) hook script path — but ONLY the piece TRW itself would
have written for that script. TRW never removes a byte it can't prove it
wrote this run or that is unchanged from what it recorded; a registration the
USER hand-added is not TRW's to remove even when it references the same dead
script, so it is left alone and reported via ``result["warnings"]`` instead.

``.claude/settings.json`` entries carry exactly one script's identity per
entry (:func:`_hook_entry_identity`'s convention), so a whole-entry drop is
safe there. ``.codex/hooks.json`` and the Copilot hooks file instead group
several hook *commands* under one matcher entry — a user can (and does, per
PRD-INFRA-192 FR10 P1-a) append their own hook command into a TRW-managed
group. Dropping the whole group there would delete the user's co-located
hook too, so those two surfaces remove only the single matching hook command
and drop the group only once it has none left.

Byte preservation (P1-b): a registration file is rewritten only when its
current bytes already equal what TRW's own writer would produce for the
parsed content — i.e. the file is in TRW's canonical form. A hand-reformatted
or hand-edited file is left byte-identical with a warning instead, because
rewriting it in canonical form would silently reformat bytes TRW never wrote.
An event key whose list is empty is only ever dropped when OUR removal made
it empty; a key that was already empty, or still holds entries, is preserved.

Belongs to the ``_tombstones.py`` enforcement pass. Kept as its own module so
that facade — and the 350 effective-LOC-gated ``_update_project.py`` /
``_init_project.py`` — never has to grow to hold this logic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from ._file_ops import read_json_object

# (event, whole matcher-group entry, tombstoned script's manifest-relative path)
_TrwEntryPredicate = Callable[[str, object, str], bool]
# (event, one hook COMMAND dict inside an entry's "hooks" list, tombstoned path)
_TrwHookCommandPredicate = Callable[[str, dict[str, object], str], bool]
_DropFn = Callable[
    [dict[str, object], "re.Pattern[str]", str, str, dict[str, list[str]]],
    tuple[dict[str, object], bool],
]


def _is_trw_settings_entry(_event: str, entry: object, rel_script_path: str) -> bool:
    from ._settings_merge import _hook_entry_identity

    script_name = rel_script_path.rsplit("/", 1)[-1]
    canonical_command = f'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/{script_name}"'
    return _hook_entry_identity(entry) == canonical_command


def _codex_canonical_command(event: str, rel_script_path: str) -> str | None:
    """The exact command string ``_codex_hooks_payload`` would write for this script/event."""
    from ._codex_hooks import _codex_hooks_payload

    script_name = rel_script_path.rsplit("/", 1)[-1]
    for group in _codex_hooks_payload()["hooks"].get(event, []):
        for hook in group.get("hooks", []):
            command = hook.get("command")
            if isinstance(command, str) and script_name in command:
                return command
    return None


def _is_trw_codex_hook_command(event: str, hook: dict[str, object], rel_script_path: str) -> bool:
    canonical = _codex_canonical_command(event, rel_script_path)
    return canonical is not None and hook.get("command") == canonical


def _copilot_canonical_command(event: str, rel_script_path: str) -> str | None:
    """The exact command string ``_copilot_hooks_payload`` would write for this script/event."""
    from ._copilot import _copilot_hooks_payload

    script_name = rel_script_path.rsplit("/", 1)[-1]
    for group in _copilot_hooks_payload()["hooks"].get(event, []):
        for hook in group.get("hooks", []):
            command = hook.get("command")
            if isinstance(command, str) and script_name in command:
                return command
    return None


def _is_trw_copilot_hook_command(event: str, hook: dict[str, object], rel_script_path: str) -> bool:
    canonical = _copilot_canonical_command(event, rel_script_path)
    return canonical is not None and hook.get("command") == canonical


def _script_path_pattern(rel_script_path: str) -> re.Pattern[str]:
    """Match *rel_script_path* as a whole path token inside a command string.

    Anchored on the full manifest-relative path (e.g.
    ``.claude/hooks/session-start.sh``): a differently named script that
    merely ends the same way (``my-session-start.sh``) never contains this
    substring, so it can never match. A negative lookahead boundary on the
    tail keeps ``session-start.sh.bak`` from matching ``session-start.sh``.
    """
    return re.compile(re.escape(rel_script_path) + r"(?![\w.\-])")


def _entry_references(entry: object, pattern: re.Pattern[str]) -> bool:
    """True if any command string in one hooks.json/settings.json entry matches *pattern*."""
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if isinstance(hook, dict):
            command = hook.get("command")
            if isinstance(command, str) and pattern.search(command):
                return True
    return False


def _prune_empty_event_keys(
    hooks_by_event: dict[str, object],
    new_lists: dict[str, list[object]],
) -> dict[str, object]:
    """Merge pruned per-event lists back in, honoring the P1-b empty-key rule.

    An event key is dropped only when its list was non-empty before this pass
    and became empty because of a removal in this pass. A key that was
    already empty, or that still holds entries, is always preserved.
    """
    merged: dict[str, object] = {}
    for event, original in hooks_by_event.items():
        if not isinstance(original, list):
            merged[event] = original
            continue
        pruned = new_lists.get(event, [])
        if pruned or not original:
            merged[event] = pruned
        # else: originally non-empty, emptied by our own removal -> drop key
    return merged


def _drop_matching_entries(
    hooks_by_event: dict[str, object],
    pattern: re.Pattern[str],
    is_trw_entry: _TrwEntryPredicate,
    rel_script_path: str,
    file_label: str,
    result: dict[str, list[str]],
) -> tuple[dict[str, object], bool]:
    """Remove every TRW-owned whole ENTRY matching *pattern*; warn (and keep) a user-owned one.

    Used for ``.claude/settings.json``, where one entry carries exactly one
    script's identity.
    """
    changed = False
    new_lists: dict[str, list[object]] = {}
    for event, entries in hooks_by_event.items():
        if not isinstance(entries, list):
            continue
        kept: list[object] = []
        for entry in entries:
            if _entry_references(entry, pattern):
                if is_trw_entry(event, entry, rel_script_path):
                    changed = True
                    continue
                result.setdefault("warnings", []).append(
                    f"{file_label}: a registration you added still references {rel_script_path}, which is deleted"
                )
            kept.append(entry)
        new_lists[event] = kept
    return _prune_empty_event_keys(hooks_by_event, new_lists), changed


def _drop_matching_hook_commands(
    hooks_by_event: dict[str, object],
    pattern: re.Pattern[str],
    is_trw_hook_command: _TrwHookCommandPredicate,
    rel_script_path: str,
    file_label: str,
    result: dict[str, list[str]],
) -> tuple[dict[str, object], bool]:
    """Remove only the TRW-owned hook COMMAND referencing *rel_script_path* within each group.

    Used for ``.codex/hooks.json`` and the Copilot hooks file, where a single
    matcher group can hold several hook commands (PRD-INFRA-192 FR10 P1-a): a
    user's own hook appended into a TRW-managed group must survive even when
    the TRW hook in the same group is removed. A group is dropped only once
    none of its hook commands remain.
    """
    changed = False
    new_lists: dict[str, list[object]] = {}
    for event, groups in hooks_by_event.items():
        if not isinstance(groups, list):
            continue
        kept_groups: list[object] = []
        for group in groups:
            if not isinstance(group, dict) or not _entry_references(group, pattern):
                kept_groups.append(group)
                continue
            hooks = group.get("hooks")
            if not isinstance(hooks, list):
                kept_groups.append(group)
                continue
            kept_hooks: list[object] = []
            group_changed = False
            for hook in hooks:
                command = hook.get("command") if isinstance(hook, dict) else None
                if isinstance(hook, dict) and isinstance(command, str) and pattern.search(command):
                    if is_trw_hook_command(event, hook, rel_script_path):
                        group_changed = True
                        continue
                    result.setdefault("warnings", []).append(
                        f"{file_label}: a hook you added still references {rel_script_path}, which is deleted"
                    )
                kept_hooks.append(hook)
            if not group_changed:
                kept_groups.append(group)
                continue
            changed = True
            if kept_hooks:
                new_group = dict(group)
                new_group["hooks"] = kept_hooks
                kept_groups.append(new_group)
            # else: no hooks left in this group -> the whole group is dropped
        new_lists[event] = kept_groups
    return _prune_empty_event_keys(hooks_by_event, new_lists), changed


def _drop_settings_entries(
    hooks_by_event: dict[str, object],
    pattern: re.Pattern[str],
    rel_script_path: str,
    file_label: str,
    result: dict[str, list[str]],
) -> tuple[dict[str, object], bool]:
    return _drop_matching_entries(hooks_by_event, pattern, _is_trw_settings_entry, rel_script_path, file_label, result)


def _drop_codex_hook_commands(
    hooks_by_event: dict[str, object],
    pattern: re.Pattern[str],
    rel_script_path: str,
    file_label: str,
    result: dict[str, list[str]],
) -> tuple[dict[str, object], bool]:
    return _drop_matching_hook_commands(
        hooks_by_event, pattern, _is_trw_codex_hook_command, rel_script_path, file_label, result
    )


def _drop_copilot_hook_commands(
    hooks_by_event: dict[str, object],
    pattern: re.Pattern[str],
    rel_script_path: str,
    file_label: str,
    result: dict[str, list[str]],
) -> tuple[dict[str, object], bool]:
    return _drop_matching_hook_commands(
        hooks_by_event, pattern, _is_trw_copilot_hook_command, rel_script_path, file_label, result
    )


# (registration file, drop function that knows how granular removal must be
# for that file's shape).
_REGISTRATION_FILES: tuple[tuple[str, _DropFn], ...] = (
    (".claude/settings.json", _drop_settings_entries),
    (".codex/hooks.json", _drop_codex_hook_commands),
    (".github/hooks/hooks.json", _drop_copilot_hook_commands),
)

# ``json.dumps(..., indent=2, sort_keys=...)`` conventions actually observed
# across this repo's own registration-file writers. Checked in order and the
# first exact match is reused for the rewrite, so re-writing the file never
# produces a spurious formatting-only diff against whichever TRW writer last
# touched it. More than one convention exists for a single file — e.g.
# ``.codex/hooks.json`` is written with ``sort_keys=True`` by
# ``generate_codex_hooks`` but with ``sort_keys=False`` by the separate
# trw-distill telemetry-channel merger — so both are accepted as canonical
# rather than picking one and false-flagging the other writer's own output
# as "user-edited".
_KNOWN_SORT_KEYS_CONVENTIONS: tuple[bool, ...] = (True, False)


def _matching_sort_keys(data: object, raw_text: str) -> bool | None:
    """The ``sort_keys`` convention whose serialization of *data* equals *raw_text*, if any.

    Compares against the parsed-and-unmodified document, so this answers "was
    this file, as it stands right now, produced by a TRW writer we know
    about" — independent of whatever we are about to remove from it. ``None``
    means the file does not match any known TRW writer convention (hand
    edited or reformatted).
    """
    for sort_keys in _KNOWN_SORT_KEYS_CONVENTIONS:
        if raw_text == json.dumps(data, indent=2, sort_keys=sort_keys) + "\n":
            return sort_keys
    return None


def _deregister_in_file(
    path: Path,
    root: Path,
    rel_script_path: str,
    drop_fn: _DropFn,
    result: dict[str, list[str]],
) -> None:
    if not path.is_file():
        return
    from ._user_file_edit import guard_refusal

    refusal = guard_refusal(path, root)
    if refusal:
        # A symlinked registration file (or a symlinked parent component)
        # would read/write through to bytes outside the project the same way
        # a symlinked delete would (PRD-INFRA-192 FR10 P0). Never follow it.
        result.setdefault("warnings", []).append(
            f"{path}: left untouched while removing the dangling registration for {rel_script_path} ({refusal})"
        )
        return
    try:
        # Raw bytes, not ``read_text`` -- avoids universal-newline translation
        # so a CRLF file's canonical-form comparison below compares against
        # its ACTUAL on-disk bytes, not a silently LF-ified copy of them.
        raw_text: str | None = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raw_text = None
    data = read_json_object(path, context="hook_deregistration")
    if data is None:
        result.setdefault("warnings", []).append(
            f"Could not parse {path} to remove its now-deleted hook registration for "
            f"{rel_script_path}; left the file unchanged."
        )
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    new_hooks, changed = drop_fn(hooks, _script_path_pattern(rel_script_path), rel_script_path, str(path), result)
    if not changed:
        return
    sort_keys = None if raw_text is None else _matching_sort_keys(data, raw_text)
    if sort_keys is None:
        # The file isn't (or can no longer be confirmed to be) in a TRW
        # writer's own canonical form — rewriting it would change bytes TRW
        # never wrote.
        result.setdefault("warnings", []).append(
            f"{path}: a TRW registration for deleted {rel_script_path} remains; the file has custom "
            "formatting, so TRW left it untouched — remove the entry by hand"
        )
        return
    from ._user_file_edit import atomic_write_text

    data["hooks"] = new_hooks
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=sort_keys) + "\n")
    result.setdefault("info", []).append(f"deregistered dangling hook: {path} no longer invokes {rel_script_path}")


def deregister_hook_script(target_dir: Path, rel_script_path: str, result: dict[str, list[str]]) -> None:
    """Remove every TRW-owned registration of *rel_script_path* from every known registration surface.

    Runs unconditionally for a tombstoned hook key, every run — a hook script
    deleted before this enforcement pass existed may already carry a dangling
    registration. A registration the user hand-added is never removed here
    even when it references the same dead script; it is reported instead so
    the user can act on it themselves.
    """
    for rel_path, drop_fn in _REGISTRATION_FILES:
        _deregister_in_file(target_dir / rel_path, target_dir, rel_script_path, drop_fn, result)
