"""Verbs and scorers that name "this project" ask git in the project, not in the directory the process stands in."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.handoff._cli_support import _new, _run
from tests.test_git_cwd_project_root import _git, two_repos  # noqa: F401


def test_rework_rate_has_no_process_directory_default(two_repos: tuple[Path, Path]) -> None:
    from trw_mcp.scoring.rework_rate import compute_rework_rate

    with pytest.raises(TypeError):
        compute_rework_rate(["README.md"])  # type: ignore[call-arg]


def test_rework_rate_reads_the_history_of_the_given_project(two_repos: tuple[Path, Path]) -> None:
    from trw_mcp.scoring.rework_rate import compute_rework_rate

    project, _other = two_repos
    (project / "README.md").write_text("fixed\n")
    _git(project, "commit", "-aqm", "fix: the readme")

    result = compute_rework_rate(["README.md"], project_root=project)

    assert result["rework_files"] == 1


def test_channel_stats_default_root_is_the_project(two_repos: tuple[Path, Path]) -> None:
    from trw_mcp.tools.channel_stats import _resolve_repo_root

    project, _other = two_repos

    assert _resolve_repo_root(None) == project.resolve()


def test_channel_stats_explicit_root_still_wins(two_repos: tuple[Path, Path], tmp_path: Path) -> None:
    from trw_mcp.tools.channel_stats import _resolve_repo_root

    assert _resolve_repo_root(str(tmp_path)) == tmp_path


def test_handoff_new_records_the_git_state_of_the_project(
    two_repos: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests._path_isolation import set_current_root
    from trw_mcp.handoff import load

    project, _other = two_repos
    set_current_root(project)
    monkeypatch.setattr("trw_mcp.state._paths.find_active_run", lambda **_: None)
    head = _git(project, "rev-parse", "HEAD").strip()

    out = _new(capsys, "--subject", "s", "--out", str(project / "d.json"))

    assert load(out)["as_of"]["base_ref"]["commit"] == head


def test_handoff_new_usage_error_path_is_unchanged(
    two_repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(capsys, "new", "--subject", "has space")[0] == 2


def test_two_repos_really_differ(two_repos: tuple[Path, Path]) -> None:
    project, other = two_repos
    assert subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    ).stdout.strip() == str(other.resolve())
    assert project != other


def _stand_in_the_directory_with_nothing_bound(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No project is bound: the suite's stand-in resolver answers the process directory, as the real one then does."""
    from tests import _path_isolation

    monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("TRW_REPO_ROOT", raising=False)
    monkeypatch.chdir(directory)
    _path_isolation.set_current_root(directory.resolve())


def test_channel_stats_from_a_subdirectory_with_nothing_bound_is_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no project bound the root is only the process directory. Run from ``repo/src`` the verb must still read
    ``repo/.trw``, as it did when it asked git: the first version of this fix read ``repo/src/.trw`` and found nothing."""
    from tests.test_git_cwd_project_root import _make_repo
    from trw_mcp.tools.channel_stats import _resolve_repo_root

    repo = _make_repo(tmp_path, "repo")
    below = repo / "src" / "deep"
    below.mkdir(parents=True, exist_ok=True)
    _stand_in_the_directory_with_nothing_bound(below, monkeypatch)

    assert _resolve_repo_root(None) == repo.resolve()


def test_channel_stats_in_a_project_inside_a_larger_repository_is_that_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_git_cwd_project_root import _make_repo
    from trw_mcp.tools.channel_stats import _resolve_repo_root

    repo = _make_repo(tmp_path, "repo")
    project = repo / "apps" / "one"
    (project / ".trw").mkdir(parents=True)
    _stand_in_the_directory_with_nothing_bound(project, monkeypatch)

    assert _resolve_repo_root(None) == project.resolve()


def test_channel_stats_outside_any_repository_is_the_process_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools.channel_stats import _resolve_repo_root

    _stand_in_the_directory_with_nothing_bound(tmp_path, monkeypatch)

    assert _resolve_repo_root(None) == tmp_path.resolve()
