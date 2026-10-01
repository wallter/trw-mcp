"""``swap --version`` installs trw-distill beside trw-mcp so the env's hints are T2 like stable."""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _cli, _ops
from trw_mcp.shared_server._records import SharedPaths, SharedServerError, serving_env_path

pytestmark = pytest.mark.integration

_FORK = "venv-8.0.0+distill-0.9.9"


@pytest.fixture
def paths(tmp_path: Path) -> SharedPaths:
    return SharedPaths.resolve(tmp_path / ".trw", SharedMcpConfig(envs_dir=str(tmp_path / "envs")))


def _wheelhouse(tmp_path: Path, *distill: str) -> SharedMcpConfig:
    house = tmp_path / "wheelhouse"
    house.mkdir()
    (house / "trw_mcp-8.0.0-py3-none-any.whl").touch()
    (house / "other_pkg-9.9.9-py3-none-any.whl").touch()
    for version in distill:
        (house / f"trw_distill-{version}-py3-none-any.whl").touch()
    return SharedMcpConfig(wheelhouse=str(house))


class _Calls(list[list[str]]):
    envs: list[dict[str, str] | None]


def _install_argv(
    monkeypatch: pytest.MonkeyPatch,
    *,
    importable: tuple[str, ...] = (),
    fail_install: bool | Exception = False,
    dist_version: str | None = None,
) -> _Calls:
    """Stub ``_ops._run``: ``uv venv`` creates the dir, ``-c import X`` succeeds for *importable*."""
    calls = _Calls()
    calls.envs = []

    def run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
        calls.append(argv)
        calls.envs.append(env)
        if argv[:3] == ["uv", "pip", "install"] and isinstance(fail_install, Exception):
            raise fail_install
        if argv[1:2] == ["-c"] and "find_spec('sentence_transformers')" in argv[2]:
            return ""  # the embeddings probe: present here, so these tests see no extras install (own test file)
        if argv[1:2] == ["-c"] and "importlib.metadata" in argv[2]:
            if dist_version is None:
                raise SharedServerError("no dist")
            return dist_version
        if argv[:2] == ["uv", "venv"]:
            (Path(argv[-1]) / "bin").mkdir(parents=True)
            (Path(argv[-1]) / "bin" / "python").touch()
        elif argv[:3] == ["uv", "pip", "install"] and fail_install:
            raise SharedServerError("install failed")
        elif argv[1:2] == ["-c"] and argv[2].removeprefix("import ") not in importable:
            raise SharedServerError("no module")
        return ""

    monkeypatch.setattr(_ops, "_run", run)
    return calls


def _installed(calls: _Calls) -> list[str]:
    (install,) = [c for c in calls if c[:3] == ["uv", "pip", "install"]]
    return [a for a in install if a.startswith("trw-")]


def test_version_venv_installs_distill_when_wheelhouse_has_it(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _wheelhouse(tmp_path, "0.9.1.dev2", "0.9.1.dev10", "0.9.0")
    calls = _install_argv(monkeypatch)
    _ops.build_version_venv(paths, "test", "8.0.0", config)
    assert _installed(calls) == ["trw-mcp==8.0.0", "trw-distill==0.9.1.dev10"]


def test_version_ordering_is_parsed_not_string(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _wheelhouse(tmp_path, "0.9.0", "0.10.0")
    calls = _install_argv(monkeypatch)
    _ops.build_version_venv(paths, "test", "8.0.0", config)
    assert _installed(calls)[1] == "trw-distill==0.10.0"


def test_no_distill_wheel_installs_mcp_only_and_says_so(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_argv(monkeypatch)
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert _installed(calls) == ["trw-mcp==8.0.0"]
    captured = capsys.readouterr()
    assert captured.out == ""
    out = captured.err.strip().splitlines()
    assert len(out) == 1 and "distill" in out[0] and "skipped" in out[0]


def test_explicit_with_spec_wins_over_the_wheelhouse(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_argv(monkeypatch)
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"), with_distill="trw-distill==0.8.0")
    assert _installed(calls) == ["trw-mcp==8.0.0", "trw-distill==0.8.0"]


@pytest.mark.parametrize("bad", ["requests==1", "trw-distill", "trw-distill>=1"])
def test_with_option_rejects_anything_but_a_pinned_distill(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    calls = _install_argv(monkeypatch)
    with pytest.raises(SharedServerError, match="--with"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill=bad)
    assert not [c for c in calls if c[:2] == ["uv", "venv"]], "a rejected spec must not create a venv"


def test_a_release_outranks_its_own_dev_builds(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_argv(monkeypatch)
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1.dev17", "0.9.1", "0.9.1rc1"))
    assert _installed(calls)[1] == "trw-distill==0.9.1"


def test_unparseable_and_incompatible_wheels_are_ignored(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _wheelhouse(tmp_path, "0.9.0")
    house = Path(config.wheelhouse)
    (house / "trw_distill-notaversion.whl").touch()
    (house / "trw_distill-99.0-cp2-cp2-plan9_1_x86.whl").touch()
    calls = _install_argv(monkeypatch)
    _ops.build_version_venv(paths, "test", "8.0.0", config)
    assert _installed(calls)[1] == "trw-distill==0.9.0"


def test_install_is_offline_but_keeps_the_uv_cache_for_third_party_deps(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_argv(monkeypatch)
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    (install,) = [c for c in calls if c[:3] == ["uv", "pip", "install"]]
    assert "--offline" in install and "--find-links" in install and "--no-index" not in install


def test_failed_install_leaves_nothing_reusable(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_argv(monkeypatch, fail_install=True)
    with pytest.raises(SharedServerError, match="install failed"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert not (paths.envs_dir / "test" / "venv-8.0.0").exists()


def test_existing_venv_is_reused_and_reports_distill(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    venv = paths.envs_dir / "test" / "venv-8.0.0"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").touch()
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert not [c for c in calls if c[0] == "uv"], "reuse installs nothing"
    assert "NOT installed" in capsys.readouterr().err


def test_explicit_with_on_an_existing_venv_installs_the_pin(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = paths.envs_dir / "test" / "venv-8.0.0"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").touch()
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==0.8.0")
    assert not [c for c in calls if c[:2] == ["uv", "venv"]]
    assert _installed(calls) == ["trw-distill==0.8.0"]


def _old_venv(paths: SharedPaths) -> None:
    venv = paths.envs_dir / "test" / "venv-8.0.0"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").touch()


def test_reused_venv_missing_distill_gets_it_from_the_wheelhouse(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.0", "0.9.1"))
    assert not [c for c in calls if c[:2] == ["uv", "venv"]], "venv is not rebuilt"
    assert _installed(calls) == ["trw-distill==0.9.1"]
    (install,) = [c for c in calls if c[:3] == ["uv", "pip", "install"]]
    assert "--offline" in install and "--find-links" in install and "--no-index" not in install


def test_reused_venv_distill_install_failure_keeps_venv_and_continues(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _old_venv(paths)
    _install_argv(monkeypatch, importable=("trw_mcp",), fail_install=True)
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    assert python.exists()
    err = capsys.readouterr().err
    assert "install of trw-distill==0.9.1 failed: SharedServerError; trw-distill not installed" in err
    assert "NOT installed" in err


def test_reused_venv_already_having_distill_installs_nothing(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.9.1")
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    assert not [c for c in calls if c[0] == "uv"]


def test_unparseable_wheel_name_is_reported_on_stderr(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _wheelhouse(tmp_path, "0.9.0")
    (Path(config.wheelhouse) / "trw_distill-notaversion.whl").touch()
    _install_argv(monkeypatch)
    _ops.build_version_venv(paths, "test", "8.0.0", config)
    assert "trw_distill-notaversion.whl" in capsys.readouterr().err


def _tree(root: Path) -> dict[str, bytes | None]:
    """Every path under *root* (relative) with its bytes (``None`` for a dir): the whole-tree user-bytes snapshot."""
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def test_existing_venv_that_cannot_import_trw_mcp_is_refused_and_nothing_deleted(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = paths.envs_dir / "test" / "venv-8.0.0"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").touch()
    (venv / "user-file").write_bytes(b"precious")
    before = _tree(paths.envs_dir)
    calls = _install_argv(monkeypatch)
    with pytest.raises(SharedServerError, match=r"exists but is not usable; remove it manually"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert not [c for c in calls if c[0] == "uv"]
    assert {k: v for k, v in _tree(paths.envs_dir).items() if not k.endswith(".lock")} == before


def test_half_built_base_venv_without_python_is_refused_untouched(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = paths.envs_dir / "test" / "venv-8.0.0"
    venv.mkdir(parents=True)
    (venv / "half").write_bytes(b"x")
    before = _tree(paths.envs_dir)
    calls = _install_argv(monkeypatch)
    with pytest.raises(SharedServerError, match="not usable"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert not [c for c in calls if c[0] == "uv"]
    assert {k: v for k, v in _tree(paths.envs_dir).items() if not k.endswith(".lock")} == before


def test_missing_wheelhouse_touches_nothing(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_argv(monkeypatch)
    with pytest.raises(SharedServerError, match="no local wheelhouse"):
        _ops.build_version_venv(paths, "test", "8.0.0", SharedMcpConfig(wheelhouse=str(tmp_path / "nope")))
    assert calls == [] and not paths.envs_dir.exists()


def test_a_held_lock_refuses_and_touches_nothing(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    with _ops._venv_lock(paths.envs_dir / "test" / "venv-8.0.0"):
        before = _tree(paths.envs_dir)
        with pytest.raises(SharedServerError, match=r"another swap is building .*venv-8\.0\.0; retry"):
            _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
        assert _tree(paths.envs_dir) == before
    assert not [c for c in calls if c[0] == "uv"]


def _fork_dir(paths: SharedPaths, name: str = _FORK) -> Path:
    fork = paths.envs_dir / "test" / name
    (fork / "bin").mkdir(parents=True)
    (fork / "bin" / "python").touch()
    (fork / "user-file").write_bytes(b"precious")
    return fork


@pytest.mark.parametrize("state", ["no_python", "no_trw_mcp", "wrong_distill"])
def test_unusable_fork_is_refused_and_the_tree_is_byte_identical(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    _old_venv(paths)
    fork = _fork_dir(paths)
    if state == "no_python":
        (fork / "bin" / "python").unlink()
    before = _tree(paths.envs_dir)
    importable = ("trw_mcp", "trw_distill") if state != "no_trw_mcp" else ()
    calls = _install_argv(monkeypatch, importable=importable, dist_version="0.8.0")
    if state == "no_trw_mcp":  # the base must still import so the fork path is reached
        real = _ops._run

        def run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
            if argv[1:2] == ["-c"] and "venv-8.0.0/" in argv[0] and "+distill" not in argv[0]:
                return "0.8.0" if "importlib" in argv[2] else ""
            return real(argv, env=env)

        monkeypatch.setattr(_ops, "_run", run)
    with pytest.raises(SharedServerError, match=r"exists but is not usable"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==0.9.9")
    assert not [c for c in calls if c[:2] in (["uv", "venv"], ["uv", "pip"])]
    assert {k: v for k, v in _tree(paths.envs_dir).items() if not k.endswith(".lock")} == before


def test_sanitised_tags_that_would_collide_get_distinct_dirs(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.8.0")
    plus = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==1+2")
    dot = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse_again(tmp_path), with_distill="trw-distill==1.2")
    assert plus.parent.parent != dot.parent.parent
    assert dot.parent.parent.name == "venv-8.0.0+distill-1.2"


def _wheelhouse_again(tmp_path: Path) -> SharedMcpConfig:
    return SharedMcpConfig(wheelhouse=str(tmp_path / "wheelhouse"))


def test_equal_versions_spelled_differently_do_not_fork(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="1.0.0")
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==1.0")
    assert python.parent.parent.name == "venv-8.0.0" and not [c for c in calls if c[0] == "uv"]


def test_unreadable_installed_version_with_an_auto_wheel_forks(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented: with nothing to compare against, the auto-selected wheel is the best available -> fork."""
    _old_venv(paths)
    _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version=None)
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    assert python.parent.parent.name == "venv-8.0.0+distill-0.9.1"


def test_cli_passes_with_option_through(paths: SharedPaths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    _cli.add_shared_subcommands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["swap", "--env", "dev", "--version", "8.0.0", "--with", "trw-distill==0.8.0"])
    assert args.with_distill == "trw-distill==0.8.0"
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        _cli, "_paths", lambda: (paths, type("C", (), {"shared_mcp": SharedMcpConfig()})(), paths.root.parents[2])
    )
    monkeypatch.setattr(_ops, "build_version_venv", lambda *a, **k: seen.update(k) or Path("/py"))
    monkeypatch.setattr(_ops, "swap", lambda *a, **k: "ok")
    _cli.run_swap(args)
    assert seen == {"with_distill": "trw-distill==0.8.0", "embeddings": True}


def test_with_needs_version(paths: SharedPaths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    _cli.add_shared_subcommands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["swap", "--env", "dev", "--python", str(tmp_path), "--with", "trw-distill==0.8.0"])
    monkeypatch.setattr(
        _cli, "_paths", lambda: (paths, type("C", (), {"shared_mcp": SharedMcpConfig()})(), paths.root.parents[2])
    )
    with pytest.raises(SystemExit):
        _cli.run_swap(args)


def test_env_create_clears_the_recorded_serving_env_and_swap_does_not(
    paths: SharedPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths.root.mkdir(parents=True)
    stale = serving_env_path(paths, "dev")
    stale.write_text("{}")
    other = serving_env_path(paths, "other")
    other.write_text("{}")
    monkeypatch.setattr(_cli, "_paths", lambda: (paths, None, paths.root.parents[2]))
    _ops.ensure_env(paths, "dev", seed_from=None)
    assert stale.exists(), "ensure_env (also reached from swap) must not clear it"
    _cli.run_env(argparse.Namespace(name="dev", seed_from=None))
    assert not stale.exists() and other.exists()
    _cli.run_env(argparse.Namespace(name="dev", seed_from=None))  # absent is fine


def test_version_probe_ignores_the_swapping_shells_pythonpath(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/some/worktree/src")
    monkeypatch.setenv("PYTHONHOME", "/some/home")
    seen: list[dict[str, str] | None] = []

    def run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
        seen.append(env)
        return "8.0.0\n/venv/lib/trw_mcp/__init__.py"

    monkeypatch.setattr(_ops, "_run", run)
    _ops.swap(paths, "dev", Path("/venv/bin/python"), project_root=tmp_path, expect="8.0.0")
    assert seen[0] is not None and "PYTHONPATH" not in seen[0] and "PYTHONHOME" not in seen[0]


def test_src_swap_probe_keeps_its_intended_pythonpath(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    seen: list[dict[str, str] | None] = []

    def run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
        seen.append(env)
        return f"8.0.0\n{src}/trw_mcp/__init__.py"

    monkeypatch.setattr(_ops, "_run", run)
    _ops.swap(paths, "dev", Path("/venv/bin/python"), project_root=tmp_path, expect=None, pythonpath=str(src))
    assert seen[0] is not None and seen[0]["PYTHONPATH"] == str(src)


def _serving_record(paths: SharedPaths, env: str = "test") -> None:
    _old_venv(paths)
    _ops.set_env_python(paths, env, paths.envs_dir / env / "venv-8.0.0" / "bin" / "python", pythonpath=None)


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired("uv", 600), FileNotFoundError("uv")])
def test_reused_venv_survives_timeout_and_missing_uv(
    paths: SharedPaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exc: Exception,
) -> None:
    _old_venv(paths)
    _install_argv(monkeypatch, importable=("trw_mcp",), fail_install=exc)
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    assert python.exists()
    assert f"failed: {type(exc).__name__}; trw-distill not installed" in capsys.readouterr().err


def test_reused_venv_no_spec_and_no_distill_reports_once_without_install(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert not [c for c in calls if c[0] == "uv"]
    assert capsys.readouterr().err.count("NOT installed") == 1


def test_failed_explicit_with_on_an_absent_distill_refuses_and_keeps_the_venv(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _old_venv(paths)
    _install_argv(monkeypatch, importable=("trw_mcp",), fail_install=SharedServerError("boom"))
    with pytest.raises(SharedServerError, match="kept, nothing swapped"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==0.9.9")
    assert (paths.envs_dir / "test" / "venv-8.0.0" / "bin" / "python").exists()
    assert (
        "install of trw-distill==0.9.9 failed: SharedServerError; trw-distill not installed" in capsys.readouterr().err
    )


def test_partial_install_is_uninstalled_best_effort(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _old_venv(paths)
    calls = _install_argv(
        monkeypatch, importable=("trw_mcp",), fail_install=SharedServerError("boom"), dist_version="0.9.1"
    )
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    assert [c for c in calls if c[:3] == ["uv", "pip", "uninstall"]]
    assert "trw-distill not installed" in capsys.readouterr().err


@pytest.mark.parametrize("reuse", [False, True])
def test_install_env_has_no_swapper_pythonpath(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reuse: bool
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/swapper/src")
    monkeypatch.setenv("PYTHONHOME", "/swapper/home")
    if reuse:
        _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    (env,) = [e for c, e in zip(calls, calls.envs, strict=True) if c[:3] == ["uv", "pip", "install"]]
    assert env is not None and "PYTHONPATH" not in env and "PYTHONHOME" not in env


def test_serving_venv_without_distill_still_gets_it(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serving_record(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    assert _installed(calls) == ["trw-distill==0.9.1"]


@pytest.mark.parametrize("serving", [True, False])
def test_changed_distill_pin_builds_a_fresh_venv_and_leaves_the_old_one_untouched(
    paths: SharedPaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    serving: bool,
) -> None:
    (_serving_record if serving else _old_venv)(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.8.0")
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==0.9.9")
    old, fresh = paths.envs_dir / "test" / "venv-8.0.0", paths.envs_dir / "test" / _FORK
    assert python == fresh / "bin" / "python" and python.exists()
    assert [c for c in calls if c[:2] == ["uv", "venv"]] == [["uv", "venv", "--quiet", str(fresh)]]
    assert _installed(calls) == ["trw-mcp==8.0.0", "trw-distill==0.9.9"]
    (install,) = [c for c in calls if c[:3] == ["uv", "pip", "install"]]
    assert str(fresh / "bin" / "python") in install and str(old / "bin" / "python") not in install
    assert (old / "bin" / "python").exists()
    assert f"old venv kept at {old}" in capsys.readouterr().err


def test_auto_selected_wheel_that_differs_builds_a_fresh_venv(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serving_record(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.9.1.dev20")
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1.dev20", "0.9.1.dev21"))
    assert python.parent.parent.name == "venv-8.0.0+distill-0.9.1.dev21"
    assert _installed(calls) == ["trw-mcp==8.0.0", "trw-distill==0.9.1.dev21"]


def test_auto_selected_wheel_older_than_installed_reuses_without_a_fork(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serving_record(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.9.1.dev21")
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1.dev3", "0.9.1.dev9"))
    assert python.parent.parent.name == "venv-8.0.0"
    assert not [c for c in calls if c[0] == "uv"]


def test_explicit_older_pin_forks_even_though_it_is_a_downgrade(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.9.9")
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==0.8.0")
    assert python.parent.parent.name == "venv-8.0.0+distill-0.8.0"
    assert _installed(calls) == ["trw-mcp==8.0.0", "trw-distill==0.8.0"]


def test_joined_venv_name_too_long_is_refused_before_any_build(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.8.0")
    with pytest.raises(SharedServerError, match=r"cannot name a fresh venv.*nothing swapped"):
        _ops.build_version_venv(
            paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==0.9.1.dev21.post7.abcdef"
        )
    assert not [c for c in calls if c[0] == "uv"]


def test_matching_auto_selected_wheel_reuses_without_a_build(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.9.1")
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    assert python.parent.parent.name == "venv-8.0.0"
    assert not [c for c in calls if c[0] == "uv"]


def test_failed_fresh_build_leaves_old_venv_and_env_record_unchanged(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serving_record(paths)
    before = _ops.read_env_map(paths)
    _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), fail_install=True, dist_version="0.8.0")
    with pytest.raises(SharedServerError, match="install failed"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==0.9.9")
    assert not (paths.envs_dir / "test" / _FORK).exists()
    assert (paths.envs_dir / "test" / "venv-8.0.0" / "bin" / "python").exists()
    assert _ops.read_env_map(paths) == before


def test_second_swap_to_the_same_changed_distill_reuses_the_fresh_venv(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    fresh = paths.envs_dir / "test" / _FORK / "bin"
    fresh.mkdir(parents=True)
    (fresh / "python").touch()
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.8.0")
    dist = {"venv-8.0.0/bin/python": "0.8.0", f"{_FORK}/bin/python": "0.9.9"}

    def run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
        calls.append(argv)
        if argv[1:2] == ["-c"] and "find_spec('sentence_transformers')" in argv[2]:
            return ""  # the embeddings probe: present here, so these tests see no extras install (own test file)
        if argv[1:2] == ["-c"] and "importlib.metadata" in argv[2]:
            return next(v for k, v in dist.items() if argv[0].endswith(k))
        return ""

    monkeypatch.setattr(_ops, "_run", run)
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==0.9.9")
    assert python == fresh / "python"
    assert not [c for c in calls if c[0] == "uv"]


def test_serving_venv_with_same_pin_is_a_silent_no_op(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serving_record(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version="0.9.9")
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill==0.9.9")
    assert not [c for c in calls if c[:3] == ["uv", "pip", "install"]]
    assert "skipped" not in capsys.readouterr().err


def test_invalid_with_is_refused_before_any_probe_on_a_reused_venv(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    with pytest.raises(SharedServerError, match="pinned spec"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path), with_distill="trw-distill>=1")
    assert calls == []


def _same_bytes(paths: SharedPaths, before: dict[str, bytes | None]) -> bool:
    return {k: v for k, v in _tree(paths.envs_dir).items() if not k.endswith(".lock")} == before


@contextlib.contextmanager
def _mode(path: Path, mode: int) -> Iterator[None]:
    """chmod *path* to *mode* for the block (never root-dependent), always restored so cleanup works."""
    original = path.stat().st_mode & 0o777
    path.chmod(mode)
    try:
        yield
    finally:
        path.chmod(original)


def test_a_stale_lock_file_from_a_dead_swap_does_not_block_and_is_kept(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    stale = paths.envs_dir / "test" / "venv-8.0.0.lock"
    stale.write_bytes(b"left by a dead swap")
    _install_argv(monkeypatch, importable=("trw_mcp",))
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert stale.read_bytes() == b"left by a dead swap"


def test_a_live_holders_lock_file_survives_a_refused_second_swap(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    _install_argv(monkeypatch, importable=("trw_mcp",))
    lock = paths.envs_dir / "test" / "venv-8.0.0.lock"
    with _ops._venv_lock(paths.envs_dir / "test" / "venv-8.0.0"):
        with pytest.raises(SharedServerError, match="another swap is building"):
            _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
        assert lock.exists(), "the refused swap must not delete the live holder's lock file"
    assert lock.exists()


def test_unreadable_venv_dir_is_refused_with_the_tree_unchanged(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    venv = paths.envs_dir / "test" / "venv-8.0.0"
    (venv / "user-file").write_bytes(b"precious")
    before = _tree(paths.envs_dir)
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    with _mode(venv, 0o000), pytest.raises(SharedServerError, match="cannot inspect"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert not [c for c in calls if c[0] == "uv"] and _same_bytes(paths, before)


def test_non_executable_venv_python_is_unusable_and_nothing_is_deleted(paths: SharedPaths, tmp_path: Path) -> None:
    _old_venv(paths)  # real _run: the empty python file cannot be executed (PermissionError / ENOEXEC)
    venv = paths.envs_dir / "test" / "venv-8.0.0"
    (venv / "bin" / "python").chmod(0o644)
    (venv / "user-file").write_bytes(b"precious")
    before = _tree(paths.envs_dir)
    with pytest.raises(SharedServerError, match="not usable"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert _same_bytes(paths, before)


def test_unreadable_distill_metadata_never_touches_the_base_venv(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The metadata probe fails (unreadable site-packages): the base is untouched; a fork is a NEW dir only."""
    _old_venv(paths)
    before = _tree(paths.envs_dir)
    _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version=None)
    python = _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    assert python.parent.parent.name == "venv-8.0.0+distill-0.9.1"
    after = {k: v for k, v in _tree(paths.envs_dir).items() if not k.endswith(".lock")}
    assert all(after[k] == v for k, v in before.items())


def test_fork_with_unreadable_distill_metadata_is_refused_untouched(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    _fork_dir(paths, "venv-8.0.0+distill-0.9.1")
    before = _tree(paths.envs_dir)
    _install_argv(monkeypatch, importable=("trw_mcp", "trw_distill"), dist_version=None)
    with pytest.raises(SharedServerError, match="not usable"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path, "0.9.1"))
    assert _same_bytes(paths, before)


def test_unreadable_parent_dir_is_refused_and_nothing_is_touched(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    before = _tree(paths.envs_dir)
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    with _mode(paths.envs_dir / "test", 0o000), pytest.raises(SharedServerError, match=r"cannot (lock|inspect)"):
        _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert not [c for c in calls if c[0] == "uv"] and _same_bytes(paths, before)


def test_venv_replaced_between_the_probe_and_the_reuse_decision_is_never_deleted(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the lock the only racer is a manual actor: swap the dir in the middle of the usability probe."""
    _old_venv(paths)
    venv = paths.envs_dir / "test" / "venv-8.0.0"
    calls = _install_argv(monkeypatch, importable=("trw_mcp",))
    removed: list[Path] = []
    monkeypatch.setattr(_ops, "remove_tree", lambda path, **_: removed.append(path))
    real_importable = _ops._importable
    raced: list[bool] = []

    def racing(python: Path, module: str) -> bool:
        answer = real_importable(python, module)
        if module == "trw_mcp" and not raced:
            raced.append(True)
            os.replace(venv, venv.with_name("venv-8.0.0.moved"))
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").touch()
            (venv / "replacement").write_bytes(b"manual actor's venv")
        return answer

    monkeypatch.setattr(_ops, "_importable", racing)
    _ops.build_version_venv(paths, "test", "8.0.0", _wheelhouse(tmp_path))
    assert raced, "the interleaving must actually have run"
    assert removed == [] and (venv / "replacement").read_bytes() == b"manual actor's venv"
    assert (venv.with_name("venv-8.0.0.moved") / "bin" / "python").exists()
    assert not [c for c in calls if c[:2] == ["uv", "venv"]]
