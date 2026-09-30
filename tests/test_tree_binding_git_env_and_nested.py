"""E2E-TREE-BINDING-GIT-ENV / -NESTED-REPO (INC-018 audit): the working-tree digest is about THIS repo, and says so when it cannot see a covered edit."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trw_mcp.state._tree_binding import UNBOUND_NESTED_REPO, snapshot_tree


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=30, env=env)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    return path


@pytest.mark.parametrize(
    "variable",
    ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR", "GIT_NAMESPACE"],
)
def test_an_inherited_git_variable_cannot_redirect_the_digest_to_another_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    project = _repo(tmp_path / "project")
    other = _repo(tmp_path / "other")
    (other / "b.py").write_text("y = 2\n", encoding="utf-8")
    expected = snapshot_tree(project, ()).tree_sha
    assert expected is not None

    value = {
        "GIT_DIR": str(other / ".git"),
        "GIT_WORK_TREE": str(other),
        "GIT_INDEX_FILE": str(other / ".git" / "index"),
        "GIT_OBJECT_DIRECTORY": str(other / ".git" / "objects"),
        "GIT_COMMON_DIR": str(other / ".git"),
        "GIT_NAMESPACE": "elsewhere",
    }[variable]
    monkeypatch.setenv(variable, value)

    assert snapshot_tree(project, ()).tree_sha == expected


def test_a_nested_repository_is_unbound_and_named(tmp_path: Path) -> None:
    project = _repo(tmp_path / "project")
    _repo(project / "vendor" / "inner")

    snap = snapshot_tree(project, ())

    assert snap.tree_sha is None
    assert snap.unbound_reason.startswith(UNBOUND_NESTED_REPO)
    assert "vendor/inner" in snap.unbound_reason


def test_an_ignored_nested_repository_does_not_unbind(tmp_path: Path) -> None:
    project = _repo(tmp_path / "project")
    (project / ".gitignore").write_text("vendor/\n", encoding="utf-8")
    _git(project, "add", ".gitignore")
    _git(project, "commit", "-qm", "ignore")
    _repo(project / "vendor" / "inner")

    assert snapshot_tree(project, ()).tree_sha is not None
