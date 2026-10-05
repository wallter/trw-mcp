"""The bootstrap writes the AG-03 hook where agy 1.2.x reads it (AG03-HOOK-REENABLE).

History: AG-03 was written to ``.antigravitycli/hooks.json`` (agy 1.0.2's flat
``{"PreToolUse": [{"matcher", "command"}]}``). agy 1.2.x does not read that file, so UF-BOOT-08 (2026-10-02)
withheld the hook rather than guess. Verified live on agy 1.2.15 (2026-10-03, a scratch workspace and a real
``write_to_file`` turn):

- ``<workspace>/.agents/hooks.json`` with the grouped, named-hook schema
  ``{"<name>": {"PreToolUse": [{"matcher", "hooks": [{"type", "command"}]}]}}`` is listed by
  ``agy -p /hooks --output-format json`` and its hook FIRED for a ``write_to_file`` call.
- The handler's working directory is the directory that holds ``hooks.json`` (``.agents/``), so the command
  ``python3 hooks/<script>`` resolves; stdin carries camelCase ``toolCall.name`` / ``toolCall.args.TargetFile``;
  a ``{"decision": "deny"}`` reply blocked the write, so the reply shape is honoured.

These tests pin the installer, its idempotence, its respect for a user's own hooks, and the script's contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels
from trw_mcp.channels.antigravity._before_edit_hook import (
    AG03_HOOK_NAME,
    AG03_HOOKS_PATH,
    ag03_hook_spec,
    install_before_edit_hook,
)

_SCRIPT = ".agents/hooks/trw_before_edit_telemetry.py"
_LEGACY_HOOKS_JSON = ".antigravitycli/hooks.json"
_LEGACY_SCRIPT = ".antigravitycli/hooks/trw_before_edit_telemetry.py"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


def _hooks(repo: Path) -> dict[str, object]:
    return json.loads((repo / AG03_HOOKS_PATH).read_text(encoding="utf-8"))


def test_the_bootstrap_installs_the_named_hook_and_its_script(repo: Path) -> None:
    result = install_antigravity_distill_channels(repo, force=True)

    assert not result["errors"], result["errors"]
    assert AG03_HOOKS_PATH in result["created"] and _SCRIPT in result["created"]
    assert _hooks(repo) == {AG03_HOOK_NAME: ag03_hook_spec()}
    assert (repo / _SCRIPT).is_file()


def test_the_registered_command_is_relative_to_the_dot_agents_directory(repo: Path) -> None:
    install_before_edit_hook(repo)

    entry = _hooks(repo)[AG03_HOOK_NAME]["PreToolUse"][0]  # type: ignore[index]
    assert entry["matcher"] == "write_to_file|replace_file_content|multi_replace_file_content"
    assert entry["hooks"] == [{"type": "command", "command": "python3 hooks/trw_before_edit_telemetry.py"}]
    # agy runs it with cwd = the directory holding hooks.json: the command must resolve from there.
    assert (repo / ".agents" / "hooks" / "trw_before_edit_telemetry.py").is_file()


def test_install_writes_nothing_at_the_legacy_paths(repo: Path) -> None:
    result = install_antigravity_distill_channels(repo, force=True)

    assert not (repo / _LEGACY_HOOKS_JSON).exists() and not (repo / _LEGACY_SCRIPT).exists()
    assert _LEGACY_HOOKS_JSON not in result["created"] + result["updated"] + result["preserved"]


def test_install_is_idempotent_and_keeps_the_hooks_json_stable(repo: Path) -> None:
    install_before_edit_hook(repo)
    first = (repo / AG03_HOOKS_PATH).read_bytes()

    again = install_before_edit_hook(repo)

    assert again["status"] == "unchanged"
    assert (repo / AG03_HOOKS_PATH).read_bytes() == first


def test_a_users_other_named_hooks_are_kept_byte_for_meaning(repo: Path) -> None:
    mine = {"safety-gate": {"PreToolUse": [{"matcher": "run_command", "hooks": [{"command": "./check.sh"}]}]}}
    (repo / ".agents").mkdir()
    (repo / AG03_HOOKS_PATH).write_text(json.dumps(mine), encoding="utf-8")

    install_before_edit_hook(repo)

    merged = _hooks(repo)
    assert merged["safety-gate"] == mine["safety-gate"]
    assert merged[AG03_HOOK_NAME] == ag03_hook_spec()


def test_a_user_edited_trw_entry_is_kept_and_no_script_is_installed(repo: Path) -> None:
    edited = {AG03_HOOK_NAME: {"PreToolUse": [{"matcher": "write_to_file", "hooks": [{"command": "echo MINE"}]}]}}
    (repo / ".agents").mkdir()
    body = json.dumps(edited)
    (repo / AG03_HOOKS_PATH).write_text(body, encoding="utf-8")

    result = install_before_edit_hook(repo)

    assert result["status"] == "user_edited" and result["skipped"] is True
    assert (repo / AG03_HOOKS_PATH).read_text(encoding="utf-8") == body
    assert not (repo / _SCRIPT).exists()


@pytest.mark.parametrize("body", ["{not json", "[1, 2]", '"text"'])
def test_a_hooks_json_that_is_not_an_object_is_left_untouched_and_reported(repo: Path, body: str) -> None:
    (repo / ".agents").mkdir()
    (repo / AG03_HOOKS_PATH).write_text(body, encoding="utf-8")

    result = install_before_edit_hook(repo)

    assert result["installed"] is False and result["error"]
    assert (repo / AG03_HOOKS_PATH).read_text(encoding="utf-8") == body, "the user's file was started over"


def test_the_bootstrap_reports_an_unparseable_hooks_json_as_an_error(repo: Path) -> None:
    (repo / ".agents").mkdir()
    (repo / AG03_HOOKS_PATH).write_text("{not json", encoding="utf-8")

    result = install_antigravity_distill_channels(repo, force=True)

    assert any("AG-03 hook install failed" in e for e in result["errors"]), result["errors"]


def test_install_leaves_an_existing_legacy_hooks_json_byte_identical(repo: Path) -> None:
    legacy = repo / _LEGACY_HOOKS_JSON
    legacy.parent.mkdir(parents=True)
    body = json.dumps({"PreToolUse": [{"matcher": "glob", "command": "echo USER_PRE_HOOK"}]}, indent=2) + "\n"
    legacy.write_text(body, encoding="utf-8")

    install_antigravity_distill_channels(repo, force=True)

    assert legacy.read_text(encoding="utf-8") == body


def test_manifest_and_subagent_steps_still_run(repo: Path) -> None:
    install_antigravity_distill_channels(repo)

    assert (repo / ".trw" / "channels" / "manifest.yaml").exists()


# -- the installed script: agy's contract, run the way agy runs it (cwd = .agents/) ----------------------------


def _run_hook(repo: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "hooks/trw_before_edit_telemetry.py"],
        cwd=repo / ".agents",
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_script_answers_allow_and_records_the_edited_path(repo: Path) -> None:
    install_before_edit_hook(repo)
    payload = {"toolCall": {"name": "write_to_file", "args": {"TargetFile": "/w/src/app.py"}}, "stepIdx": 2}

    done = _run_hook(repo, json.dumps(payload))

    assert done.returncode == 0 and json.loads(done.stdout) == {"decision": "allow"}
    line = (repo / ".trw" / "telemetry" / "channel-events.jsonl").read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["tool_name"] == "write_to_file" and event["file_path"] == "/w/src/app.py"
    assert event["channel_id"] == "ag-03-before-edit-hook" and event["client"] == "antigravity-cli"


@pytest.mark.parametrize("stdin", ["", "{not json", "[]", '{"toolCall": 7}', '{"toolCall": {"args": "x"}}'])
def test_the_script_is_fail_open_on_any_input(repo: Path, stdin: str) -> None:
    install_before_edit_hook(repo)

    done = _run_hook(repo, stdin)

    assert done.returncode == 0 and json.loads(done.stdout) == {"decision": "allow"}
