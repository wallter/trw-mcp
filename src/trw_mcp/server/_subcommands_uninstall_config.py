"""Managed-block and merged-config cleanup for lifecycle uninstall."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path

from trw_mcp.bootstrap._generated_entries import (
    mcp_server_entries,
    toml_table_texts,
)
from trw_mcp.bootstrap._git_hooks import MARKER_END as _GIT_HOOK_MARKER_END
from trw_mcp.bootstrap._git_hooks import MARKER_START as _GIT_HOOK_MARKER_START
from trw_mcp.bootstrap._git_hooks import SHIM_PREAMBLE as _GIT_HOOK_SHIM_PREAMBLE
from trw_mcp.bootstrap._opencode_instructions import (
    OPENCODE_INSTRUCTIONS_REL as _OPENCODE_INSTRUCTIONS_REL,
)
from trw_mcp.bootstrap._safe_remove import remove_if_hash
from trw_mcp.bootstrap._user_file_edit import (
    atomic_write_text,
    is_generated_entry,
    matching_sort_keys,
    safe_read_text,
    strip_managed_block,
    strip_toml_table,
)
from trw_mcp.channels._manifest_models import MARKER_REGISTRY
from trw_mcp.server._uninstall_hook_strips import (
    _STRIP_PATH,
    _entry_changed,
    _strip_codex_hook_groups,
    _strip_copilot_hook_groups,
    _strip_legacy_claude_md,
    _strip_trw_antigravity_hooks,
    _strip_trw_claude_settings,
    _strip_trw_cursor_hooks,
)
from trw_mcp.server._uninstall_hook_strips import CUSTOM_FORMAT as CUSTOM_FORMAT
from trw_mcp.server._uninstall_hook_strips import KEPT_EDITED as KEPT_EDITED
from trw_mcp.server._uninstall_hook_strips import (
    QUIET as QUIET,
)
from trw_mcp.server._uninstall_hook_strips import (
    _canonical_or_untouched as _canonical_or_untouched,
)
from trw_mcp.server._uninstall_hook_strips import (
    _drop_template_env as _drop_template_env,
)
from trw_mcp.server._uninstall_hook_strips import (
    _hook_commands as _hook_commands,
)
from trw_mcp.server._uninstall_hook_strips import (
    _runtime_logger as _runtime_logger,
)
from trw_mcp.server._uninstall_hook_strips import (
    _strip_grouped_hooks as _strip_grouped_hooks,
)
from trw_mcp.server._uninstall_hook_strips import (
    _warn_kept_trw_commands as _warn_kept_trw_commands,
)
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


# Names the file being stripped, for warnings raised inside the shape strategies (which take no path).
_STRIP_LABEL: ContextVar[str] = ContextVar("_STRIP_LABEL", default="")

# CUSTOM_FORMAT / _STRIP_PATH live in the leaf _uninstall_hook_strips (imported below) so its hook
# strategies can record a custom-formatted file too; they are re-exported here for existing importers.

# Why each file was last refused (a symlink, or a read failure the path guard cannot see), for the report.
REFUSAL_REASONS: dict[Path, str] = {}


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
    REFUSAL_REASONS.pop(path, None)
    if refusal:
        REFUSAL_REASONS[path] = refusal
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
    # A post-commit hook TRW created holds only its shim header once the block goes (E2E-UNINSTALL-EMPTY-DIRS).
    empty = not stripped.strip() or stripped.strip() == _GIT_HOOK_SHIM_PREAMBLE.strip()
    if dry_run:
        return "removed" if empty else "stripped"
    if empty:
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


def _delete_judged(path: Path, root: Path, raw: str, rel: Path, captures: dict[str, list[str]] | None) -> str:
    """Remove *path* only while it still holds the bytes *raw* that were judged to be TRW's alone.

    ``remove_if_hash`` captures the file into ``.trw/trash``, re-hashes the captured bytes and links them back
    under the name on any mismatch, so an edit saved since the read survives (HB-2). The capture joins
    *captures*, for the move on to the system Trash. Returns ``"removed"`` or ``"refused"`` (reason recorded).
    """
    outcome = remove_if_hash(path, root, hashlib.sha256(raw.encode("utf-8")).hexdigest(), key=rel.as_posix())
    if outcome.status == "removed" and captures is not None:
        captures.setdefault("trashed", []).append(str(path))
        captures.setdefault("trashed_at", []).append(str(outcome.retained_at or ""))
    if outcome.status in ("removed", "absent"):
        return "removed"
    where = f"; your edited bytes are in {outcome.retained_at}" if outcome.status == "retained" else ""
    REFUSAL_REASONS[path] = f"kept: the file changed while uninstall ran ({outcome.reason}); left as found{where}"
    return "refused"


def _strip_trw_from_merged_config(
    path: Path,
    root: Path,
    dry_run: bool,
    *,
    shape: str = "",
    verify_unchanged: bool = False,
    captures: dict[str, list[str]] | None = None,
) -> str | None:
    """Strip ONLY TRW-owned entries from a merged client config file.

    sec-006: merged client config files (the root ``.mcp.json``,
    ``.codex/config.toml``, ``.codex/hooks.json``, ``.github/hooks/hooks.json``,
    ``.cursor/mcp.json``, ``.antigravitycli/settings.json``) may carry
    user-owned settings/servers/hook groups. They MUST NOT be deleted wholesale
    on uninstall — we parse the file, drop only the TRW-owned entries via the
    structural strategy named by ``shape``, and write the rest back.

    A project file that holds nothing user-owned once TRW's entries are gone (an empty ``{}``, an empty
    hook map, an empty ``[mcp_servers]`` table) is TRW's own shell and is removed (INC-117); any key, server,
    hook or table the user owns keeps the file, and so does ``opencode.json``'s ``$schema``/``permission``/
    ``tools``: those are content whose origin (TRW's seed or the user's own file) cannot be proven without an
    install-time record. Known limit: a user's own empty ``{}`` that held TRW's entry is indistinguishable from
    a shell TRW created. The machine-global file (*verify_unchanged*) is the user's client config and is never
    removed.

    Returns ``"stripped"`` when TRW entries were removed (user content kept),
    ``"removed"`` when nothing user-owned remained (file deleted), ``None`` when
    the file has no TRW content (left untouched), or ``"skipped"`` when the file
    cannot be parsed (left untouched + warned). On ``dry_run`` the same
    classification is returned without mutating the file. With *verify_unchanged*
    (the machine-global file) the bytes are re-read right before the write and a
    difference returns ``"changed"``: another writer got there first, so the file
    is left as that writer left it.
    """
    raw, refusal = safe_read_text(path, root)
    REFUSAL_REASONS.pop(path, None)
    if refusal:
        REFUSAL_REASONS[path] = refusal
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
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    label_token = _STRIP_LABEL.set(rel.as_posix())
    path_token = _STRIP_PATH.set(path)
    CUSTOM_FORMAT.discard(path)
    KEPT_EDITED.pop(path, None)
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
    finally:
        _STRIP_LABEL.reset(label_token)
        _STRIP_PATH.reset(path_token)
    if not changed:
        return None
    if not dry_run and verify_unchanged:
        current, refusal = safe_read_text(path, root)
        if refusal:  # the compare read itself failed: report that, not a concurrent edit
            REFUSAL_REASONS[path] = refusal
            return "refused"
        if current != raw:
            _runtime_logger().warning(
                "uninstall_merged_config_changed_during_run", path=str(path), action="left_untouched"
            )
            return "changed"
    if not (delete or verify_unchanged) and path.suffix.lower() == ".json" and rendered.strip() == "{}":
        delete = True  # nothing the user owns is left: the file is only TRW's shell
    if delete:
        return "removed" if dry_run else _delete_judged(path, root, raw, rel, captures)
    if not dry_run:
        atomic_write_text(path, rendered)
    return "stripped"


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
    file_label = _STRIP_LABEL.get() or file_label
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
        if (strip_path := _STRIP_PATH.get()) is not None:
            CUSTOM_FORMAT.add(strip_path)
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
    from trw_mcp.server._uninstall_codex_config import strip_codex_managed

    managed = strip_codex_managed(raw)  # a marker-managed .codex/config.toml: withdraw TRW's whole merge
    if managed is not None:
        return managed
    rendered, removed, refusal = strip_toml_table(raw, f"{_TOML_MCP_KEY}.{_TRW_SERVER_KEY}", toml_table_texts(root))
    if refusal:
        _entry_changed("config.toml", refusal)
    if removed and {line.strip() for line in rendered.splitlines()} <= {"", f"[{_TOML_MCP_KEY}]"}:
        return True, "", True  # only an empty ``[mcp_servers]`` header is left: TRW's own file
    return (True, rendered, False) if removed else (False, raw, False)


# Shape -> strip strategy dispatch. Each strategy takes the raw file text and
# returns ``(changed, rendered, delete)``. Registered after the strategy
# functions so the names resolve.


_STRIP_STRATEGIES: dict[str, StripStrategy] = {
    "legacy-claude-md": _strip_legacy_claude_md,
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
