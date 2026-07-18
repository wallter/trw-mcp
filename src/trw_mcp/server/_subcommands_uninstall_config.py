"""Managed-block and merged-config cleanup for lifecycle uninstall."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

# PRD-SEC-006 FR07: TRW-managed marker block pairs for SHARED files. Uninstall
# strips ONLY the content between these markers (preserving user content) rather
# than deleting the file wholesale. Each shared instruction file uses one pair;
# the bootstrap layer is the source of these literals (kept in sync there).
_MANAGED_BLOCK_MARKERS: tuple[tuple[str, str], ...] = (
    ("<!-- trw:start -->", "<!-- trw:end -->"),
    ("<!-- trw:gemini:start -->", "<!-- trw:gemini:end -->"),
    ("<!-- trw:copilot:start -->", "<!-- trw:copilot:end -->"),
    ("<!-- trw:antigravity:start -->", "<!-- trw:antigravity:end -->"),
)

# TRW server entry key written into merged client config files. JSON
# (.gemini/settings.json, .cursor/mcp.json, .antigravitycli/settings.json) nests
# it under ``mcpServers``; TOML (.codex/config.toml) under ``mcp_servers``.
_TRW_SERVER_KEY = "trw"
_JSON_MCP_KEY = "mcpServers"
_TOML_MCP_KEY = "mcp_servers"

# TRW-managed hook groups in a codex/copilot ``hooks.json`` map carry a
# ``description`` starting with this prefix (bootstrap: _codex_hooks.py /
# _copilot.py). Uninstall strips only groups tagged this way.
_TRW_HOOK_DESCRIPTION_PREFIX = "TRW managed:"

# The TRW-managed Gemini ``hooks.BeforeTool`` block is identified by this hook
# name (bootstrap: _gemini_distill_channels.py). Uninstall strips only it.
_GEMINI_MANAGED_HOOK_NAME = "trw-distill-before-edit-hint"
_GEMINI_HOOKS_KEY = "hooks"
_GEMINI_BEFORE_TOOL_KEY = "BeforeTool"


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

    sec-006: merged client config files (``.gemini/settings.json``,
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


def _strip_trw_json(raw: str) -> tuple[bool, str, bool]:
    """Remove ``mcpServers.trw`` from JSON text; return (changed, rendered, delete)."""
    import json

    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False
    servers = data.get(_JSON_MCP_KEY)
    if not isinstance(servers, dict) or _TRW_SERVER_KEY not in servers:
        return False, raw, False
    del servers[_TRW_SERVER_KEY]
    if not servers:
        # Last server was ours — drop the now-empty container key too.
        del data[_JSON_MCP_KEY]
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


def _is_gemini_managed_hook_block(block: object) -> bool:
    """True when *block* is the TRW-managed Gemini BeforeTool block (by name)."""
    if not isinstance(block, dict):
        return False
    hooks = block.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(isinstance(h, dict) and h.get("name") == _GEMINI_MANAGED_HOOK_NAME for h in hooks)


def _strip_trw_gemini_settings(raw: str) -> tuple[bool, str, bool]:
    """Strip TRW entries from ``.gemini/settings.json``; return (changed, rendered, delete).

    Removes both ``mcpServers.trw`` and the managed ``hooks.BeforeTool`` block
    (identified by hook name). Shared config carrying user settings — never
    deleted wholesale (delete is always False).
    """
    import json

    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw, False

    changed = False
    servers = data.get(_JSON_MCP_KEY)
    if isinstance(servers, dict) and _TRW_SERVER_KEY in servers:
        del servers[_TRW_SERVER_KEY]
        changed = True
        if not servers:
            del data[_JSON_MCP_KEY]

    hooks = data.get(_GEMINI_HOOKS_KEY)
    if isinstance(hooks, dict):
        before_tool = hooks.get(_GEMINI_BEFORE_TOOL_KEY)
        if isinstance(before_tool, list):
            kept = [b for b in before_tool if not _is_gemini_managed_hook_block(b)]
            if len(kept) != len(before_tool):
                changed = True
                if kept:
                    hooks[_GEMINI_BEFORE_TOOL_KEY] = kept
                else:
                    del hooks[_GEMINI_BEFORE_TOOL_KEY]
                if not hooks:
                    del data[_GEMINI_HOOKS_KEY]

    if not changed:
        return False, raw, False
    return True, json.dumps(data, indent=2) + "\n", False


# Shape -> strip strategy dispatch. Each strategy takes the raw file text and
# returns ``(changed, rendered, delete)``. Registered after the strategy
# functions so the names resolve.
_STRIP_STRATEGIES: dict[str, Callable[[str], tuple[bool, str, bool]]] = {
    "mcp-server-map": _strip_trw_json,
    "codex-toml": _strip_trw_toml,
    "hook-group-list": _strip_trw_hook_groups,
    "gemini-settings": _strip_trw_gemini_settings,
}
