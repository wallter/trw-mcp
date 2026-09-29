"""Hook-config strip strategies for lifecycle uninstall.

Holds the strip strategies for hook-registration files (the grouped codex,
copilot and ``.claude/settings.json`` shapes, the flat cursor and antigravity
shapes, the legacy CLAUDE.md block) plus the leaf helpers they share with the
server-map strategies: the ``QUIET`` flag, ``_runtime_logger`` and
``_entry_changed``. Split out of ``_subcommands_uninstall_config`` for the
effective-LOC ratchet; every name is re-exported there. This module is the
leaf -- it imports nothing from its parent, so the strategy dispatch table in
the parent can reference these functions without an import cycle.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.bootstrap._generated_entries import (
    flat_hook_entries,
    grouped_hook_entries,
    hook_file_rest,
)
from trw_mcp.bootstrap._user_file_edit import (
    drop_matching_flat_hook_entries,
    drop_matching_hook_commands,
    is_generated_entry,
    matching_sort_keys,
)
from trw_mcp.bootstrap._utils import _DATA_DIR

# Set while a listing-only match check runs, so the real pass that follows is the one to warn.
QUIET: ContextVar[bool] = ContextVar("QUIET", default=False)


def _runtime_logger() -> Any:
    """Return a fresh logger so structlog test capture sees late-bound events."""
    return structlog.ReturnLogger() if QUIET.get() else structlog.get_logger(__name__)


def _entry_changed(file_label: str, what: object) -> None:
    """Say why TRW's entry was left in place: it no longer equals what TRW generates."""
    _runtime_logger().warning("uninstall_entry_changed", path=file_label, entry=what, action="left_untouched")


def _hook_commands(node: object) -> Iterator[str]:
    """Every ``command`` string anywhere in a hooks document."""
    if isinstance(node, dict):
        if isinstance(node.get("command"), str):
            yield node["command"]
        for value in node.values():
            yield from _hook_commands(value)
    elif isinstance(node, list):
        for item in node:
            yield from _hook_commands(item)


def _warn_kept_trw_commands(kept: object, generated: dict[str, list[object]], file_label: str) -> None:
    """Warn about a TRW hook command left in place because its entry was edited."""
    ours = set(_hook_commands(generated))
    left = sorted({command for command in _hook_commands(kept) if command in ours})
    if left:
        _entry_changed(file_label, left)


def _strip_grouped_hooks(
    raw: str, shape: str, file_label: str, *, deletable: bool = True, strip_env: bool = False
) -> tuple[bool, str, bool]:
    """Strip only the hook dicts TRW generates from a grouped ``{"hooks": {event: [group]}}`` file.

    A hook goes only when it equals, in full, a hook TRW generates for that
    event, inside a group whose own fields (``matcher``, ``description``) are
    TRW's too; a user's hook appended into a TRW group stays, and so does a TRW
    hook the user gave a ``timeout``. Shared by ``.codex/hooks.json``, the
    Copilot hooks file and ``.claude/settings.json``, via the same
    :func:`trw_mcp.bootstrap._user_file_edit.drop_matching_hook_commands` the
    per-script tombstone path uses. Rewrites only a file already in TRW's
    canonical formatting. With *strip_env* (``.claude/settings.json``) the
    template's ``env`` values are withdrawn too and emptied ``env``/``hooks``
    keys are dropped.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    hooks = data.get("hooks")
    new_env, env_changed = _drop_template_env(data.get("env")) if strip_env else (data.get("env"), False)
    # A non-dict ``hooks`` is the user's (malformed) content: never rewrite or drop it; only env is processed.
    hooks_malformed = not isinstance(hooks, dict)
    if not isinstance(hooks, dict):
        if not env_changed:
            return False, raw, False
        hooks = {}
    trw_hooks, trw_groups = grouped_hook_entries(shape)

    def _is_trw_hook(event: str, hook: dict[str, object]) -> bool:
        return is_generated_entry(hook, trw_hooks.get(event, []))

    def _is_trw_group(event: str, group: object) -> bool:
        fields = {k: v for k, v in group.items() if k != "hooks"} if isinstance(group, dict) else group
        return is_generated_entry(fields, trw_groups.get(event, []))

    new_hooks, changed = drop_matching_hook_commands(hooks, _is_trw_hook, file_label, {}, is_trw_group=_is_trw_group)
    _warn_kept_trw_commands(new_hooks, trw_hooks, file_label)
    if not changed and not env_changed:
        return False, raw, False
    new_data = dict(data)
    if not hooks_malformed and ("hooks" in data or new_hooks):
        new_data["hooks"] = new_hooks
    if strip_env:
        if env_changed:
            new_data["env"] = new_env
        for key in ("env", "hooks"):
            if key == "hooks" and hooks_malformed:
                continue
            if key in new_data and new_data[key] == {}:
                del new_data[key]
    return _canonical_or_untouched(
        data, raw, new_data, empty_key="hooks", file_label=file_label, shape=shape if deletable else ""
    )


def _drop_template_env(env: object) -> tuple[object, bool]:
    """Remove from *env* each key whose value equals the bundled template's ``env`` value.

    A differing value is the user's choice (e.g. the ``ENABLE_TOOL_SEARCH``
    opt-out ``"false"``) and stays, as does every key the template lacks.
    Known limit: a user who set the same value by hand is indistinguishable
    from TRW's write, so that key is removed.
    """
    if not isinstance(env, dict):
        return env, False
    template = json.loads((_DATA_DIR / "settings.json").read_text(encoding="utf-8")).get("env", {})
    kept = {k: v for k, v in env.items() if not (k in template and v == template[k])}
    return kept, len(kept) != len(env)


def _strip_codex_hook_groups(raw: str, _root: Path) -> tuple[bool, str, bool]:
    return _strip_grouped_hooks(raw, "codex-hook-group-list", ".codex/hooks.json")


def _strip_copilot_hook_groups(raw: str, _root: Path) -> tuple[bool, str, bool]:
    return _strip_grouped_hooks(raw, "copilot-hook-group-list", ".github/hooks/hooks.json")


def _canonical_or_untouched(
    data: dict[str, Any],
    raw: str,
    new_data: dict[str, Any],
    *,
    empty_key: str,
    file_label: str,
    shape: str = "",
) -> tuple[bool, str, bool]:
    """Render *new_data* only when *raw* is TRW's canonical form of *data*.

    Shared tail for every JSON hook-map strip below. The canonical check runs
    FIRST and unconditionally — including on the path that would otherwise
    delete the file — so a hand-formatted file whose only content happens to
    be TRW's own is left byte-identical-with-a-warning rather than deleted
    without ever having proven its bytes matched what TRW wrote (PRD-INFRA-192
    FR09/FR10 P0 round 2: the delete branch used to run BEFORE this check,
    unconditionally deleting an all-TRW file regardless of formatting). Once
    canonical, the file is deleted only when every hook in it was TRW's AND
    everything else in it (``version`` included) equals the file TRW's writer
    generates for *shape*; otherwise it is kept with an empty hooks map. With no
    *shape* (``.claude/settings.json``, the user's own client config) it is never
    deleted, only emptied.
    """
    sort_keys = matching_sort_keys(data, raw)
    if sort_keys is None:
        _runtime_logger().warning("uninstall_merged_config_custom_formatting", path=file_label, action="left_untouched")
        return False, raw, False
    rest = {k: v for k, v in data.items() if k != empty_key}
    if shape and not new_data.get(empty_key) and is_generated_entry(rest, [hook_file_rest(shape)]):
        return True, "", True
    return True, json.dumps(new_data, indent=2, sort_keys=sort_keys) + "\n", False


def _strip_trw_claude_settings(raw: str, _root: Path) -> tuple[bool, str, bool]:
    """Withdraw TRW hook registrations from ``.claude/settings.json``.

    Shape: ``{"hooks": {<event>: [{"matcher": ..., "hooks": [{"command": ...}]}]}}``
    alongside user-owned ``env``/``permissions``/etc. -- the same group shape as
    codex/copilot, checked against the bundled ``settings.json`` template.

    Withdraws hook registrations and the ``env`` values TRW's template seeds
    (see :func:`_drop_template_env`). The file is never deleted -- it is the
    user's client config, emptied to ``{}`` at most.
    """
    return _strip_grouped_hooks(raw, "claude-settings", ".claude/settings.json", deletable=False, strip_env=True)


def _strip_trw_cursor_hooks(raw: str, _root: Path) -> tuple[bool, str, bool]:
    """Strip TRW hook entries from ``.cursor/hooks.json``.

    Shape ``{"version": 1, "hooks": {event: [{"command": ...}]}}``. An entry
    goes only when it equals, in full, one cursor-ide or cursor-cli generates
    for that event; one the user retimed stays. Event keys that were already
    empty are kept.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False, raw, False
    ours = flat_hook_entries("cursor-hook-list")
    new_hooks, changed = drop_matching_flat_hook_entries(hooks, lambda ev, e: is_generated_entry(e, ours.get(ev, [])))
    _warn_kept_trw_commands(new_hooks, ours, ".cursor/hooks.json")
    if not changed:
        return False, raw, False
    new_data = dict(data)
    new_data["hooks"] = new_hooks
    return _canonical_or_untouched(
        data, raw, new_data, empty_key="hooks", file_label=".cursor/hooks.json", shape="cursor-hook-list"
    )


def _strip_trw_antigravity_hooks(raw: str, _root: Path) -> tuple[bool, str, bool]:
    """Strip TRW hook entries from the FLAT ``.antigravitycli/hooks.json``.

    Shape ``{event: [{"matcher": ..., "command": ...}]}`` — no ``hooks``
    wrapper (channels/antigravity/_before_edit_hook.py::_merge_hooks_json).
    That merger does ``dict(existing)`` and preserves every other event key, so
    the file is a merged config, not a TRW-only artifact. The entry goes only
    when it equals, in full, the one AG-03 generates.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    ours = flat_hook_entries("antigravity-hook-map")
    new_data, changed = drop_matching_flat_hook_entries(data, lambda ev, e: is_generated_entry(e, ours.get(ev, [])))
    _warn_kept_trw_commands(new_data, ours, ".antigravitycli/hooks.json")
    if not changed:
        return False, raw, False
    # PRD-INFRA-192 FR09/FR10 P0 round 2: the canonical check runs BEFORE the
    # delete decision -- an all-TRW file is deleted only when its bytes are
    # already proven to be TRW's own canonical form, never unconditionally.
    sort_keys = matching_sort_keys(data, raw)
    if sort_keys is None:
        _runtime_logger().warning(
            "uninstall_merged_config_custom_formatting", path=".antigravitycli/hooks.json", action="left_untouched"
        )
        return False, raw, False
    if not new_data:
        return True, "", True
    return True, json.dumps(new_data, indent=2, sort_keys=sort_keys) + "\n", False


def _strip_legacy_claude_md(raw: str, _root: Path) -> tuple[bool, str, bool]:
    from trw_mcp.state.claude_md._orphan_strip import strip_legacy_claude_md

    return strip_legacy_claude_md(raw)
