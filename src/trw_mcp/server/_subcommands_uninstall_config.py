"""Managed-block and merged-config cleanup for lifecycle uninstall."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.bootstrap._git_hooks import MARKER_END as _GIT_HOOK_MARKER_END
from trw_mcp.bootstrap._git_hooks import MARKER_START as _GIT_HOOK_MARKER_START
from trw_mcp.bootstrap._opencode_instructions import (
    OPENCODE_INSTRUCTIONS_REL as _OPENCODE_INSTRUCTIONS_REL,
)
from trw_mcp.channels._manifest_models import MARKER_REGISTRY
from trw_mcp.state.claude_md._parser import LEGACY_TRW_MARKER_END, LEGACY_TRW_MARKER_START

# Marker pairs TRW writes that the channel MARKER_REGISTRY does not carry. Each
# is imported from (or names) its producer so the literals cannot drift.
#
# The registry is authoritative for channel segment markers, but it is not a
# complete inventory of every TRW-managed block: the antigravity instruction
# block, the git post-commit shim, and the claude-code distill segment
# (``trw-distill``, hyphenated — a second spelling of the registry's
# ``trw:distill``) are all written outside the channel-manifest layer.
_EXTRA_BLOCK_MARKERS: tuple[tuple[str, str], ...] = (
    ("<!-- trw:antigravity:start -->", "<!-- trw:antigravity:end -->"),
    # PRD-CORE-231 git ``post-commit`` shim. Shell, not markdown, so it uses a
    # ``#``-comment marker pair. Imported from the installer rather than copied
    # so the two literals cannot drift — a drifted marker means an
    # unremovable managed block, which is exactly how this pair was missed.
    (_GIT_HOOK_MARKER_START, _GIT_HOOK_MARKER_END),
    # channels/claude_code/_cc02_segment.py
    ("<!-- trw-distill:start -->", "<!-- trw-distill:end -->"),
    # PRD-CORE-243-FR06/FR08: the retired cursor-cli install-time dialect (an
    # UPPERCASE sentinel pair — a third spelling). No writer emits it anymore
    # -- generate_cursor_cli_agents_md merges into the shared trw:start block
    # and migrates any dead legacy block it finds in place — but a project
    # installed before that fix can still have one on disk, so uninstall must
    # still be able to find and remove it.
    (LEGACY_TRW_MARKER_START, LEGACY_TRW_MARKER_END),
)

# Marker values whose partner is formed by these open -> close substitutions.
_MARKER_BOUNDARY_TOKENS: tuple[tuple[str, str], ...] = (("start", "end"), ("BEGIN", "END"))


def _registry_marker_pairs() -> tuple[tuple[str, str], ...]:
    """Derive ``(start, end)`` marker pairs from the channel MARKER_REGISTRY.

    The registry is a flat ``name -> literal`` map, so pairing is done on the
    LITERALS (``...:start -->`` <-> ``...:end -->``, ``BEGIN`` <-> ``END``) not
    on the key names, which are irregular (``trw_start_generic`` /
    ``trw_end_generic``). Registry insertion order is preserved so the result is
    deterministic.

    This replaces a hand-copied 3-pair subset that had drifted from the
    registry's 7: uninstall was silently leaving ``trw:distill``, ``trw:memory``,
    ``trw:cursor:mdc`` and ``trw:codex`` blocks in user files while printing
    that it had cleaned them.
    """
    values = list(MARKER_REGISTRY.values())
    known = set(values)
    pairs: list[tuple[str, str]] = []
    for value in values:
        for open_token, close_token in _MARKER_BOUNDARY_TOKENS:
            if open_token not in value:
                continue
            partner = value.replace(open_token, close_token)
            if partner in known and (value, partner) not in pairs:
                pairs.append((value, partner))
            break
    return tuple(pairs)


def _managed_block_markers() -> tuple[tuple[str, str], ...]:
    """All marker pairs uninstall strips: registry-derived plus local extras."""
    pairs = list(_registry_marker_pairs())
    pairs.extend(pair for pair in _EXTRA_BLOCK_MARKERS if pair not in pairs)
    return tuple(pairs)


# PRD-SEC-006 FR07: TRW-managed marker block pairs for SHARED files. Uninstall
# strips ONLY the content between these markers (preserving user content) rather
# than deleting the file wholesale.
_MANAGED_BLOCK_MARKERS: tuple[tuple[str, str], ...] = _managed_block_markers()

# TRW server entry key written into merged client config files. JSON
# (the root .mcp.json, .cursor/mcp.json, .antigravitycli/settings.json) nests
# it under ``mcpServers``; TOML (.codex/config.toml) under ``mcp_servers``.
_TRW_SERVER_KEY = "trw"
_JSON_MCP_KEY = "mcpServers"
_TOML_MCP_KEY = "mcp_servers"
# Same server-map structure, different container key per client.
_VSCODE_MCP_KEY = "servers"
_OPENCODE_MCP_KEY = "mcp"

# TRW-managed hook groups in a codex/copilot ``hooks.json`` map carry a
# ``description`` starting with this prefix (bootstrap: _codex_hooks.py /
# _copilot.py). Uninstall strips only groups tagged this way.
_TRW_HOOK_DESCRIPTION_PREFIX = "TRW managed:"

# TRW hook scripts registered in ``.claude/settings.json`` all live under this
# project-relative directory (bundled data/settings.json). The dir is itself an
# uninstall surface removed wholesale, so a command referencing it is TRW's.
_TRW_HOOK_COMMAND_DIR = ".claude/hooks/"

# Command-identity tokens for the two clients whose hook entries are identified
# by the script they invoke rather than by a description tag. Both name a
# directory that is itself a TRW-owned uninstall surface.
_CURSOR_HOOK_COMMAND_ID = ".cursor/hooks/trw-"
_ANTIGRAVITY_HOOK_COMMAND_ID = ".antigravitycli/hooks/"

# The managed instruction file opencode.json points at, taken from the writer
# that produces it so the two cannot drift.
_OPENCODE_INSTRUCTION_ENTRY = _OPENCODE_INSTRUCTIONS_REL.as_posix()


def _runtime_logger() -> Any:
    """Return a fresh logger so structlog test capture sees late-bound events."""
    return structlog.get_logger(__name__)


def _has_unbalanced_marker(text: str) -> bool:
    """True when any start marker appears on its own line with no matching end.

    Line-anchored: a marker mentioned inside prose (substring) does not count.
    Deleting to EOF on an unbalanced marker would destroy user content, so the
    caller leaves the file untouched and warns when this returns True.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    return any(start in lines and end not in lines for start, end in _MANAGED_BLOCK_MARKERS)


def _strip_managed_blocks(text: str) -> str:
    """Remove every line-anchored TRW-managed marker block from *text*.

    A block is only stripped when BOTH its start and end markers appear as
    their own (whitespace-stripped) lines — never as substrings inside prose
    (an earlier substring match risked deleting user content; see the
    ``index_sync._find_marker_line`` precedent). An unbalanced start marker
    (start present, end missing) is left untouched here so the caller can warn
    rather than delete to EOF. Surrounding user lines are preserved and blank
    runs left by removal are collapsed.
    """
    if _has_unbalanced_marker(text):
        # Unbalanced — refuse to strip; the caller decides (warn + preserve).
        return text

    marker_pairs = dict(_MANAGED_BLOCK_MARKERS)
    end_markers = set(marker_pairs.values())
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping_end: str | None = None
    for line in lines:
        stripped = line.strip()
        if skipping_end is not None:
            if stripped == skipping_end:
                skipping_end = None
            continue
        if stripped in marker_pairs:
            skipping_end = marker_pairs[stripped]
            continue
        if stripped in end_markers:
            # Defensive: orphan end line with no preceding start — drop it.
            continue
        out.append(line)
    result = "".join(out)
    # Collapse 3+ consecutive newlines created by removal into a single blank.
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result


def _remove_managed_block_file(path: Path, dry_run: bool) -> str | None:
    """Strip TRW blocks from a shared file; return a status word or None.

    Returns ``"stripped"`` when the TRW block was removed (file preserved),
    ``"removed"`` when stripping left the file empty (file deleted), or ``None``
    when the file has no TRW block (left untouched). On ``dry_run`` the same
    classification is returned without mutating the file.
    """
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if _has_unbalanced_marker(original):
        # A start marker with no matching end. Stripping to EOF would destroy
        # user content; leave the file untouched and surface the anomaly.
        _runtime_logger().warning(
            "uninstall_marker_unbalanced",
            path=str(path),
            action="left_untouched",
        )
        return None
    stripped = _strip_managed_blocks(original)
    if stripped == original:
        return None  # no TRW block present -- leave the user's file alone
    if dry_run:
        return "removed" if not stripped.strip() else "stripped"
    if not stripped.strip():
        path.unlink()
        return "removed"
    path.write_text(stripped, encoding="utf-8")
    return "stripped"


# Suffix -> default shape when a merged_config surface carries no explicit
# ``config_shape`` (backward-compat for surfaces registered before the
# shape-dispatch existed).
_SUFFIX_DEFAULT_SHAPE: dict[str, str] = {".json": "mcp-server-map", ".toml": "codex-toml"}


def _resolve_strip_strategy(shape: str, suffix: str) -> Callable[[str], tuple[bool, str, bool]] | None:
    """Return the strip strategy for a shape, inferring from suffix when unset."""
    resolved = shape or _SUFFIX_DEFAULT_SHAPE.get(suffix, "")
    return _STRIP_STRATEGIES.get(resolved)


def _strip_trw_from_merged_config(path: Path, dry_run: bool, *, shape: str = "") -> str | None:
    """Strip ONLY TRW-owned entries from a merged client config file.

    sec-006: merged client config files (the root ``.mcp.json``,
    ``.codex/config.toml``, ``.codex/hooks.json``, ``.github/hooks/hooks.json``,
    ``.cursor/mcp.json``, ``.antigravitycli/settings.json``) may carry
    user-owned settings/servers/hook groups. They MUST NOT be deleted wholesale
    on uninstall — we parse the file, drop only the TRW-owned entries via the
    structural strategy named by ``shape``, and write the rest back.

    A hook-group file that contains nothing user-owned after stripping is
    deleted (it held only TRW artifacts); every other shape is always preserved.

    Returns ``"stripped"`` when TRW entries were removed (user content kept),
    ``"removed"`` when nothing user-owned remained (file deleted), ``None`` when
    the file has no TRW content (left untouched), or ``"skipped"`` when the file
    cannot be parsed (left untouched + warned). On ``dry_run`` the same
    classification is returned without mutating the file.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    strategy = _resolve_strip_strategy(shape, path.suffix.lower())
    if strategy is None:
        return None
    try:
        changed, rendered, delete = strategy(raw)
    except (ValueError, TypeError) as exc:
        _runtime_logger().warning(
            "uninstall_merged_config_unparseable",
            path=str(path),
            error=type(exc).__name__,
            action="left_untouched",
        )
        return "skipped"
    if not changed:
        return None
    if delete:
        if not dry_run:
            path.unlink()
        return "removed"
    if not dry_run:
        path.write_text(rendered, encoding="utf-8")
    return "stripped"


def _strip_server_map(raw: str, container_key: str) -> tuple[bool, str, bool]:
    """Remove ``<container_key>.trw`` from JSON text.

    The MCP-server map is the same structure under three different container
    keys across clients: ``mcpServers`` (.mcp.json / .cursor / antigravity),
    ``servers`` (VS Code), ``mcp`` (opencode). Only the key differs, so the
    strip is one function parameterized by it.
    """
    import json

    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    servers = data.get(container_key)
    if not isinstance(servers, dict) or _TRW_SERVER_KEY not in servers:
        return False, raw, False
    del servers[_TRW_SERVER_KEY]
    if not servers:
        # Last server was ours — drop the now-empty container key too.
        del data[container_key]
    return True, json.dumps(data, indent=2) + "\n", False


def _strip_trw_json(raw: str) -> tuple[bool, str, bool]:
    """Remove ``mcpServers.trw`` from JSON text; return (changed, rendered, delete)."""
    return _strip_server_map(raw, _JSON_MCP_KEY)


def _strip_trw_vscode(raw: str) -> tuple[bool, str, bool]:
    """Remove ``servers.trw`` from a VS Code ``.vscode/mcp.json``."""
    return _strip_server_map(raw, _VSCODE_MCP_KEY)


def _strip_trw_opencode(raw: str) -> tuple[bool, str, bool]:
    """Remove ``mcp.trw`` and the managed instruction path from opencode.json.

    ``instructions`` is a list of instruction files opencode loads at start-up.
    Uninstall removes ``.opencode/INSTRUCTIONS.md``, so leaving it listed points
    the client at a file that no longer exists — the same dangling-reference
    failure as a hook entry whose script was deleted. Other entries in the list
    (and every other user key: model, agent, permission) are untouched.
    """
    import json

    changed, rendered, delete = _strip_server_map(raw, _OPENCODE_MCP_KEY)
    data = json.loads(rendered)
    if not isinstance(data, dict):
        return changed, rendered, delete
    instructions = data.get("instructions")
    if not isinstance(instructions, list):
        return changed, rendered, delete
    kept = [entry for entry in instructions if entry != _OPENCODE_INSTRUCTION_ENTRY]
    if len(kept) == len(instructions):
        return changed, rendered, delete
    if kept:
        data["instructions"] = kept
    else:
        del data["instructions"]
    return True, json.dumps(data, indent=2) + "\n", False


def _strip_trw_toml(raw: str) -> tuple[bool, str, bool]:
    """Remove ``[mcp_servers.trw]`` from TOML text; return (changed, rendered, delete).

    Reuses the codex TOML helpers (round-trip safe) so user tables/keys and
    comments are preserved structurally.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - Python <3.11 fallback
        import tomli as tomllib

    from trw_mcp.bootstrap._codex_toml import _toml_dumps

    data = tomllib.loads(raw)
    servers = data.get(_TOML_MCP_KEY)
    if not isinstance(servers, dict) or _TRW_SERVER_KEY not in servers:
        return False, raw, False
    del servers[_TRW_SERVER_KEY]
    if not servers:
        del data[_TOML_MCP_KEY]
    return True, _toml_dumps(data), False


def _is_trw_managed_hook_group(group: object) -> bool:
    """True when *group* is a TRW-managed hook group (``description`` prefix)."""
    if not isinstance(group, dict):
        return False
    description = group.get("description")
    return isinstance(description, str) and description.startswith(_TRW_HOOK_DESCRIPTION_PREFIX)


def _strip_trw_hook_groups(raw: str) -> tuple[bool, str, bool]:
    """Strip TRW-managed hook groups from a codex/copilot ``hooks.json`` map.

    Shape: ``{"hooks": {<event>: [<group>, ...]}}`` (with an optional top-level
    ``"version"``). TRW groups carry a ``description`` starting ``"TRW
    managed:"``; user groups and unknown top-level keys are preserved verbatim.
    When stripping leaves no user group anywhere AND no user top-level keys, the
    file held only TRW artifacts and is deleted (delete=True).
    """
    import json

    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False, raw, False

    changed = False
    new_hooks: dict[str, object] = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            # Preserve a malformed/unexpected event value untouched.
            new_hooks[event] = groups
            continue
        user_groups = [g for g in groups if not _is_trw_managed_hook_group(g)]
        if len(user_groups) != len(groups):
            changed = True
        if user_groups:
            new_hooks[event] = user_groups

    if not changed:
        return False, raw, False
    data["hooks"] = new_hooks
    # ``version`` is a TRW/structural key, not user content; any other top-level
    # key is treated as user-owned and forces preservation.
    user_top_level = [k for k in data if k not in ("hooks", "version")]
    if not new_hooks and not user_top_level:
        return True, "", True
    return True, json.dumps(data, indent=2) + "\n", False


def _is_trw_settings_hook(hook: object) -> bool:
    """True when a ``.claude/settings.json`` hook command invokes a TRW script.

    Every bundled hook command has the shape
    ``sh "$CLAUDE_PROJECT_DIR/.claude/hooks/<name>.sh"``. The directory is the
    discriminator, and it is a sound one: ``.claude/hooks`` is itself a plain
    uninstall surface that TRW deletes wholesale, so the manifest already
    treats everything under it as TRW-owned. Matching on the path (not on a
    list of script names) keeps hooks installed by an older TRW version
    removable too.
    """
    if not isinstance(hook, dict):
        return False
    return _TRW_HOOK_COMMAND_DIR in str(hook.get("command", ""))


def _strip_trw_settings_entry(entry: object) -> tuple[object | None, bool]:
    """Drop TRW hooks from one settings.json hook entry.

    Returns ``(kept_entry_or_None, changed)``. An entry whose ``hooks`` list
    becomes empty held nothing but TRW hooks and is dropped; an entry that also
    carries user hooks keeps its matcher and the user's hooks. Anything that is
    not the expected shape is preserved verbatim.
    """
    if not isinstance(entry, dict):
        return entry, False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return entry, False
    kept = [hook for hook in hooks if not _is_trw_settings_hook(hook)]
    if len(kept) == len(hooks):
        return entry, False
    if not kept:
        return None, True
    return {**entry, "hooks": kept}, True


def _strip_trw_claude_settings(raw: str) -> tuple[bool, str, bool]:
    """Withdraw TRW hook registrations from ``.claude/settings.json``.

    Shape: ``{"hooks": {<event>: [{"matcher": ..., "hooks": [{"command": ...}]}]}}``
    alongside user-owned ``env``/``permissions``/etc.

    Only hook registrations are withdrawn. ``env`` is deliberately left alone:
    ``_merge_settings_json`` seeds env keys with ``setdefault``, so a key TRW
    added and a key the user set themselves are indistinguishable on disk, and
    removing a setting we cannot prove we own is the worse failure. The file is
    never deleted — it is the user's client config, emptied at most.
    """
    import json

    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False, raw, False

    changed = False
    new_hooks: dict[str, object] = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            new_hooks[event] = entries
            continue
        kept_entries: list[object] = []
        for entry in entries:
            kept, entry_changed = _strip_trw_settings_entry(entry)
            changed = changed or entry_changed
            if kept is not None:
                kept_entries.append(kept)
        if kept_entries:
            new_hooks[event] = kept_entries

    if not changed:
        return False, raw, False
    if new_hooks:
        data["hooks"] = new_hooks
    else:
        del data["hooks"]
    return True, json.dumps(data, indent=2) + "\n", False


def _strip_hook_entries_by_command(hooks: dict[str, Any], token: str) -> tuple[dict[str, Any], bool]:
    """Drop ``{event: [{command: ...}]}`` entries whose command names *token*.

    Shared by the two clients that identify their TRW hook entries by the
    command path rather than by a description tag. Event keys left empty are
    dropped; non-list event values are preserved untouched.
    """
    out: dict[str, Any] = {}
    changed = False
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            out[event] = entries
            continue
        kept = [e for e in entries if not (isinstance(e, dict) and token in str(e.get("command", "")))]
        if len(kept) != len(entries):
            changed = True
        if kept:
            out[event] = kept
    return out, changed


def _strip_trw_cursor_hooks(raw: str) -> tuple[bool, str, bool]:
    """Strip TRW hook entries from ``.cursor/hooks.json``.

    Shape ``{"version": 1, "hooks": {event: [{"command": ...}]}}``. Cursor
    identifies TRW entries by the ``.cursor/hooks/trw-`` command prefix — the
    same identity ``_cursor_ide.py`` passes to ``smart_merge_cursor_json`` as
    ``identity_prefix`` — NOT by a ``"TRW managed:"`` description, so the
    hook-group strategy matches nothing here. This file was previously a plain
    surface: install preserved a Cursor user's own hooks, uninstall deleted them.
    """
    import json

    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False, raw, False
    new_hooks, changed = _strip_hook_entries_by_command(hooks, _CURSOR_HOOK_COMMAND_ID)
    if not changed:
        return False, raw, False
    data["hooks"] = new_hooks
    user_top_level = [k for k in data if k not in ("hooks", "version")]
    if not new_hooks and not user_top_level:
        return True, "", True
    return True, json.dumps(data, indent=2) + "\n", False


def _strip_trw_antigravity_hooks(raw: str) -> tuple[bool, str, bool]:
    """Strip TRW hook entries from the FLAT ``.antigravitycli/hooks.json``.

    Shape ``{event: [{"matcher": ..., "command": ...}]}`` — no ``hooks``
    wrapper (channels/antigravity/_before_edit_hook.py::_merge_hooks_json).
    That merger does ``dict(existing)`` and preserves every other event key, so
    the file is a merged config, not a TRW-only artifact as its previous plain
    registration assumed.
    """
    import json

    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    new_data, changed = _strip_hook_entries_by_command(data, _ANTIGRAVITY_HOOK_COMMAND_ID)
    if not changed:
        return False, raw, False
    if not new_data:
        return True, "", True
    return True, json.dumps(new_data, indent=2) + "\n", False


# Shape -> strip strategy dispatch. Each strategy takes the raw file text and
# returns ``(changed, rendered, delete)``. Registered after the strategy
# functions so the names resolve.
_STRIP_STRATEGIES: dict[str, Callable[[str], tuple[bool, str, bool]]] = {
    "mcp-server-map": _strip_trw_json,
    "codex-toml": _strip_trw_toml,
    "hook-group-list": _strip_trw_hook_groups,
    "claude-settings": _strip_trw_claude_settings,
    "vscode-server-map": _strip_trw_vscode,
    "opencode-config": _strip_trw_opencode,
    "cursor-hook-list": _strip_trw_cursor_hooks,
    "antigravity-hook-map": _strip_trw_antigravity_hooks,
}
