"""A one-line edit to the deployed canon (same version stamp) is repaired by ``update-project`` as by ``trw_init``.

Canary-observed: ``.trw/frameworks/FRAMEWORK.md`` is TRW-owned; ``trw_init`` repaired a hand edit, ``update-project``
was reported not to. The user-edited ROOT ``FRAMEWORK.md`` stays preserved (test_update_redeploys_canon).
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _drifted_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, bytes]:
    from trw_mcp.bootstrap import init_project
    from trw_mcp.bootstrap._utils import _DATA_DIR
    from trw_mcp.models.config import _reset_config

    monkeypatch.setenv("MEMORY_DAEMON_AUTOSTART", "false")
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    assert not init_project(project, ide="claude-code")["errors"]
    bundled = (_DATA_DIR / "framework.md").read_bytes()
    canon = project / ".trw" / "frameworks" / "FRAMEWORK.md"
    assert canon.read_bytes() == bundled
    lines = bundled.decode("utf-8").splitlines(True)
    lines[len(lines) // 2] = "DRIFTED LINE, version stamp untouched\n"
    canon.write_bytes("".join(lines).encode("utf-8"))
    assert canon.read_bytes() != bundled
    monkeypatch.chdir(project)
    _reset_config()
    return project, canon, bundled


def test_update_project_cli_repairs_a_one_line_canon_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server._subcommands import _run_update_project

    project, canon, bundled = _drifted_project(tmp_path, monkeypatch)
    args = argparse.Namespace(target_dir=str(project), pip_install=False, dry_run=False, ide=None, reprovision=None)
    root_framework = project / "FRAMEWORK.md"
    root_before = root_framework.read_bytes()

    with pytest.raises(SystemExit) as exit_info:
        _run_update_project(args)

    assert exit_info.value.code == 0
    assert canon.read_bytes() == bundled
    assert root_framework.read_bytes() == root_before


def test_update_project_keeps_user_files_beside_the_repaired_canon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrowing the guard must not let the redeploy clobber a user note in frameworks/ or a hand-edited config."""
    from trw_mcp.server._subcommands import _run_update_project

    project, canon, bundled = _drifted_project(tmp_path, monkeypatch)
    notes = project / ".trw" / "frameworks" / "my-notes.md"
    notes.write_bytes(b"my private notes\n")
    config = project / ".trw" / "config.yaml"
    config.write_bytes(config.read_bytes() + b"# hand-edited by the user\n")
    notes_before, config_before = notes.read_bytes(), config.read_bytes()
    args = argparse.Namespace(target_dir=str(project), pip_install=False, dry_run=False, ide=None, reprovision=None)

    with pytest.raises(SystemExit) as exit_info:
        _run_update_project(args)

    assert exit_info.value.code == 0
    assert canon.read_bytes() == bundled
    assert notes.read_bytes() == notes_before
    assert config.read_bytes() == config_before


def test_trw_init_deploy_repairs_a_one_line_canon_edit_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools._orchestration_helpers import _deploy_frameworks

    project, canon, bundled = _drifted_project(tmp_path, monkeypatch)

    _deploy_frameworks(project / ".trw")

    assert canon.read_bytes() == bundled
