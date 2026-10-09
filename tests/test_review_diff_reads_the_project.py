"""The review tools and the transition gate read the PROJECT's diff, wherever the server process was started."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trw_mcp.tools import _review_helpers as helpers


def _git(repo: Path, *args: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    (repo / "a.txt").write_text("changed in the project\n")
    return repo


def test_the_diff_helper_reads_the_checkout_it_is_given(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a directory the helper runs in the process's own, which is the project only by coincidence."""
    elsewhere = tmp_path / "not-a-repository"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert helpers._get_git_diff() is None  # no repository here: unknown, never an empty diff
    diff = helpers._get_git_diff(cwd=project)
    assert diff is not None and "+changed in the project" in diff


@pytest.mark.parametrize("module", ["_review_auto", "_review_cross_model", "_review_manual"])
def test_every_review_mode_asks_for_the_projects_diff(module: str) -> None:
    """Each mode passes the project root; a bare call would review whatever repository the server stands in."""
    source = (Path(helpers.__file__).parent / f"{module}.py").read_text(encoding="utf-8")

    assert "_get_git_diff(cwd=resolve_project_root())" in source
    assert "_get_git_diff()" not in source
