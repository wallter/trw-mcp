"""E2E-TREE-BINDING-GIT-ENV / -NESTED-REPO (INC-018 audit): the working-tree digest is about THIS repo, and says so when it cannot see a covered edit."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import structlog

from trw_mcp.state._tree_binding import (
    UNBOUND_GIT_ERROR,
    UNBOUND_NESTED_REPO,
    UNBOUND_NOT_GIT,
    _empty_nested_repo,
    _git_env,
    snapshot_tree,
)


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


def test_a_nested_repository_with_no_commit_is_named_like_any_other(tmp_path: Path) -> None:
    """``git add`` cannot record a repository that has no commit and fails outright. That came back as a bare
    ``tree_unbound_git_error``: a build record in a real checkout on 2026-10-09 was unbound and nothing said why."""
    project = _repo(tmp_path / "project")
    empty = project / "vendor" / "inner"
    empty.mkdir(parents=True)
    _git(empty, "init", "-q")

    with structlog.testing.capture_logs() as logs:
        snap = snapshot_tree(project, ())

    assert (snap.tree_sha, snap.unbound_reason) == (None, f"{UNBOUND_NESTED_REPO}:vendor/inner")
    [event] = [entry for entry in logs if entry["event"] == "tree_snapshot_unbound"]
    assert (event["log_level"], event["detail"]) == ("info", "git add -A exited 128")


def test_any_other_git_failure_says_which_step_failed_and_keeps_gits_words_out_of_the_default_log(
    tmp_path: Path,
) -> None:
    """The reason code alone could not be acted on. The step and its exit code go out where a default log keeps them;
    what git printed can quote a path, a setting or a filter's output, so it is a debug line of its own."""
    project = _repo(tmp_path / "project")
    (project / ".git" / "index").write_bytes(b"not an index")  # every later git call on this repository fails

    with structlog.testing.capture_logs() as logs:
        snap = snapshot_tree(project, ())

    assert (snap.tree_sha, snap.unbound_reason) == (None, UNBOUND_GIT_ERROR)
    [event] = [entry for entry in logs if entry["event"] == "tree_snapshot_unbound"]
    assert event["log_level"] == "info"
    assert re.fullmatch(r"git ls-files -v exited \d+", event["detail"]), event["detail"]
    assert all(entry["log_level"] == "debug" for entry in logs if "said" in entry)


def test_a_directory_that_is_no_repository_is_unbound_without_a_log_line_above_debug(tmp_path: Path) -> None:
    with structlog.testing.capture_logs() as logs:
        snap = snapshot_tree(tmp_path, ())

    assert snap.unbound_reason == UNBOUND_NOT_GIT
    assert not [entry for entry in logs if entry["log_level"] != "debug"]


def test_git_is_asked_for_its_messages_in_one_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """One of git's messages is read to name a nested repository; the caller's locale must not change it."""
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "de_DE.UTF-8")

    assert _git_env(None)["LC_ALL"] == "C"


_PHRASE = "does not have a commit checked out"


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        pytest.param(
            f"error: 'vendor/inner/' {_PHRASE}\nfatal: adding files failed\n", "vendor/inner", id="a-repository"
        ),
        pytest.param(f"error: 'vendor/plain/' {_PHRASE}\n", "", id="a-directory-that-is-no-repository"),
        pytest.param(f"error: 'no/such/dir/' {_PHRASE}\n", "", id="a-path-that-does-not-exist"),
        pytest.param(f"error: '../outside/' {_PHRASE}\n", "", id="a-repository-outside-the-root"),
        pytest.param(f"error: './' {_PHRASE}\n", "", id="the-root-itself"),
        pytest.param(f"filter said: error: 'vendor/inner/' {_PHRASE}\n", "", id="not-at-the-start-of-a-line"),
        pytest.param(f"error: 'vendor/inner/' {_PHRASE} and more\n", "", id="not-the-whole-line"),
        pytest.param(f"error: 'x' {_PHRASE}' {_PHRASE}\n", "", id="a-name-that-holds-the-phrase"),
        pytest.param("fatal: adding files failed\n", "", id="another-failure"),
    ],
)
def test_a_nested_path_from_gits_message_is_believed_only_when_it_is_a_repository_below_the_root(
    tmp_path: Path, stderr: str, expected: str
) -> None:
    project = _repo(tmp_path / "project")
    for inner in (project / "vendor" / "inner", tmp_path / "outside"):
        inner.mkdir(parents=True)
        _git(inner, "init", "-q")
    (project / "vendor" / "plain").mkdir()

    assert _empty_nested_repo(project, stderr) == expected
