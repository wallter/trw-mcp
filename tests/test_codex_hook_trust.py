"""Opt-in pre-approval of TRW's own Codex hooks (``bootstrap/_codex_hook_trust.py``).

Every test points ``CODEX_HOME`` (or ``home=``) at a tmp directory: no test reads or writes a real ``~/.codex``.
The golden hashes are the ``trusted_hash`` values the Codex UI itself wrote on 2026-10-05 (codex-cli 0.160.0)
after "Allow all" for these exact hook entries, so a drift in the normalization fails here, not on a user's machine.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from trw_mcp.bootstrap._codex_hook_trust import (
    codex_hook_hash,
    revoke_trw_codex_hook_trust,
    run_cli,
    trust_trw_codex_hooks,
    trw_codex_hook_trust_entries,
)
from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

_GIT_ROOT = "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
_HINT: dict[str, object] = {
    "type": "command",
    "command": f'TRW_HOOK_CLIENT=codex /bin/sh "{_GIT_ROOT}/.claude/hooks/pre-tool-distill-hint.sh"',
    "statusMessage": "Loading TRW pre-edit hint",
    "timeout": 3,
}
_TELEMETRY = {
    "type": "command",
    "command": f'python3 "{_GIT_ROOT}/.codex/hooks/trw_post_edit_telemetry.py"',
    "statusMessage": "Recording TRW distill telemetry",
}
_USER_HOOK = {"type": "command", "command": "./my-own-lint.sh", "statusMessage": "user lint"}
_HINT_HASH = "sha256:ecff015f7854587feb3da02d92f2f11775c35ae14a9c147785b8f7050b4a2a8d"
_TELEMETRY_HASH = "sha256:9afc3d5065e15bb5e33f20935019980590dc784a4fbb41201cfbe069f45f9992"


def _write_hooks(project: Path, *, with_user_group: bool = False) -> Path:
    post = [{"description": "TRW managed: trw-distill PostToolUse telemetry", "hooks": [_TELEMETRY]}]
    if with_user_group:
        post.insert(0, {"hooks": [_USER_HOOK]})
    payload = {
        "hooks": {
            "PostToolUse": post,
            "PreToolUse": [{"description": "TRW managed: PreToolUse", "hooks": [_HINT], "matcher": "apply_patch"}],
        }
    }
    hooks_json = project / ".codex" / "hooks.json"
    hooks_json.parent.mkdir(parents=True)
    hooks_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return hooks_json


@pytest.fixture()
def codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root.resolve()


def _state(home: Path) -> dict[str, dict[str, object]]:
    parsed = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    state: dict[str, dict[str, object]] = parsed.get("hooks", {}).get("state", {})
    return state


def test_hash_matches_what_codex_wrote() -> None:
    assert codex_hook_hash("PreToolUse", "apply_patch", _HINT) == _HINT_HASH
    assert codex_hook_hash("PostToolUse", None, _TELEMETRY) == _TELEMETRY_HASH
    # Stop ignores the matcher and has no statusMessage; also a Codex-written value.
    stop: dict[str, object] = {
        "type": "command",
        "command": '/bin/sh "$(git rev-parse --show-toplevel)/.claude/hooks/stop-ceremony.sh"',
        "timeout": 30,
    }
    expected_stop = "sha256:5f877322f4d66accf48ad964633abb44130563465ef22207bbf4d84e1c61f847"
    assert codex_hook_hash("Stop", "ignored", stop) == expected_stop


def test_any_edit_to_the_hook_entry_changes_the_hash() -> None:
    for field, value in (("command", f"{_HINT['command']} "), ("statusMessage", "x"), ("timeout", 4)):
        assert codex_hook_hash("PreToolUse", "apply_patch", {**_HINT, field: value}) != _HINT_HASH
    assert codex_hook_hash("PreToolUse", "Bash", _HINT) != _HINT_HASH


def test_trust_writes_exactly_trws_entries_and_keeps_the_rest(codex_home: Path, project: Path) -> None:
    hooks_json = _write_hooks(project, with_user_group=True)
    original = '# my settings\nmodel = "x"\n\n[projects."/p"]\ntrust_level = "trusted"\n'
    (codex_home / "config.toml").write_text(original, encoding="utf-8")

    report = trust_trw_codex_hooks(project)

    assert report.error is None
    text = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert text.startswith(original)
    assert _state(codex_home) == {
        f"{hooks_json}:post_tool_use:1:0": {"trusted_hash": _TELEMETRY_HASH},
        f"{hooks_json}:pre_tool_use:0:0": {"trusted_hash": _HINT_HASH},
    }
    assert sorted(report.changed) == sorted(_state(codex_home))
    assert f"{hooks_json}:post_tool_use:0:0" not in text  # the user's own hook is never approved


def test_trust_is_idempotent_and_rewrites_a_stale_hash(codex_home: Path, project: Path) -> None:
    hooks_json = _write_hooks(project)
    trust_trw_codex_hooks(project)
    first = (codex_home / "config.toml").read_text(encoding="utf-8")
    again = trust_trw_codex_hooks(project)
    assert again.changed == [] and len(again.unchanged) == 2
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == first

    key = f"{hooks_json}:pre_tool_use:0:0"
    (codex_home / "config.toml").write_text(first.replace(_HINT_HASH, "sha256:old"), encoding="utf-8")
    assert trust_trw_codex_hooks(project).changed == [key]
    assert _state(codex_home)[key] == {"trusted_hash": _HINT_HASH}


def test_non_trw_command_inside_a_trw_group_is_not_approved(project: Path) -> None:
    hooks_json = _write_hooks(project)
    payload = json.loads(hooks_json.read_text(encoding="utf-8"))
    payload["hooks"]["PreToolUse"][0]["hooks"].append(_USER_HOOK)
    hooks_json.write_text(json.dumps(payload), encoding="utf-8")
    assert f"{hooks_json}:pre_tool_use:0:1" not in trw_codex_hook_trust_entries(project)


def test_revoke_removes_only_trws_exact_approvals(codex_home: Path, project: Path) -> None:
    hooks_json = _write_hooks(project)
    hint_key = f"{hooks_json}:pre_tool_use:0:0"
    tele_key = f"{hooks_json}:post_tool_use:0:0"
    (codex_home / "config.toml").write_text(
        f'model = "x"\n\n[hooks.state."{hint_key}"]\nenabled = false\ntrusted_hash = "{_HINT_HASH}"\n'
        f'\n[hooks.state."{tele_key}"]\ntrusted_hash = "sha256:someone-elses"\n',
        encoding="utf-8",
    )

    report = revoke_trw_codex_hook_trust(project)

    assert report.error is None and report.changed == [hint_key]
    assert _state(codex_home) == {hint_key: {"enabled": False}, tele_key: {"trusted_hash": "sha256:someone-elses"}}


def test_revoke_drops_the_whole_table_it_wrote(codex_home: Path, project: Path) -> None:
    _write_hooks(project)
    (codex_home / "config.toml").write_text('model = "x"\n', encoding="utf-8")
    trust_trw_codex_hooks(project)
    revoke_trw_codex_hook_trust(project)
    assert tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8")) == {"model": "x"}


def test_unparseable_or_foreign_shaped_config_is_left_unchanged(codex_home: Path, project: Path) -> None:
    hooks_json = _write_hooks(project)
    config = codex_home / "config.toml"
    config.write_text("model = [unterminated\n", encoding="utf-8")
    report = trust_trw_codex_hooks(project)
    assert report.error and report.changed == []
    assert config.read_text(encoding="utf-8") == "model = [unterminated\n"

    inline = f'[hooks.state]\n"{hooks_json}:pre_tool_use:0:0" = {{ enabled = true }}\n'
    config.write_text(inline, encoding="utf-8")
    report = trust_trw_codex_hooks(project)
    assert report.error is None
    assert any("does not edit" in line for line in report.skipped)
    assert f"{hooks_json}:post_tool_use:0:0" in report.changed  # the other key is still approved
    assert _state(codex_home)[f"{hooks_json}:pre_tool_use:0:0"] == {"enabled": True}


def test_missing_codex_home_is_an_error_and_writes_nothing(
    tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_hooks(project)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent"))
    assert trust_trw_codex_hooks(project).error
    assert not (tmp_path / "absent").exists()
    assert revoke_trw_codex_hook_trust(project).error is None  # uninstall stays quiet without Codex


def test_cli_verb_is_registered_and_prints_each_approval(
    codex_home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hooks_json = _write_hooks(project)
    args = _build_arg_parser().parse_args(["trust-codex-hooks", str(project)])
    SUBCOMMAND_HANDLERS["trust-codex-hooks"](args)
    out = capsys.readouterr().out
    assert f"Approved in {codex_home / 'config.toml'}: {hooks_json}:pre_tool_use:0:0" in out

    run_cli(_build_arg_parser().parse_args(["trust-codex-hooks", str(project), "--revoke"]))
    assert "Revoked" in capsys.readouterr().out
    assert _state(codex_home) == {}


@pytest.mark.usefixtures("no_memory_daemon")
def test_uninstall_revokes_trws_approvals_before_removing_hooks(
    codex_home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    from trw_mcp.server._subcommands import _run_uninstall

    _write_hooks(project)
    (project / ".trw").mkdir()
    (project / ".trw" / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    (codex_home / "config.toml").write_text('model = "x"\n', encoding="utf-8")
    trust_trw_codex_hooks(project)
    assert len(_state(codex_home)) == 2

    # The hand-written hooks.json here is "custom-formatted", so uninstall keeps it and exits non-zero; the
    # approvals still go first, which only means Codex asks again for a hook that is still there.
    with pytest.raises(SystemExit):
        _run_uninstall(argparse.Namespace(target_dir=str(project), dry_run=False, yes=True))

    assert _state(codex_home) == {}
    assert "Revoked in" in capsys.readouterr().out


def test_review_warning_names_the_trust_verb(project: Path) -> None:
    from trw_mcp.bootstrap._codex_hooks import codex_hooks_review_warning

    _write_hooks(project)
    assert "trw-mcp trust-codex-hooks" in codex_hooks_review_warning(project)
    assert "trw-mcp trust-codex-hooks" in codex_hooks_review_warning()
