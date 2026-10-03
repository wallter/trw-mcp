"""UPDATE-SNAPSHOT-SPECIAL-FILES: a FIFO under a managed surface never aborts the pre-update snapshot."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from trw_mcp.bootstrap._update_project import _rollback
from trw_mcp.bootstrap._update_transaction import _snapshot_transaction_paths

pytestmark = [
    pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo"),
    pytest.mark.usefixtures("no_memory_daemon"),
]


def test_snapshot_skips_fifo_and_copies_regular_siblings(tmp_path: Path) -> None:
    skill = tmp_path / "project" / ".claude" / "skills" / "trw-stale"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill\n", encoding="utf-8")
    os.mkfifo(skill / "pipe")

    snapshot = _snapshot_transaction_paths(tmp_path / "project")
    try:
        copied = snapshot / ".claude" / "skills" / "trw-stale"
        assert (copied / "SKILL.md").read_text(encoding="utf-8") == "skill\n"
        assert not os.path.lexists(copied / "pipe")
    finally:
        shutil.rmtree(snapshot)


def test_rollback_leaves_the_skipped_fifo_in_place(tmp_path: Path) -> None:
    project = tmp_path / "project"
    skill = project / ".claude" / "skills" / "trw-stale"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill\n", encoding="utf-8")
    os.mkfifo(skill / "pipe")

    snapshot = _snapshot_transaction_paths(project)
    try:
        (skill / "SKILL.md").write_text("changed\n", encoding="utf-8")
        _rollback(project, snapshot, {"warnings": [], "errors": []})
    finally:
        shutil.rmtree(snapshot)
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "skill\n"
    assert (skill / "pipe").is_fifo()


@pytest.mark.integration
def test_update_project_with_a_fifo_in_a_skill_dir_does_not_abort(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    stale = tmp_path / ".claude" / "skills" / "trw-stale"
    stale.mkdir(parents=True)
    os.mkfifo(stale / "pipe")

    result = update_project(tmp_path, ide="claude-code")

    assert not any("Failed to snapshot" in e for e in result["errors"]), result["errors"]
    assert (stale / "pipe").is_fifo()


def _init_claude_code(tmp_path: Path) -> Path:
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    return tmp_path


@pytest.mark.integration
@pytest.mark.parametrize(
    "rel", [".trw/managed-artifacts.yaml", ".mcp.json", "AGENTS.md", ".trw/frameworks/FRAMEWORK.md"]
)
def test_fifo_at_a_managed_path_is_refused_before_any_write(tmp_path: Path, rel: str) -> None:
    """Every path here hung update_project forever once the snapshot skipped special files (FIFO probe, 13/68)."""
    project = _init_claude_code(tmp_path)
    (project / rel).unlink()
    os.mkfifo(project / rel)

    with pytest.raises(OSError, match="named pipe, socket or device") as info:
        _snapshot_transaction_paths(project)

    assert f"  {rel}" in str(info.value)
    assert (project / rel).is_fifo()


def test_read_yaml_refuses_a_fifo_instead_of_blocking(tmp_path: Path) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.persistence import FileStateReader

    os.mkfifo(tmp_path / "config.yaml")
    with pytest.raises(StateError, match="not a regular file"):
        FileStateReader().read_yaml(tmp_path / "config.yaml")


@pytest.mark.integration
@pytest.mark.timeout(120)
@pytest.mark.parametrize("rel", [".trw/config.yaml", ".trw/managed-artifacts.yaml"])
def test_update_project_with_a_fifo_at_an_early_read_path_fails_fast(tmp_path: Path, rel: str) -> None:
    """These two are read before the snapshot; both hung update_project until read_yaml refused special files."""
    from trw_mcp.bootstrap import update_project

    project = _init_claude_code(tmp_path)
    (project / rel).unlink()
    os.mkfifo(project / rel)

    result = update_project(project, ide="claude-code")

    assert result["errors"]
    assert (project / rel).is_fifo()
