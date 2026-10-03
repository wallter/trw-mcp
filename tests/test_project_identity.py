"""SYNC-PROJECT-IDENTITY (a): a stable, portable project id rides on every pushed learning.

The id is ``git:`` plus the first 16 hex of sha256 over the repository's root commit id (the smallest when there
are several). It reveals nothing (no name, path or URL), is the same in every clone on every machine, and falls
back to the project namespace (``ns:``) where git cannot say: no repository, or a shallow clone whose root commit
is not the real one.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(cwd: Path, *args: str) -> str:
    import os

    done = subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **_GIT_ENV}, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _repo(path: Path, commits: int = 1) -> tuple[Path, str]:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    for n in range(commits):
        (path / f"f{n}").write_text(f"{path.name}-{n}")  # unique content: equal trees at one second share a hash
        _git(path, "add", ".")
        _git(path, "commit", "-q", "-m", f"c{n}")
    return path, _git(path, "rev-list", "--max-parents=0", "HEAD")


def _expected(root_commit: str) -> str:
    return "git:" + hashlib.sha256(root_commit.encode()).hexdigest()[:16]


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    from trw_mcp.state import _project_identity

    _project_identity.reset_cache()


def test_the_id_is_the_hash_of_the_root_commit_and_reveals_nothing(tmp_path: Path) -> None:
    from trw_mcp.state._project_identity import project_id

    repo, root = _repo(tmp_path / "secret-client-name", commits=2)

    got = project_id(repo, namespace="project:x-1")

    assert got == _expected(root)
    assert "secret" not in got and str(tmp_path) not in got


def test_a_clone_on_another_machine_has_the_same_id(tmp_path: Path) -> None:
    from trw_mcp.state._project_identity import project_id

    repo, _ = _repo(tmp_path / "origin", commits=3)
    clone = tmp_path / "elsewhere" / "clone"
    clone.parent.mkdir()
    _git(tmp_path, "clone", "-q", str(repo), str(clone))

    assert project_id(clone, namespace="project:other-2") == project_id(repo, namespace="project:x-1")


def test_several_root_commits_pick_the_smallest(tmp_path: Path) -> None:
    from trw_mcp.state._project_identity import project_id

    repo, first = _repo(tmp_path / "multi", commits=1)
    _git(repo, "checkout", "-q", "--orphan", "other")
    (repo / "g").write_text("g")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "second root")
    second = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--allow-unrelated-histories", "-m", "join", "other")

    assert sorted(_git(repo, "rev-list", "--max-parents=0", "HEAD").split()) == sorted([first, second])
    assert project_id(repo, namespace="project:x-1") == _expected(min(first, second))


def test_a_shallow_clone_does_not_mistake_its_boundary_for_the_root(tmp_path: Path) -> None:
    from trw_mcp.state._project_identity import project_id

    repo, _ = _repo(tmp_path / "origin", commits=3)
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow))

    assert project_id(shallow, namespace="project:x-1") == "ns:" + hashlib.sha256(b"project:x-1").hexdigest()[:16]


def test_outside_a_repository_it_falls_back_to_the_namespace(tmp_path: Path) -> None:
    from trw_mcp.state._project_identity import project_id

    plain = tmp_path / "plain"
    plain.mkdir()

    assert project_id(plain, namespace="project:x-1") == "ns:" + hashlib.sha256(b"project:x-1").hexdigest()[:16]


def test_it_is_computed_once_per_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import _project_identity

    repo, _ = _repo(tmp_path / "r")
    calls: list[Path] = []
    real = _project_identity._roots
    monkeypatch.setattr(_project_identity, "_roots", lambda root: (calls.append(root), real(root))[1])

    first = _project_identity.project_id(repo, namespace="project:x-1")
    second = _project_identity.project_id(repo, namespace="project:x-1")

    assert first == second and len(calls) == 1


def test_a_pulled_row_from_this_same_project_is_attributable_and_another_projects_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row the operator's other machine pushed for THIS repository is not foreign; another repository's row is."""
    from trw_mcp.state import _paths
    from trw_mcp.state._origin_project import demote_unattributable, is_attributable_to_this_project
    from trw_mcp.state._project_identity import project_id

    repo, _ = _repo(tmp_path / "mine")
    other, _ = _repo(tmp_path / "other", commits=2)
    monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: repo / ".trw")
    mine = project_id(repo, namespace="project:mine-1")
    theirs = project_id(other, namespace="project:other-1")
    base = {"source": "team_sync", "namespace": "project:mine-1"}
    own_row = {**base, "id": "team-sync-a", "metadata": {"origin_project": mine}}
    foreign_row = {**base, "id": "team-sync-b", "metadata": {"origin_project": theirs}}
    unknown_row = {**base, "id": "team-sync-c", "metadata": {"origin_project": "unknown"}}

    assert is_attributable_to_this_project(own_row) is True
    assert is_attributable_to_this_project(foreign_row) is False
    assert is_attributable_to_this_project(unknown_row) is False
    assert [r["id"] for r in demote_unattributable([foreign_row, unknown_row, own_row])] == [
        "team-sync-a",
        "team-sync-b",
        "team-sync-c",
    ]


def _orphan_root(repo: Path, branch: str) -> str:
    _git(repo, "checkout", "-q", "--orphan", branch)
    (repo / f"{branch}.txt").write_text(branch)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", branch)
    return _git(repo, "rev-parse", "HEAD")


def test_the_stamped_id_does_not_depend_on_the_checked_out_branch(tmp_path: Path) -> None:
    """A docs or pages orphan branch is a second root; checking it out must not change this machine's id."""
    from trw_mcp.state import _project_identity

    repo, first = _repo(tmp_path / "r")
    second = _orphan_root(repo, "pages")
    smallest = min(first, second)

    on_pages = _project_identity.project_id(repo, namespace="project:x-1")
    _project_identity.reset_cache()
    _git(repo, "checkout", "-q", "main")
    on_main = _project_identity.project_id(repo, namespace="project:x-1")

    assert on_pages == on_main == _expected(smallest)


def test_every_root_of_the_repository_is_this_project(tmp_path: Path) -> None:
    """A row stamped before an unrelated history was merged in carries a root that is no longer the smallest."""
    from trw_mcp.state import _project_identity

    repo, first = _repo(tmp_path / "r")
    second = _orphan_root(repo, "pages")

    ids = _project_identity.own_project_ids(repo, namespace="project:x-1")

    assert ids == {_expected(first), _expected(second)}


def test_a_shallow_or_plain_directory_has_only_the_namespace_id(tmp_path: Path) -> None:
    from trw_mcp.state import _project_identity

    plain = tmp_path / "plain"
    plain.mkdir()

    assert _project_identity.own_project_ids(plain, namespace="project:x-1") == {
        "ns:" + hashlib.sha256(b"project:x-1").hexdigest()[:16]
    }
