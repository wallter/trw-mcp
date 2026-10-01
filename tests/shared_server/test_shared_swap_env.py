"""The serving environment is recorded at an env's first start and replayed for swap successors."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from trw_memory.daemon._paths import write_secret_file

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _proxy
from trw_mcp.shared_server._records import (
    SharedPaths,
    SharedServerError,
    env_serving_env,
    serving_env_path,
    set_env_python,
)


@pytest.fixture
def paths(tmp_path: Path) -> SharedPaths:
    p = SharedPaths.resolve(tmp_path / ".trw", SharedMcpConfig(envs_dir=str(tmp_path / "envs")))
    p.root.mkdir(parents=True, exist_ok=True)
    set_env_python(p, "stable", Path("/py"))
    return p


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class _Proc:
        def wait(self) -> int:
            return 0

    def fake_popen(argv: list[str], **kwargs: Any) -> _Proc:
        seen["env"] = kwargs["env"]
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return seen


def _first_start(paths: SharedPaths, env: str, root: Path, monkeypatch: pytest.MonkeyPatch, *, ok: bool = True) -> None:
    """Drive EnvResolver._start with a fake server that does (or does not) publish."""

    def fake_wait(*_a: Any, **_k: Any) -> Any:
        if not ok:
            raise SharedServerError("never published")
        return object()

    monkeypatch.setattr(_proxy, "wait_published", fake_wait)
    resolver = _proxy.EnvResolver(paths, env, project_root=str(root))
    if ok:
        resolver._start()
    else:
        with pytest.raises(SharedServerError):
            resolver._start()


def test_successor_replays_recorded_env_not_swapper_env(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: dict[str, Any]
) -> None:
    monkeypatch.setenv("TRW_USER_DIR", "/serving/user-dir")
    monkeypatch.setenv("TRW_ONLY_AT_START", "1")
    _first_start(paths, "stable", tmp_path, monkeypatch)
    monkeypatch.setenv("TRW_USER_DIR", "/swapper/user-dir")
    monkeypatch.delenv("TRW_ONLY_AT_START")
    monkeypatch.setenv("TRW_SWAPPER_ONLY", "1")
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert spawned["env"]["TRW_USER_DIR"] == "/serving/user-dir"
    assert spawned["env"]["TRW_ONLY_AT_START"] == "1"
    assert "TRW_SWAPPER_ONLY" not in spawned["env"]


def test_named_env_user_dir_is_pinned_regardless_of_the_swapper_shell(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: dict[str, Any]
) -> None:
    """Named envs already override TRW_USER_DIR from paths.user_dir; the recorded env matters for stable."""
    set_env_python(paths, "dev", Path("/py"))
    (paths.user_dir("dev") / "memory").mkdir(parents=True)  # type: ignore[operator]
    monkeypatch.setenv("TRW_USER_DIR", "/tmp/x")
    _proxy.spawn_server(paths, "dev", project_root=str(tmp_path), successor=True)
    assert spawned["env"]["TRW_USER_DIR"] == str(paths.user_dir("dev"))


def test_record_is_first_start_only_and_excludes_secrets(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: dict[str, Any]
) -> None:
    monkeypatch.setenv("TRW_USER_DIR", "/first")
    monkeypatch.setenv("TRW_API_KEY", "s3cret")
    monkeypatch.setenv("TRW_DB_DSN", "s3cret-dsn")
    monkeypatch.setenv("TRW_BACKEND_URL", "https://u:s3cret-pw@host/x")
    monkeypatch.setenv("UNLISTED_VAR", "x")
    _first_start(paths, "stable", tmp_path, monkeypatch)
    monkeypatch.setenv("TRW_USER_DIR", "/second")
    _first_start(paths, "stable", tmp_path, monkeypatch)  # not the first start
    monkeypatch.setenv("TRW_API_KEY", "rotated")
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert spawned["env"]["TRW_USER_DIR"] == "/first"
    assert spawned["env"]["TRW_API_KEY"] == "rotated", "secrets are never recorded; the swapper's is used"
    assert spawned["env"]["UNLISTED_VAR"] == "x"
    assert "s3cret" not in serving_env_path(paths, "stable").read_text()


def test_env_without_a_record_falls_back_to_the_swapper_env(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: dict[str, Any]
) -> None:
    monkeypatch.setenv("TRW_USER_DIR", "/swapper")
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert spawned["env"]["TRW_USER_DIR"] == "/swapper"
    assert not serving_env_path(paths, "stable").exists()


@pytest.mark.parametrize("junk", ["{not json", "[1, 2]", '{"TRW_X": 3}', ""])
def test_corrupt_record_does_not_break_spawn(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: dict[str, Any], junk: str
) -> None:
    serving_env_path(paths, "stable").write_text(junk)
    monkeypatch.setenv("TRW_USER_DIR", "/swapper")
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert spawned["env"]["TRW_USER_DIR"] == "/swapper"


def test_failed_first_start_records_nothing(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: dict[str, Any]
) -> None:
    monkeypatch.setenv("TRW_USER_DIR", "/failed")
    _first_start(paths, "stable", tmp_path, monkeypatch, ok=False)
    assert not serving_env_path(paths, "stable").exists()


def test_two_envs_record_independently(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: dict[str, Any]
) -> None:
    set_env_python(paths, "dev", Path("/py"))
    (paths.user_dir("dev") / "memory").mkdir(parents=True)  # type: ignore[operator]
    monkeypatch.setenv("TRW_MARK", "a")
    _first_start(paths, "stable", tmp_path, monkeypatch)
    monkeypatch.setenv("TRW_MARK", "b")
    _first_start(paths, "dev", tmp_path, monkeypatch)
    monkeypatch.setenv("TRW_MARK", "swapper")
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert spawned["env"]["TRW_MARK"] == "a"
    _proxy.spawn_server(paths, "dev", project_root=str(tmp_path), successor=True)
    assert spawned["env"]["TRW_MARK"] == "b"
    assert paths.envs() == ["dev", "stable"], "the record file is not mistaken for an env"


def test_swap_src_pythonpath_wins_over_the_recorded_one(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: dict[str, Any]
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/first/A")
    _first_start(paths, "stable", tmp_path, monkeypatch)
    set_env_python(paths, "stable", Path("/py"), pythonpath="/wt/B/trw-mcp/src:/wt/B/trw-memory/src")
    monkeypatch.setenv("PYTHONPATH", "/swapper/C")
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert spawned["env"]["PYTHONPATH"].startswith("/wt/B/")
    set_env_python(paths, "stable", Path("/py"))  # a --python swap: no source, so no PYTHONPATH at all
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert "PYTHONPATH" not in spawned["env"]


def test_a_legacy_record_holding_pythonpath_still_loads_without_it(paths: SharedPaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    write_secret_file(serving_env_path(paths, "stable"), '{"PYTHONPATH": "/old", "TRW_X": "1"}')
    assert env_serving_env(paths, "stable") == {"TRW_X": "1"}


@pytest.mark.parametrize(("pythonpath", "expected"), [(None, None), ("/wt/B/src", "/wt/B/src")])
def test_child_pythonpath_comes_from_the_swap_record_only(
    paths: SharedPaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spawned: dict[str, Any],
    pythonpath: str | None,
    expected: str | None,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/swapper/X")
    monkeypatch.setenv("PYTHONHOME", "/swapper/home")
    set_env_python(paths, "stable", Path("/py"), pythonpath=pythonpath)
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert spawned["env"].get("PYTHONPATH") == expected and "PYTHONHOME" not in spawned["env"]


@pytest.mark.parametrize(
    "payload",
    ['{"TRW_X": "a\\u0000b"}', '{"OTHER": "v"}', '{"TRW_API_KEY": "v"}', '{"TRW_URL": "https://u:p@h/x"}'],
)
def test_record_with_a_disallowed_entry_is_no_record(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: dict[str, Any], payload: str
) -> None:
    serving_env_path(paths, "stable").write_text(payload)
    monkeypatch.setenv("TRW_USER_DIR", "/swapper")
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert spawned["env"]["TRW_USER_DIR"] == "/swapper"
    assert "OTHER" not in spawned["env"]
    assert env_serving_env(paths, "stable") is None


def test_corrupt_record_warning_never_reaches_stdout(paths: SharedPaths, capsys: pytest.CaptureFixture[str]) -> None:
    serving_env_path(paths, "stable").write_text("{not json")
    assert env_serving_env(paths, "stable") is None
    out = capsys.readouterr()
    assert out.out == ""
    assert "unreadable" in out.err


def test_swap_writes_the_stores_launcher_record(paths: SharedPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clients (trw-mcp and the trw-memory CLI) autostart from the record `swap` writes, not from envs.json."""
    from trw_memory.daemon import DaemonPaths
    from trw_memory.daemon._launcher_record import read_launcher_record

    from trw_mcp.shared_server import _ops

    python = Path("/envs/dev/bin/python")
    monkeypatch.setattr(_ops, "env_memory_version", lambda *_a: "7.7.7")
    (paths.user_dir("dev") / "memory").mkdir(parents=True)  # type: ignore[operator]

    _ops._record_launcher(paths, "dev", python, "/src/tree")

    record = read_launcher_record(DaemonPaths(user_memory_dir=_ops._memory_dir(paths, "dev")))
    assert record is not None
    assert (record.python, record.version, record.pythonpath) == (str(python), "7.7.7", "/src/tree")
