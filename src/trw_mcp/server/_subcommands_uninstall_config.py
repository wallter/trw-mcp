"""Managed-block and merged-config cleanup for lifecycle uninstall."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.bootstrap._generated_entries import (
    flat_hook_entries,
    grouped_hook_entries,
    hook_file_rest,
    mcp_server_entries,
    toml_table_texts,
)
from trw_mcp.bootstrap._git_hooks import MARKER_END as _GIT_HOOK_MARKER_END
from trw_mcp.bootstrap._git_hooks import MARKER_START as _GIT_HOOK_MARKER_START
from trw_mcp.bootstrap._opencode_instructions import (
    OPENCODE_INSTRUCTIONS_REL as _OPENCODE_INSTRUCTIONS_REL,
)
from trw_mcp.bootstrap._user_file_edit import (
    atomic_write_text,
    drop_matching_flat_hook_entries,
    drop_matching_hook_commands,
    is_generated_entry,
    matching_sort_keys,
    safe_read_text,
    strip_managed_block,
    strip_toml_table,
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

# The managed instruction file opencode.json points at, taken from the writer
# that produces it so the two cannot drift.
_OPENCODE_INSTRUCTION_ENTRY = _OPENCODE_INSTRUCTIONS_REL.as_posix()


def _runtime_logger() -> Any:
    """Return a fresh logger so structlog test capture sees late-bound events."""
    return structlog.get_logger(__name__)


def _strip_managed_blocks(text: str) -> str:
    """Back-compat text-only wrapper over :func:`strip_managed_block`.

    Drops the warnings list (callers that need orphan-marker reporting should
    call :func:`strip_managed_block` directly); kept for the existing
    ``TestStripManagedBlocks`` unit coverage that asserts on text shape alone.
    """
    stripped, _changed, _warnings = strip_managed_block(text, _MANAGED_BLOCK_MARKERS)
    return stripped


def _remove_managed_block_file(path: Path, root: Path, dry_run: bool) -> str | None:
    """Strip TRW blocks from a shared file; return a status word or None.

    Returns ``"stripped"`` when the TRW block was removed (file preserved),
    ``"removed"`` when stripping left the file empty (file deleted),
    ``"refused"`` when the guard rejected the path (a symlink itself, a
    symlinked parent, or an escape from *root*), or ``None`` when the file has
    no TRW block (left untouched). On ``dry_run`` the same classification is
    returned without mutating the file.

    Delegates the actual span removal (orphan-marker preservation, localized
    blank-line collapse only) to :func:`trw_mcp.bootstrap._user_file_edit.strip_managed_block`.
    """
    original, refusal = safe_read_text(path, root)
    if refusal:
        # PRD-INFRA-192 FR09 P0: a symlinked shared file (or a symlinked
        # parent component) would read/write through to bytes outside the
        # project the same way a symlinked delete surface would. Never touch
        # it -- same rule as _safe_remove -- and report the refusal.
        _runtime_logger().warning("uninstall_managed_block_refused", path=str(path), reason=refusal)
        return "refused"
    if original is None:
        return None
    stripped, changed, warnings = strip_managed_block(original, _MANAGED_BLOCK_MARKERS)
    for warning in warnings:
        _runtime_logger().warning("uninstall_marker_orphan", path=str(path), detail=warning)
    if not changed:
        return None  # no verified TRW block present -- leave the user's file alone
    if dry_run:
        return "removed" if not stripped.strip() else "stripped"
    if not stripped.strip():
        path.unlink()
        return "removed"
    atomic_write_text(path, stripped)
    return "stripped"


# Suffix -> default shape when a merged_config surface carries no explicit
# ``config_shape`` (backward-compat for surfaces registered before the
# shape-dispatch existed).
_SUFFIX_DEFAULT_SHAPE: dict[str, str] = {".json": "mcp-server-map", ".toml": "codex-toml"}


# A strip strategy: (raw text, project root) -> (changed, rendered, delete).
StripStrategy = Callable[[str, Path], tuple[bool, str, bool]]


def _resolve_strip_strategy(shape: str, suffix: str) -> StripStrategy | None:
    """Return the strip strategy for a shape, inferring from suffix when unset."""
    resolved = shape or _SUFFIX_DEFAULT_SHAPE.get(suffix, "")
    return _STRIP_STRATEGIES.get(resolved)


def _strip_trw_from_merged_config(path: Path, root: Path, dry_run: bool, *, shape: str = "") -> str | None:
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
    raw, refusal = safe_read_text(path, root)
    if refusal:
        # PRD-INFRA-192 FR09 P0: same rule as _remove_managed_block_file — a
        # symlinked merged-config file, or one behind a symlinked parent
        # component, would read/write through to bytes outside the project.
        _runtime_logger().warning("uninstall_merged_config_refused", path=str(path), reason=refusal)
        return "refused"
    if raw is None:
        return None
    strategy = _resolve_strip_strategy(shape, path.suffix.lower())
    if strategy is None:
        return None
    try:
        changed, rendered, delete = strategy(raw, root)
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
        atomic_write_text(path, rendered)
    return "stripped"


def _entry_changed(file_label: str, what: object) -> None:
    """Say why TRW's entry was left in place: it no longer equals what TRW generates."""
    _runtime_logger().warning("uninstall_entry_changed", path=file_label, entry=what, action="left_untouched")


def _strip_server_map(raw: str, container_key: str, file_label: str, generated: list[object]) -> tuple[bool, str, bool]:
    """Remove ``<container_key>.trw`` from JSON text, byte-preserving.

    The MCP-server map is the same structure under three different container
    keys across clients: ``mcpServers`` (.mcp.json / .cursor / antigravity /
    the home-scoped ``.gemini/config/mcp_config.json``), ``servers`` (VS
    Code), ``mcp`` (opencode). Only the key differs, so the strip is one
    function parameterized by it. The entry goes only when it equals, in full,
    one TRW generates (*generated*); a ``trw`` server the user replaced or gave
    an ``env`` stays. The rewrite happens only when *raw* already equals TRW's
    own canonical serialization of the parsed original (PRD-INFRA-192
    FR09/FR10) -- a custom-formatted file is left byte-identical with a warning.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    servers = data.get(container_key)
    if not isinstance(servers, dict) or _TRW_SERVER_KEY not in servers:
        return False, raw, False
    if not is_generated_entry(servers[_TRW_SERVER_KEY], generated):
        _entry_changed(file_label, f"{container_key}.{_TRW_SERVER_KEY}")
        return False, raw, False
    new_servers = dict(servers)
    del new_servers[_TRW_SERVER_KEY]
    new_data = dict(data)
    if new_servers:
        new_data[container_key] = new_servers
    else:
        # Last server was ours — drop the now-empty container key too.
        del new_data[container_key]
    sort_keys = matching_sort_keys(data, raw)
    if sort_keys is None:
        _runtime_logger().warning("uninstall_merged_config_custom_formatting", path=file_label, action="left_untouched")
        return False, raw, False
    return True, json.dumps(new_data, indent=2, sort_keys=sort_keys) + "\n", False


def _strip_trw_json(raw: str, root: Path) -> tuple[bool, str, bool]:
    """Remove ``mcpServers.trw`` from JSON text; return (changed, rendered, delete)."""
    return _strip_server_map(raw, _JSON_MCP_KEY, ".mcp.json", mcp_server_entries("mcp-server-map", root))


def _strip_trw_vscode(raw: str, root: Path) -> tuple[bool, str, bool]:
    """Remove ``servers.trw`` from a VS Code ``.vscode/mcp.json``."""
    return _strip_server_map(raw, _VSCODE_MCP_KEY, ".vscode/mcp.json", mcp_server_entries("vscode-server-map", root))


def _strip_trw_opencode(raw: str, root: Path) -> tuple[bool, str, bool]:
    """Remove ``mcp.trw`` and the managed instruction path from opencode.json.

    ``instructions`` is a list of instruction files opencode loads at start-up.
    Uninstall removes ``.opencode/INSTRUCTIONS.md``, so leaving it listed points
    the client at a file that no longer exists — the same dangling-reference
    failure as a hook entry whose script was deleted. Other entries in the list
    (and every other user key: model, agent, permission) are untouched. The
    ``trw`` server goes only when it equals, in full, the entry TRW generates.

    Both edits (the server-map entry and the instructions entry) are decided
    against the SAME canonical-or-untouched check on the original bytes, so a
    custom-formatted opencode.json is left completely alone rather than
    getting the server-map edit applied and the instructions edit skipped (or
    vice versa) against two different notions of "changed."
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    servers = data.get(_OPENCODE_MCP_KEY)
    has_server = isinstance(servers, dict) and _TRW_SERVER_KEY in servers
    if has_server and not is_generated_entry(servers[_TRW_SERVER_KEY], mcp_server_entries("opencode-config", root)):  # type: ignore[index]
        _entry_changed("opencode.json", f"{_OPENCODE_MCP_KEY}.{_TRW_SERVER_KEY}")
        has_server = False
    instructions = data.get("instructions")
    has_instruction = isinstance(instructions, list) and _OPENCODE_INSTRUCTION_ENTRY in instructions
    if not has_server and not has_instruction:
        return False, raw, False

    new_data = dict(data)
    if has_server:
        new_servers = dict(servers)  # type: ignore[arg-type]
        del new_servers[_TRW_SERVER_KEY]
        if new_servers:
            new_data[_OPENCODE_MCP_KEY] = new_servers
        else:
            del new_data[_OPENCODE_MCP_KEY]
    if has_instruction:
        kept = [entry for entry in instructions if entry != _OPENCODE_INSTRUCTION_ENTRY]  # type: ignore[union-attr]
        if kept:
            new_data["instructions"] = kept
        else:
            del new_data["instructions"]

    sort_keys = matching_sort_keys(data, raw)
    if sort_keys is None:
        _runtime_logger().warning(
            "uninstall_merged_config_custom_formatting", path="opencode.json", action="left_untouched"
        )
        return False, raw, False
    return True, json.dumps(new_data, indent=2, sort_keys=sort_keys) + "\n", False


def _strip_trw_toml(raw: str, root: Path) -> tuple[bool, str, bool]:
    """Remove ``[mcp_servers.trw]`` from TOML text; return (changed, rendered, delete).

    Text-level removal (:func:`trw_mcp.bootstrap._user_file_edit.strip_toml_table`)
    rather than parse-then-redump: TRW's own ``_toml_dumps`` writer has no
    comment/formatting round-trip. The table goes only when it equals, in full,
    the codex or grok table TRW generates; a table carrying the user's ``env``,
    tool approvals or a comment stays, with a warning. ``.codex/config.toml``
    and ``.grok/config.toml`` share this shape.
    """
    rendered, removed, refusal = strip_toml_table(raw, f"{_TOML_MCP_KEY}.{_TRW_SERVER_KEY}", toml_table_texts(root))
    if refusal:
        _entry_changed("config.toml", refusal)
    return (True, rendered, False) if removed else (False, raw, False)


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


def _strip_grouped_hooks(raw: str, shape: str, file_label: str, *, deletable: bool = True) -> tuple[bool, str, bool]:
    """Strip only the hook dicts TRW generates from a grouped ``{"hooks": {event: [group]}}`` file.

    A hook goes only when it equals, in full, a hook TRW generates for that
    event, inside a group whose own fields (``matcher``, ``description``) are
    TRW's too; a user's hook appended into a TRW group stays, and so does a TRW
    hook the user gave a ``timeout``. Shared by ``.codex/hooks.json``, the
    Copilot hooks file and ``.claude/settings.json``, via the same
    :func:`trw_mcp.bootstrap._user_file_edit.drop_matching_hook_commands` the
    per-script tombstone path uses. Rewrites only a file already in TRW's
    canonical formatting.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False, raw, False
    trw_hooks, trw_groups = grouped_hook_entries(shape)

    def _is_trw_hook(event: str, hook: dict[str, object]) -> bool:
        return is_generated_entry(hook, trw_hooks.get(event, []))

    def _is_trw_group(event: str, group: object) -> bool:
        fields = {k: v for k, v in group.items() if k != "hooks"} if isinstance(group, dict) else group
        return is_generated_entry(fields, trw_groups.get(event, []))

    new_hooks, changed = drop_matching_hook_commands(hooks, _is_trw_hook, file_label, {}, is_trw_group=_is_trw_group)
    _warn_kept_trw_commands(new_hooks, trw_hooks, file_label)
    if not changed:
        return False, raw, False
    new_data = dict(data)
    new_data["hooks"] = new_hooks
    return _canonical_or_untouched(
        data, raw, new_data, empty_key="hooks", file_label=file_label, shape=shape if deletable else ""
    )


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

    Only hook registrations are withdrawn. ``env`` is deliberately left alone:
    ``_merge_settings_json`` seeds env keys with ``setdefault``, so a key TRW
    added and a key the user set themselves are indistinguishable on disk, and
    removing a setting we cannot prove we own is the worse failure. The file is
    never deleted — it is the user's client config, emptied at most.
    """
    return _strip_grouped_hooks(raw, "claude-settings", ".claude/settings.json", deletable=False)


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


# Shape -> strip strategy dispatch. Each strategy takes the raw file text and
# returns ``(changed, rendered, delete)``. Registered after the strategy
# functions so the names resolve.
_STRIP_STRATEGIES: dict[str, StripStrategy] = {
    "mcp-server-map": _strip_trw_json,
    "codex-toml": _strip_trw_toml,
    "codex-hook-group-list": _strip_codex_hook_groups,
    "copilot-hook-group-list": _strip_copilot_hook_groups,
    "claude-settings": _strip_trw_claude_settings,
    "vscode-server-map": _strip_trw_vscode,
    "opencode-config": _strip_trw_opencode,
    "cursor-hook-list": _strip_trw_cursor_hooks,
    "antigravity-hook-map": _strip_trw_antigravity_hooks,
}
