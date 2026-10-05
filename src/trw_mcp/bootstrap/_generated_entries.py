"""What TRW generates into each merged client config, per uninstall shape (PRD-INFRA-192 FR09/FR10).

Uninstall removes a structured entry only when it equals, in full, one of
these (:func:`trw_mcp.bootstrap._user_file_edit.is_generated_entry`). Every
entry comes from its writer, called for the project being uninstalled, so the
identity cannot drift from what install produces. An entry a writer merged
user keys into (codex, grok and antigravity keep a user's ``env``) is no
longer one of these, and stays.

The server entries are resolved for *root* as it stands now: an entry written
while the project had a ``.venv`` launcher that has since gone no longer
matches, and is left with a warning. That is the safe direction.
"""

from __future__ import annotations

import json
from pathlib import Path

# (event -> TRW hook dicts, event -> TRW group fields other than "hooks")
GroupedHooks = tuple[dict[str, list[object]], dict[str, list[object]]]


def mcp_server_entries(shape: str, root: Path) -> list[object]:
    """Every ``trw`` server entry TRW's writers produce for a file of *shape*."""
    if shape == "mcp-server-map":
        # .mcp.json, .cursor/mcp.json, .antigravitycli/settings.json, the Copilot
        # CLI's .github/mcp.json and the home-scoped antigravity mcp_config.json
        # share this one shape.
        from ._antigravity_cli import _resolve_trw_mcp_command
        from ._copilot_cli_mcp import copilot_cli_trw_entry
        from ._cursor import _get_trw_mcp_entry_cursor
        from ._utils import _trw_mcp_server_entry

        command, args = _resolve_trw_mcp_command()
        return [
            _trw_mcp_server_entry(root),
            dict(_get_trw_mcp_entry_cursor(root)),
            {"command": command, "args": args},
            copilot_cli_trw_entry(root),
        ]
    if shape == "vscode-server-map":
        from trw_mcp.channels.copilot._vscode_mcp import _TRW_MCP_SERVER_ENTRY, _trw_entry_for

        return [_trw_entry_for(root), _TRW_MCP_SERVER_ENTRY]
    if shape == "opencode-config":
        from ._opencode import _get_trw_mcp_entry

        return [dict(_get_trw_mcp_entry(root))]
    if shape == "codex-toml":
        from ._codex import merge_codex_config
        from ._grok import merge_grok_config

        codex = merge_codex_config({}, target_dir=root)["mcp_servers"]["trw"]
        grok = merge_grok_config({}, target_dir=root)["mcp_servers"]
        return [dict(codex), grok["trw"] if isinstance(grok, dict) else None]
    return []


def toml_table_texts(root: Path) -> list[str]:
    """The exact ``[mcp_servers.trw]`` text (nested tables included) the codex and grok writers render."""
    from ._codex_toml import _toml_dumps

    return [
        _toml_dumps({"mcp_servers": {"trw": entry}}).removeprefix("[mcp_servers]\n\n")
        for entry in mcp_server_entries("codex-toml", root)
        if isinstance(entry, dict)
    ]


def hook_file_rest(shape: str) -> dict[str, object]:
    """Everything but ``hooks`` in the hooks file TRW's writer generates for *shape* (``version``)."""
    if shape == "codex-hook-group-list":
        from ._codex_hooks import _codex_hooks_payload

        payload: dict[str, object] = dict(_codex_hooks_payload())
    elif shape == "copilot-hook-group-list":
        from ._copilot import _copilot_hooks_payload

        payload = dict(_copilot_hooks_payload())
    elif shape == "cursor-hook-list":
        from ._cursor_hooks_io import build_cursor_hook_config

        payload = dict(build_cursor_hook_config({}))
    else:
        return {}
    return {k: v for k, v in payload.items() if k != "hooks"}


def _grouped(hooks_by_event: dict[str, list[dict[str, object]]]) -> GroupedHooks:
    hooks: dict[str, list[object]] = {}
    shells: dict[str, list[object]] = {}
    for event, groups in hooks_by_event.items():
        for group in groups:
            hooks.setdefault(event, []).extend(group.get("hooks", []))  # type: ignore[arg-type]
            shells.setdefault(event, []).append({k: v for k, v in group.items() if k != "hooks"})
    return hooks, shells


def grouped_hook_entries(shape: str, root: Path | None = None) -> GroupedHooks:
    """TRW's hook dicts and group fields, per event, for a grouped hooks file of *shape* (in project *root*)."""
    if shape == "codex-hook-group-list":
        from ._codex_distill_channels import _pre_edit_hint_group, distill_hook_group, legacy_distill_hook_group
        from ._codex_hooks import _codex_hooks_payload

        codex: dict[str, list[dict[str, object]]] = dict(_codex_hooks_payload()["hooks"])  # type: ignore[arg-type]
        # The CC-03 pre-edit hint group set_pre_edit_hint_registration adds; uninstall deletes its script, so it
        # must withdraw this group too or codex fails every apply_patch (INC-012 follow-up).
        codex["PreToolUse"] = [*codex.get("PreToolUse", []), _pre_edit_hint_group()]
        if root is not None:
            # E2E-UNINSTALL-CODEX: the distill telemetry group names the project's absolute script path, so it
            # is only TRW's for this root; without it uninstall deleted the script and kept the hook calling it.
            codex["PostToolUse"] = [
                *codex.get("PostToolUse", []),
                distill_hook_group(root),
                legacy_distill_hook_group(root),  # an install never updated since the portable command
            ]
        return _grouped(codex)
    if shape == "copilot-hook-group-list":
        from ._copilot import _copilot_hooks_payload

        return _grouped(_copilot_hooks_payload()["hooks"])  # type: ignore[arg-type]
    if shape == "claude-settings":
        from ._claude_code_distill_channels import _CC03_ENTRY
        from ._utils import _DATA_DIR

        bundled = json.loads((_DATA_DIR / "settings.json").read_text(encoding="utf-8"))
        hooks: dict[str, list[dict[str, object]]] = dict(bundled.get("hooks", {}))
        # The CC-03 pre-edit hint entry is registered by its channel installer, not the template; uninstall
        # removes its script, so it must withdraw this registration too or Claude Code calls a missing hook.
        hooks["PreToolUse"] = [*hooks.get("PreToolUse", []), _CC03_ENTRY]
        return _grouped(hooks)
    return {}, {}


def flat_hook_entries(shape: str) -> dict[str, list[object]]:
    """TRW's whole hook entries, per event, for a flat (no group wrapper) hooks file of *shape*."""
    if shape == "cursor-hook-list":
        from ._cursor_cli import _CLI_HOOK_EVENTS
        from ._cursor_ide import _IDE_HOOK_EVENTS

        out: dict[str, list[object]] = {}
        for events in (_IDE_HOOK_EVENTS, _CLI_HOOK_EVENTS):
            for event, entries in events.items():
                out.setdefault(event, []).extend(entries)
        return out
    if shape == "antigravity-hook-map":
        from trw_mcp.channels.antigravity._before_edit_hook import (
            _PRE_TOOL_USE_EVENT,
            LEGACY_EDIT_TOOL_MATCHER,
            LEGACY_HOOK_SCRIPT_PATH,
        )

        return {
            _PRE_TOOL_USE_EVENT: [
                {"matcher": LEGACY_EDIT_TOOL_MATCHER, "command": f"python3 {LEGACY_HOOK_SCRIPT_PATH}"}
            ]
        }
    return {}
