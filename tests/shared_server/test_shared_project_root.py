"""SWAP-PROJECT-ROOT: shared-server verbs act only on a real TRW project; outside one they refuse and create nothing.

`trw-mcp swap` resolved the project from CWD or TRW_PROJECT_ROOT and silently created a record root wherever that
was, so every canary "swap" went to a private record no server used.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _trw_project(root: Path, *, git: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".trw").mkdir()
    (root / ".trw" / "config.yaml").write_text("framework_version: test\n", encoding="utf-8")
    return root


@pytest.fixture
def cwd_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The production resolution order (binding > TRW_PROJECT_ROOT > CWD); the suite's conftest pins it to tmp_path."""
    import os

    from trw_mcp.state._project_root_binding import install_target

    def production_order() -> Path:
        bound = install_target()
        if bound is not None:
            return bound
        env_root = os.environ.get("TRW_PROJECT_ROOT")
        return Path(env_root).resolve() if env_root else Path.cwd().resolve()

    monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", production_order)
    return monkeypatch


def _status(capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    from trw_mcp.shared_server import _cli

    with pytest.raises(SystemExit) as exc:
        _cli.run_status(argparse.Namespace())
    return int(exc.value.code or 0), capsys.readouterr().err


def test_a_non_project_cwd_refuses_and_creates_nothing(
    tmp_path: Path, cwd_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    cwd_env.chdir(plain)

    code, err = _status(capsys)

    assert code == 1 and "not a TRW project" in err
    assert not (plain / ".trw").exists(), "no record root is created outside a project"


def test_a_stray_trw_dir_without_config_is_not_a_project(
    tmp_path: Path, cwd_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stray = tmp_path / "stray"
    (stray / ".trw" / "context").mkdir(parents=True)  # what a hook state leak leaves behind
    cwd_env.chdir(stray)

    code, err = _status(capsys)

    assert code == 1 and "config.yaml" in err
    assert not (stray / ".trw" / "runtime").exists()


def test_trw_project_root_at_a_non_project_refuses(
    tmp_path: Path, cwd_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _trw_project(tmp_path / "proj", git=True)
    cwd_env.chdir(project)  # the CWD is fine; the explicit setting wins and is wrong
    cwd_env.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    code, err = _status(capsys)

    assert code == 1 and "not a TRW project" in err and str(tmp_path) in err


def test_a_subdirectory_resolves_to_its_git_toplevel(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    from trw_mcp.shared_server._cli import shared_project_root

    project = _trw_project(tmp_path / "proj", git=True)
    sub = project / "src" / "pkg"
    sub.mkdir(parents=True)
    cwd_env.chdir(sub)

    assert shared_project_root() == project.resolve()


def test_a_non_git_trw_project_resolves_to_itself(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    from trw_mcp.shared_server._cli import shared_project_root

    project = _trw_project(tmp_path / "nogit", git=False)
    cwd_env.chdir(project)
    cwd_env.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))  # never find an enclosing repo above tmp_path

    assert shared_project_root() == project.resolve()


def test_a_subdirectory_uses_the_project_roots_own_config(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    """Codex KI1: the record root was normalized but the config still loaded through the subdirectory."""
    from trw_mcp.shared_server import _cli

    project = _trw_project(tmp_path / "proj", git=True)
    envs = tmp_path / "project-envs"
    (project / ".trw" / "config.yaml").write_text(f"shared_mcp:\n  envs_dir: {envs}\n", encoding="utf-8")
    sub = project / "src"
    sub.mkdir()
    cwd_env.setenv("TRW_PROJECT_ROOT", str(sub))

    paths, config, root = _cli._paths()

    assert root == project.resolve()
    assert config.shared_mcp.envs_dir == str(envs)
    assert paths.envs_dir == envs


def test_a_missing_git_keeps_the_resolved_root(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    """Codex KI2: with no git on PATH a non-git TRW project crashed instead of resolving to itself."""
    from trw_mcp.shared_server import _cli

    project = _trw_project(tmp_path / "nogit", git=False)
    cwd_env.chdir(project)

    def no_git(*_a: object, **_k: object) -> object:
        raise FileNotFoundError(2, "No such file or directory", "git")

    cwd_env.setattr("subprocess.run", no_git)

    assert _cli.shared_project_root() == project.resolve()


def test_the_proxy_and_the_cli_resolve_one_record_root(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    """PROXY-PROJECT-ROOT (codex KI3): from a project subdirectory the proxy used <subdir>/.trw while swap and
    status used the project root, so a swap could leave the proxy's server untouched (two roots, one project)."""
    from trw_mcp.shared_server import _cli, _proxy

    project = _trw_project(tmp_path / "proj", git=True)
    sub = project / "src" / "pkg"
    sub.mkdir(parents=True)
    cwd_env.chdir(project)  # `trw-mcp swap` run from the project root
    cli_paths, _config, cli_root = _cli._paths()
    cwd_env.chdir(sub)  # a client whose proxy starts in a subdirectory
    proxy_paths, proxy_root = _proxy.proxy_paths()

    assert proxy_root == cli_root == project.resolve()
    assert proxy_paths.record("canary") == cli_paths.record("canary")  # the record swap writes is the one served
    assert proxy_paths.record("canary") == project.resolve() / ".trw" / "runtime" / "shared-mcp" / "canary.json"


def test_the_proxy_outside_a_project_serves_in_process_and_creates_nothing(
    tmp_path: Path, cwd_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No refusal for a client started outside a project: it gets the in-process server, and no record is made."""
    import sys

    from trw_mcp.shared_server import _cli

    plain = tmp_path / "plain"
    plain.mkdir()
    cwd_env.chdir(plain)
    served: list[list[str]] = []
    cwd_env.setattr(sys, "argv", ["trw-mcp-proxy"])
    # Outside a project the root is None, so shared_mcp.enabled is never consulted: no config stub (a global
    # get_config stub broke once another reader on this path wanted a field it lacked).
    cwd_env.setattr("trw_mcp.server._cli.main", lambda: served.append(list(sys.argv)))

    _cli.main_proxy()

    assert served == [["trw-mcp", "serve"]]
    assert "not inside a TRW project" in capsys.readouterr().err
    assert not (plain / ".trw").exists()


def test_a_git_timeout_keeps_the_resolved_root(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    """Codex KI on SWAP-ROOT-CONFIG: a hung git (TimeoutExpired is not an OSError) must not crash the resolver."""
    import subprocess

    from trw_mcp.shared_server._project_root import trw_project_root

    project = _trw_project(tmp_path / "nogit", git=False)
    cwd_env.chdir(project)

    def hung(*_a: object, **_k: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    cwd_env.setattr("subprocess.run", hung)

    assert trw_project_root() == project.resolve()


def test_a_nested_subdir_of_a_non_git_project_walks_up_to_it(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    """INC-126 (e): with no git toplevel to jump to, the resolver walks parents to the first TRW project."""
    from trw_mcp.shared_server._project_root import trw_project_root

    project = _trw_project(tmp_path / "nogit", git=False)
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    cwd_env.chdir(nested)
    cwd_env.setenv("HOME", str(tmp_path))  # the walk runs only beneath $HOME
    cwd_env.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    assert trw_project_root() == project.resolve()


def test_the_walk_up_never_climbs_above_home(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    """A TRW project ABOVE $HOME is never adopted by a directory under it (the lead's cap)."""
    from trw_mcp.shared_server._project_root import trw_project_root

    outer = _trw_project(tmp_path / "outer", git=False)  # a project above HOME
    home = outer / "home" / "user"
    work = home / "work"
    work.mkdir(parents=True)
    cwd_env.setenv("HOME", str(home))
    cwd_env.chdir(work)
    cwd_env.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    assert trw_project_root() is None


def _walk_from(start: Path, cwd_env: pytest.MonkeyPatch, *, home: str | None, ceiling: Path) -> Path | None:
    from trw_mcp.shared_server._project_root import trw_project_root

    if home is None:
        cwd_env.delenv("HOME", raising=False)
    else:
        cwd_env.setenv("HOME", home)
    cwd_env.chdir(start)
    cwd_env.setenv("GIT_CEILING_DIRECTORIES", str(ceiling))
    return trw_project_root()


def test_a_start_outside_home_never_walks(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    """Codex P1 (W5): HOME never appears among an outside-HOME start's parents, so the stop never fired."""
    _trw_project(tmp_path / "srv", git=False)  # a project above an outside-HOME start
    start = tmp_path / "srv" / "team" / "sub"
    start.mkdir(parents=True)
    (tmp_path / "home" / "user").mkdir(parents=True)

    assert _walk_from(start, cwd_env, home=str(tmp_path / "home" / "user"), ceiling=tmp_path) is None


def test_a_start_outside_home_still_finds_itself(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    project = _trw_project(tmp_path / "srv" / "proj", git=False)
    (tmp_path / "home" / "user").mkdir(parents=True)

    assert _walk_from(project, cwd_env, home=str(tmp_path / "home" / "user"), ceiling=tmp_path) == project.resolve()


@pytest.mark.parametrize("home", [None, "", "relative/home"])
def test_an_unusable_home_disables_the_walk(tmp_path: Path, cwd_env: pytest.MonkeyPatch, home: str | None) -> None:
    project = _trw_project(tmp_path / "proj", git=False)
    nested = project / "a"
    nested.mkdir()

    assert _walk_from(nested, cwd_env, home=home, ceiling=tmp_path) is None
    assert _walk_from(project, cwd_env, home=home, ceiling=tmp_path) == project.resolve()


def test_home_itself_can_be_the_project(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    home = _trw_project(tmp_path / "home" / "user", git=False)
    work = home / "work"
    work.mkdir()

    assert _walk_from(work, cwd_env, home=str(home), ceiling=tmp_path) == home.resolve()


def test_the_nearest_of_nested_projects_wins(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    outer = _trw_project(tmp_path / "home" / "outer", git=False)
    inner = _trw_project(outer / "inner", git=False)
    start = inner / "src"
    start.mkdir()

    assert _walk_from(start, cwd_env, home=str(tmp_path / "home"), ceiling=tmp_path) == inner.resolve()


def test_a_symlinked_trw_dir_is_not_a_project(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    """Codex P1 (W5): parent/.trw -> another project's .trw made `parent` a project with no .trw of its own."""
    other = _trw_project(tmp_path / "home" / "other", git=False)
    parent = tmp_path / "home" / "parent"
    start = parent / "sub"
    start.mkdir(parents=True)
    (parent / ".trw").symlink_to(other / ".trw", target_is_directory=True)

    assert _walk_from(start, cwd_env, home=str(tmp_path / "home"), ceiling=tmp_path) is None
    assert _walk_from(parent, cwd_env, home=str(tmp_path / "home"), ceiling=tmp_path) is None


def test_malformed_markers_are_not_a_project(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    as_file = home / "as-file"
    as_file.mkdir(parents=True)
    (as_file / ".trw").write_text("not a dir\n", encoding="utf-8")
    config_dir = home / "config-dir"
    (config_dir / ".trw" / "config.yaml").mkdir(parents=True)

    assert _walk_from(as_file, cwd_env, home=str(home), ceiling=tmp_path) is None
    assert _walk_from(config_dir, cwd_env, home=str(home), ceiling=tmp_path) is None


def test_the_proxy_and_the_cli_agree_from_non_git_subdirs(tmp_path: Path, cwd_env: pytest.MonkeyPatch) -> None:
    from trw_mcp.shared_server import _cli, _proxy

    home = tmp_path / "home"
    project = _trw_project(home / "proj", git=False)
    (project / "a").mkdir()
    (project / "b" / "c").mkdir(parents=True)
    cwd_env.setenv("HOME", str(home))
    cwd_env.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    cwd_env.chdir(project / "a")
    cli_paths, _config, cli_root = _cli._paths()
    cwd_env.chdir(project / "b" / "c")
    proxy_paths, proxy_root = _proxy.proxy_paths()

    assert proxy_root == cli_root == project.resolve()
    assert proxy_paths.record("canary") == cli_paths.record("canary")
    assert not (project / "a" / ".trw").exists() and not (project / "b" / "c" / ".trw").exists()
