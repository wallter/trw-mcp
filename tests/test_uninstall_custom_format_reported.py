"""E2E-UNINSTALL-CUSTOM-JSON (INC-013): uninstall never reports success while TRW entries stay behind.

A merged JSON config (``.mcp.json``, ``.claude/settings.json``) that is not in TRW's own formatting is left
byte-identical by design (PRD-INFRA-192 FR09/FR10): uninstall never rewrites a file the user formatted. It
used to say so only in a stderr log event while printing "Done" and exiting 0, so the ``trw`` server and 15
hook registrations pointing at deleted scripts stayed with no visible signal. Each such file is now named on
stdout with the remedy, and the run exits 1 like any other partial uninstall.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]

_FILES = (".mcp.json", ".claude/settings.json")


def _ns(project: Path) -> argparse.Namespace:
    return argparse.Namespace(
        target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide="claude-code"
    )


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide="claude-code")
    assert not result["errors"], result["errors"]
    return tmp_path


def _reformat(path: Path, *, add_user_server: bool = False) -> None:
    """Rewrite *path* as a user would: same data, ``json.dumps(indent=2)`` with no trailing newline."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if add_user_server:
        data.setdefault("mcpServers", {})["mine"] = {"command": "my-server"}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_custom_formatted_configs_are_reported_and_the_run_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    _reformat(project / ".mcp.json", add_user_server=True)
    _reformat(project / ".claude" / "settings.json")
    before = {rel: (project / rel).read_bytes() for rel in _FILES}

    with pytest.raises(SystemExit) as exited:
        _run_uninstall(_ns(project))

    assert exited.value.code == 1
    out = capsys.readouterr().out
    for rel in _FILES:
        assert (project / rel).read_bytes() == before[rel], f"{rel} must stay byte-identical"
        line = next((ln for ln in out.splitlines() if rel in ln and "Kept" in ln), None)
        assert line is not None, f"{rel} is not reported on stdout:\n{out}"
        assert "remove" in line.lower() and "by hand" in line.lower(), line


def test_canonically_formatted_configs_are_still_stripped_and_the_run_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: a file in TRW's formatting is cleaned, and nothing is reported as kept."""
    project = _project(tmp_path)

    _run_uninstall(_ns(project))

    out = capsys.readouterr().out
    assert "Kept" not in out or not any(rel in ln for ln in out.splitlines() if "Kept" in ln for rel in _FILES)
    mcp = project / ".mcp.json"
    assert not mcp.exists() or "trw" not in json.loads(mcp.read_text(encoding="utf-8")).get("mcpServers", {})
