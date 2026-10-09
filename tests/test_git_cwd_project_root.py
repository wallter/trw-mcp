"""Tools that ask git about the project ask it in the project, wherever the server process was started."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
    }
    done = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env)
    return done.stdout


def _make_repo(root: Path, name: str) -> Path:
    repo = root / name
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text(f"{name}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


@pytest.fixture
def two_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Project A (the one served) and repository B (where the process stands)."""
    project = _make_repo(tmp_path, "project")
    other = _make_repo(tmp_path, "other")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    monkeypatch.chdir(other)
    return project, other


def test_untracked_source_warning_reads_the_project_of_the_run(two_repos: tuple[Path, Path]) -> None:
    from trw_mcp.tools._delivery_helpers import _check_untracked_files

    project, _other = two_repos
    (project / "src" / "x.py").write_text("x = 1\n")
    run_path = project / ".trw" / "runs" / "task" / "20260101T000000Z-abcd"
    run_path.mkdir(parents=True)

    warning = _check_untracked_files(run_path)

    assert warning is not None and "src/x.py" in warning


def test_failure_attribution_compares_against_the_projects_changes(two_repos: tuple[Path, Path]) -> None:
    from trw_mcp.tools.build._failure_attribution import attribute_failures

    project, _other = two_repos
    (project / "src" / "widget.py").write_text("changed = True\n")
    _git(project, "add", "src/widget.py")

    block = attribute_failures(["tests/test_widget.py::test_it"])

    assert block is not None
    assert block["per_failure"][0]["classification"] == "likely_introduced"


def test_hint_mode_asks_about_the_project_when_no_repo_root_is_given(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from trw_mcp.tools import code

    project, _other = two_repos
    seen: list[str | None] = []
    real = code.compute_before_edit_hint

    def spy(**kwargs: str | None) -> object:
        seen.append(kwargs.get("repo_root"))
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(code, "compute_before_edit_hint", spy)

    code._one_hint("src/x.py", None, True)

    assert seen == [os.path.realpath(project)]


def test_dispatch_tool_without_a_cwd_works_in_the_project(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_dispatch_frictionless import _result, _Root, _tool

    project, _other = two_repos
    (project / "wip.txt").write_text("uncommitted\n")  # dirty project; the process stands in a clean repository
    launched: list[object] = []
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda req: launched.append(req) or _result("codex", "x"))

    out = _tool()(prompt="p", client="codex", role="implement", wait=True)

    assert "uncommitted" in str(out.get("warning", ""))
    assert [getattr(r, "cwd", None) for r in launched] == [project.resolve()]


def test_meta_tune_boot_validation_is_given_the_project_root(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._sub_models import MetaTuneConfig
    from trw_mcp.server import _app

    project, _other = two_repos
    seen: list[Path | None] = []
    monkeypatch.setattr(_app, "validate_meta_tune_defaults", lambda _cfg, *, repo_root=None: seen.append(repo_root))

    _app._run_meta_tune_boot_validation(TRWConfig(meta_tune=MetaTuneConfig(enabled=True)))

    assert seen == [project.resolve()]


def test_hint_mode_uses_the_repository_top_level_of_a_subdirectory_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from trw_mcp.tools import code

    top = _make_repo(tmp_path, "mono")
    (top / "pkg").mkdir()
    other = _make_repo(tmp_path, "other")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(top / "pkg"))
    monkeypatch.chdir(other)
    seen: list[str | None] = []
    real = code.compute_before_edit_hint

    def spy(**kwargs: str | None) -> object:
        seen.append(kwargs.get("repo_root"))
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(code, "compute_before_edit_hint", spy)

    code._one_hint("pkg/x.py", None, True)

    assert seen == [os.path.realpath(top)]


def test_dispatch_default_cwd_is_not_refused_when_trw_dir_is_nested(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_dispatch_frictionless import _result, _Root, _tool

    project, _other = two_repos
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: project / "state" / ".trw")
    launched: list[object] = []
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda req: launched.append(req) or _result("codex", "x"))

    out = _tool()(prompt="p", client="codex", allow_writes=True, wait=True)

    assert out.get("exit_code") != 2 and len(launched) == 1


def test_dispatch_relative_cwd_is_relative_to_the_project(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_dispatch_frictionless import _result, _Root, _tool

    project, _other = two_repos
    (project / "sub").mkdir()
    launched: list[object] = []
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda req: launched.append(req) or _result("codex", "x"))

    _tool()(prompt="p", client="codex", cwd="sub", wait=True)

    assert [getattr(r, "cwd", None) for r in launched] == [project.resolve() / "sub"]


def test_untracked_warning_with_a_custom_runs_root_reads_the_project(two_repos: tuple[Path, Path]) -> None:
    from trw_mcp.tools._delivery_helpers import _check_untracked_files

    project, _other = two_repos
    (project / "src" / "new.py").write_text("x = 1\n")
    run_path = project / "state" / "runs" / "task" / "20260101T000000Z-abcd"
    run_path.mkdir(parents=True)

    warning = _check_untracked_files(run_path)

    assert warning is not None and "src/new.py" in warning
