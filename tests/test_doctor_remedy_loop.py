"""E2E-DOCTOR-REMEDY-LOOP: every remedy a doctor row names, when run, turns that row green.

Break the project -> the row names a command -> run that command -> the row passes. The project is a real
``git init`` repo left uncommitted, the shape ``update-project`` meets right after ``init-project``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.server._doctor_hook_channel import hook_channel_row
from trw_mcp.server._doctor_launcher_divergence import launcher_divergence_row

_HOOK = ".claude/hooks/pre-tool-distill-hint.sh"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_memory_daemon: None) -> Path:
    """An initialised, uncommitted git repo; PATH holds only a stub ``trw-mcp`` (never the machine's install)."""
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    (stub_bin / "trw-mcp").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (stub_bin / "trw-mcp").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}/usr/bin{os.pathsep}/bin")
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=30)
    assert not init_project(root, ide="claude-code").get("errors")
    return root


def _run_named_remedy(message: str, root: Path) -> None:
    """Run the ``trw-mcp update-project ...`` command a doctor message names, parsed by the production CLI parser."""
    match = re.search(
        r"Remedy: (trw-mcp update-project[^(]*)|`(trw-mcp update-project[^`]*)`|-- fix with `?(trw-mcp update-project[^`,]*)",
        message,
    )
    assert match is not None, f"the row names no update-project command: {message!r}"
    command = next(group for group in match.groups() if group).split()
    args = _build_arg_parser().parse_args(command[1:])
    assert Path(args.target_dir).resolve() == Path(".").resolve() or args.target_dir in (".", None), (
        f"the printed command parses with target_dir={args.target_dir!r}, so it would not repair this project"
    )
    assert not update_project(root, reprovision=args.reprovision).get("errors")


@pytest.fixture
def hooked(project: Path) -> Path:
    """The project with the pre-edit hint hook switched on and deployed."""
    with (project / ".trw" / "config.yaml").open("a", encoding="utf-8") as handle:
        handle.write("\ncc03_hook_enabled: true\n")
    assert not update_project(project).get("errors")
    assert hook_channel_row(project)[0] == "PASS"
    return project


def test_a_hook_script_that_lost_its_exec_bit_is_repaired_by_the_named_remedy(hooked: Path) -> None:
    project = hooked
    (project / _HOOK).chmod(0o644)

    status, message = hook_channel_row(project)
    assert status == "FAIL"
    _run_named_remedy(message, project)

    assert hook_channel_row(project)[0] == "PASS"
    assert os.access(project / _HOOK, os.X_OK)


def test_a_deleted_hook_script_is_repaired_by_the_named_remedy(hooked: Path) -> None:
    project = hooked
    (project / _HOOK).unlink()

    status, message = hook_channel_row(project)
    assert status == "FAIL"
    assert f"--reprovision {_HOOK}" in message, "update-project alone keeps a deleted managed file deleted"
    _run_named_remedy(message, project)

    assert hook_channel_row(project)[0] == "PASS"


def test_a_launcher_off_the_project_venv_is_repaired_by_the_named_remedy(project: Path) -> None:
    (project / "trw-mcp" / "src" / "trw_mcp").mkdir(parents=True)
    (project / ".venv" / "bin").mkdir(parents=True)
    launcher = project / ".venv" / "bin" / "trw-mcp"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    assert json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["trw"]["command"] == "trw-mcp"

    status, message = launcher_divergence_row(project)
    assert status == "WARN"
    _run_named_remedy(message, project)

    assert launcher_divergence_row(project)[0] == "PASS"
    assert json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["trw"]["command"] == (
        ".venv/bin/trw-mcp"
    )


def test_an_uncommitted_malformed_mcp_json_is_still_kept_whole(project: Path) -> None:
    """The exemption that lets the launcher repair run on an uncommitted file must not cover a file the merge would discard."""
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "bin" / "trw-mcp").write_text("#!/bin/sh\n", encoding="utf-8")
    (project / ".mcp.json").write_text('{"mcpServers": [1, 2]}', encoding="utf-8")

    update_project(project)

    assert (project / ".mcp.json").read_text(encoding="utf-8") == '{"mcpServers": [1, 2]}'


def test_both_deleted_hook_scripts_are_repaired_by_the_named_remedy(hooked: Path) -> None:
    for name in ("pre-tool-distill-hint.sh", "lib-distill-hint.sh"):
        (hooked / ".claude" / "hooks" / name).unlink()

    status, message = hook_channel_row(hooked)
    assert status == "FAIL"
    _run_named_remedy(message, hooked)

    assert hook_channel_row(hooked)[0] == "PASS"


@pytest.mark.parametrize(
    "entry",
    [
        '{"command": "./tools/custom-trw", "args": ["--mine"]}',
        '{"command": "./tools/trw-mcp", "args": []}',
        '{"command": "trw-mcp", "args": ["--debug"]}',
        '{"command": "trw-mcp", "args": [], "env": {"A": "1"}}',
    ],
)
def test_an_uncommitted_custom_launcher_is_kept_whole(project: Path, entry: str) -> None:
    """A relative custom command with its own arguments is the user's work: the merge would replace it, so it comes back."""
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "bin" / "trw-mcp").write_text("#!/bin/sh\n", encoding="utf-8")
    custom = '{"mcpServers": {"trw": ' + entry + "}}"
    (project / ".mcp.json").write_text(custom, encoding="utf-8")

    update_project(project)

    assert (project / ".mcp.json").read_text(encoding="utf-8") == custom
