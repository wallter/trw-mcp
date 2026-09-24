"""PRD-INFRA-192 FR09/FR10 round 5: a structured entry is removed only when it is exactly TRW's.

A matching key, command or table name says where TRW's entry would be, never
that the bytes there are still TRW's. Each test builds the entry TRW's own
writer generates, changes one thing a user would, and checks the entry
survives, then checks the unedited entry still goes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _canonical(data: object) -> str:
    return json.dumps(data, indent=2) + "\n"


# --- mcpServers.trw: judged by content, not by key -------------------------


def test_mcp_server_entry_the_user_replaced_survives(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._utils import _trw_mcp_server_entry
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_json

    theirs = _canonical({"mcpServers": {"trw": {"command": "/opt/my-trw", "args": ["--mine"]}}})
    assert _strip_trw_json(theirs, tmp_path) == (False, theirs, False)

    ours = _canonical({"mcpServers": {"trw": _trw_mcp_server_entry(tmp_path), "mine": {"command": "m"}}})
    changed, rendered, _ = _strip_trw_json(ours, tmp_path)
    assert changed is True
    assert json.loads(rendered) == {"mcpServers": {"mine": {"command": "m"}}}


def test_mcp_server_entry_with_a_user_env_survives(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._utils import _trw_mcp_server_entry
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_json

    edited = {**_trw_mcp_server_entry(tmp_path), "env": {"TRW_DEBUG": "1"}}
    raw = _canonical({"mcpServers": {"trw": edited}})
    assert _strip_trw_json(raw, tmp_path) == (False, raw, False)


def test_opencode_server_the_user_edited_survives_while_the_instruction_entry_goes(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._opencode import _get_trw_mcp_entry
    from trw_mcp.bootstrap._opencode_instructions import OPENCODE_INSTRUCTIONS_REL
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_opencode

    edited = {**_get_trw_mcp_entry(tmp_path), "enabled": False}
    raw = _canonical({"mcp": {"trw": edited}, "instructions": [OPENCODE_INSTRUCTIONS_REL.as_posix()]})
    changed, rendered, _ = _strip_trw_opencode(raw, tmp_path)
    assert changed is True
    assert json.loads(rendered) == {"mcp": {"trw": edited}}


# --- [mcp_servers.trw]: the whole table, nested tables and comments included --


def _codex_table(root: Path) -> str:
    from trw_mcp.bootstrap._codex import merge_codex_config
    from trw_mcp.bootstrap._codex_toml import _toml_dumps

    rendered = _toml_dumps({"mcp_servers": {"trw": merge_codex_config({}, target_dir=root)["mcp_servers"]["trw"]}})
    return rendered.removeprefix("[mcp_servers]\n\n")  # the parent header is not part of the trw table


def test_the_generated_codex_table_goes_and_a_trailing_comment_stays(tmp_path: Path) -> None:
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_toml

    head = '[mcp_servers.mine]\ncommand = "m"\n\n'
    tail = "# <<< TRW MANAGED BLOCK (tables) <<<\n"
    changed, rendered, _ = _strip_trw_toml(head + _codex_table(tmp_path) + "\n" + tail, tmp_path)
    assert changed is True
    assert rendered == head + tail


@pytest.mark.parametrize(
    ("edit", "why"),
    [
        ('env = { TRW_DEBUG = "1" }\n', "a user key"),
        ("# my note about trw\n", "a comment inside the table"),
    ],
)
def test_a_codex_table_the_user_added_to_survives_byte_identical(tmp_path: Path, edit: str, why: str) -> None:
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_toml

    table = _codex_table(tmp_path).replace("[mcp_servers.trw]\n", "[mcp_servers.trw]\n" + edit, 1)
    raw = '[mcp_servers.mine]\ncommand = "m"\n\n' + table
    assert _strip_trw_toml(raw, tmp_path) == (False, raw, False), why


def test_a_codex_table_with_a_user_nested_table_survives_byte_identical(tmp_path: Path) -> None:
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_toml

    raw = _codex_table(tmp_path) + '\n[mcp_servers.trw.tools.my_own_tool]\napproval_mode = "approve"\n'
    assert _strip_trw_toml(raw, tmp_path) == (False, raw, False)


def test_a_blank_line_the_user_added_inside_the_codex_table_survives(tmp_path: Path) -> None:
    """Round 6: the parsed table still equals TRW's, but the text span does not -- the blank line is theirs."""
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_toml

    table = _codex_table(tmp_path).replace("enabled = true\n", "enabled = true\n\n", 1)
    raw = '[mcp_servers.mine]\ncommand = "m"\n\n' + table
    assert _strip_trw_toml(raw, tmp_path) == (False, raw, False)


def test_a_bare_key_after_the_codex_table_keeps_it(tmp_path: Path) -> None:
    """A key below the table's last line (even past a blank line) still belongs to the table in TOML."""
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_toml

    raw = _codex_table(tmp_path) + "\nstartup_timeout_sec = 30\n"
    assert _strip_trw_toml(raw, tmp_path) == (False, raw, False)


def test_an_inline_comment_in_the_codex_table_survives(tmp_path: Path) -> None:
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_toml

    table = _codex_table(tmp_path).replace("enabled = true", "enabled = true # keep me on", 1)
    assert "keep me on" in table
    assert _strip_trw_toml(table, tmp_path) == (False, table, False)


# --- hook dicts: a TRW command the user changed is the user's ---------------


def _codex_group() -> tuple[str, dict[str, object]]:
    from trw_mcp.bootstrap._codex_hooks import _codex_hooks_payload

    event, groups = next(iter(_codex_hooks_payload()["hooks"].items()))
    return event, json.loads(json.dumps(groups[0]))


def test_a_codex_hook_the_user_gave_a_timeout_survives(tmp_path: Path) -> None:
    from trw_mcp.server._subcommands_uninstall_config import _strip_codex_hook_groups

    event, group = _codex_group()
    group["hooks"][0]["timeout"] = 999  # type: ignore[index]
    raw = _canonical({"hooks": {event: [group]}})
    assert _strip_codex_hook_groups(raw, tmp_path) == (False, raw, False)


def test_a_codex_group_whose_matcher_the_user_changed_survives(tmp_path: Path) -> None:
    from trw_mcp.server._subcommands_uninstall_config import _strip_codex_hook_groups

    event, group = _codex_group()
    group["matcher"] = "my-own-matcher"
    raw = _canonical({"hooks": {event: [group]}, "mine": True})
    assert _strip_codex_hook_groups(raw, tmp_path) == (False, raw, False)


def test_an_unedited_codex_hook_still_goes(tmp_path: Path) -> None:
    from trw_mcp.server._subcommands_uninstall_config import _strip_codex_hook_groups

    event, group = _codex_group()
    changed, _rendered, delete = _strip_codex_hook_groups(_canonical({"hooks": {event: [group]}}), tmp_path)
    assert (changed, delete) == (True, True)


@pytest.mark.parametrize("version", [2, "1"])
def test_a_hooks_file_whose_version_the_user_changed_is_emptied_not_deleted(tmp_path: Path, version: object) -> None:
    """Round 6: only a file equal to the generated one (``version`` included) is deleted."""
    from trw_mcp.bootstrap._cursor_ide import _IDE_HOOK_EVENTS
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_cursor_hooks

    event, entries = next(iter(_IDE_HOOK_EVENTS.items()))
    changed, rendered, delete = _strip_trw_cursor_hooks(
        _canonical({"version": version, "hooks": {event: [dict(entries[0])]}}), tmp_path
    )
    assert (changed, delete) == (True, False)
    assert json.loads(rendered) == {"version": version, "hooks": {}}


def test_a_codex_hooks_file_the_user_gave_a_version_is_emptied_not_deleted(tmp_path: Path) -> None:
    from trw_mcp.server._subcommands_uninstall_config import _strip_codex_hook_groups

    event, group = _codex_group()
    changed, rendered, delete = _strip_codex_hook_groups(
        _canonical({"version": 1, "hooks": {event: [group]}}), tmp_path
    )
    assert (changed, delete) == (True, False)
    assert json.loads(rendered) == {"version": 1, "hooks": {}}


# --- flat hooks: whole-entry match, and pre-existing empty keys stay ---------


def test_flat_hook_removal_keeps_an_event_key_that_was_already_empty() -> None:
    from trw_mcp.bootstrap._user_file_edit import drop_matching_flat_hook_entries

    ours = {"command": "trw-cmd"}
    hooks: dict[str, object] = {"stop": [], "beforeShell": [dict(ours)], "afterEdit": [dict(ours), {"command": "u"}]}
    new_hooks, changed = drop_matching_flat_hook_entries(hooks, lambda _ev, e: e == ours)
    assert changed is True
    assert new_hooks == {"stop": [], "afterEdit": [{"command": "u"}]}


def test_a_cursor_hook_the_user_retimed_survives(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._cursor_ide import _IDE_HOOK_EVENTS
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_cursor_hooks

    event, entries = next(iter(_IDE_HOOK_EVENTS.items()))
    retimed = {**entries[0], "timeout": 999}
    raw = _canonical({"version": 1, "hooks": {event: [retimed], "userEvent": []}})
    assert _strip_trw_cursor_hooks(raw, tmp_path) == (False, raw, False)

    changed, rendered, _ = _strip_trw_cursor_hooks(
        _canonical({"version": 1, "hooks": {event: [dict(entries[0])], "userEvent": []}}), tmp_path
    )
    assert changed is True
    assert json.loads(rendered)["hooks"] == {"userEvent": []}


# --- update: a symlinked .claude/hooks parent is never followed --------------


def _record_retired_hook(outside: Path) -> dict[str, str]:
    retired = outside / "retired.sh"
    retired.write_text("#!/bin/sh\necho retired\n")
    return {"retired.sh": hashlib.sha256(retired.read_bytes()).hexdigest()}


def test_update_does_not_follow_a_symlinked_hooks_parent(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    project, outside = tmp_path / "proj", tmp_path / "outside"
    (project / ".claude").mkdir(parents=True)
    outside.mkdir()
    manifest = _record_retired_hook(outside)
    (project / ".claude" / "hooks").symlink_to(outside, target_is_directory=True)
    result: dict[str, list[str]] = {}

    _update_hooks(project, _DATA_DIR, result, None, manifest, ide="claude-code")

    assert sorted(p.name for p in outside.iterdir()) == ["retired.sh"], "nothing read, written or deleted outside"
    assert any("symlink" in w for w in result["warnings"])


def test_withdrawal_rechecks_each_recorded_hook_before_reading_or_deleting(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._template_updater import _withdraw_retired_hooks

    project, outside = tmp_path / "proj", tmp_path / "outside"
    (project / ".claude" / "hooks").mkdir(parents=True)
    outside.mkdir()
    manifest = _record_retired_hook(outside)
    (project / ".claude" / "hooks" / "retired.sh").symlink_to(outside / "retired.sh")
    result: dict[str, list[str]] = {}

    _withdraw_retired_hooks(project, set(), manifest, result)

    assert (outside / "retired.sh").is_file()
    assert (project / ".claude" / "hooks" / "retired.sh").is_symlink()
    assert "removed" not in result
    assert any("symlink" in w for w in result["warnings"])


# --- r7: Python equates True, 1 and 1.0; TRW's identity check must not --------


@pytest.mark.parametrize("version", [True, 1.0], ids=["true", "1.0"])
def test_a_hooks_file_whose_version_only_equals_ours_in_python_is_kept(tmp_path: Path, version: object) -> None:
    """``True == 1 == 1.0`` in Python, but the user wrote different bytes: kept, with an empty hooks map."""
    from trw_mcp.bootstrap._cursor_ide import _IDE_HOOK_EVENTS
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_cursor_hooks

    event, entries = next(iter(_IDE_HOOK_EVENTS.items()))
    changed, rendered, delete = _strip_trw_cursor_hooks(
        _canonical({"version": version, "hooks": {event: [dict(entries[0])]}}), tmp_path
    )
    assert (changed, delete) == (True, False)
    assert json.loads(rendered) == {"version": version, "hooks": {}}
    assert json.dumps(json.loads(rendered)["version"]) == json.dumps(version)


def test_a_server_entry_whose_enabled_only_equals_ours_in_python_survives(tmp_path: Path) -> None:
    """opencode generates ``"enabled": true``; a user's ``"enabled": 1`` is not TRW's entry."""
    from trw_mcp.bootstrap._opencode import _get_trw_mcp_entry
    from trw_mcp.server._subcommands_uninstall_config import _strip_trw_opencode

    edited = {**_get_trw_mcp_entry(tmp_path), "enabled": 1}
    raw = _canonical({"mcp": {"trw": edited}})
    assert _strip_trw_opencode(raw, tmp_path) == (False, raw, False)
