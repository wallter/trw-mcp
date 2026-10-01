"""A memory client autostarts the daemon through the shared env's recorded interpreter (DAEMON-AUTOSTART-VERSION-RACE).

2026-09-30: after a drain of the 8.1.2 stable server's daemon, a repo-``.venv`` client (trw-memory 5.1.0.dev10)
called first and published an OLDER daemon beside the stable server. When an env record names the interpreter
that serves the daemon's store, every client that autostarts uses that interpreter and never ``sys.executable``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from trw_memory.daemon import DaemonPaths
from trw_memory.exceptions import DaemonUnreachableError

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _daemon_launch
from trw_mcp.shared_server._records import SharedPaths, set_env_python

pytestmark = pytest.mark.integration

_ENV_PYTHON = "/envs/stable/venv-8.1.2/bin/python"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private user home: the default (stable) memory dir is ``<home>/.trw/memory``."""
    for name in ("TRW_USER_DIR", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def config(tmp_path: Path) -> SharedMcpConfig:
    return SharedMcpConfig(enabled=True, envs_dir=str(tmp_path / "envs"))


@pytest.fixture
def paths(tmp_path: Path, config: SharedMcpConfig) -> SharedPaths:
    return SharedPaths.resolve(tmp_path / "project" / ".trw", config)


class _Start:
    """Records ``start_daemon_detached`` calls; returns a marker instead of a process."""

    def __init__(self) -> None:
        self.calls: list[tuple[DaemonPaths, dict[str, Any]]] = []

    def __call__(self, daemon_paths: DaemonPaths, **kwargs: Any) -> str:
        self.calls.append((daemon_paths, kwargs))
        return "spawned"


@pytest.fixture
def start(monkeypatch: pytest.MonkeyPatch) -> _Start:
    start = _Start()
    monkeypatch.setattr(_daemon_launch, "start_daemon_detached", start)
    return start


def _python(tmp_path: Path, name: str = "stable") -> Path:
    python = tmp_path / "envs" / name / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    return python


def _launch(tmp_path: Path, config: SharedMcpConfig, daemon_paths: DaemonPaths) -> Any:
    launcher = _daemon_launch.shared_env_launcher(tmp_path / "project" / ".trw", config)
    assert launcher is not None
    return launcher(daemon_paths)


def test_a_stable_record_starts_the_default_stores_daemon_from_the_recorded_interpreter(
    tmp_path: Path, home: Path, config: SharedMcpConfig, paths: SharedPaths, start: _Start,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    python = _python(tmp_path)
    set_env_python(paths, "stable", python)
    monkeypatch.setenv("PYTHONPATH", "/repo/.venv-client/worktree/src")
    daemon_paths = DaemonPaths(user_memory_dir=(home / ".trw" / "memory").resolve())

    assert _launch(tmp_path, config, daemon_paths) == "spawned"

    ((called_paths, kwargs),) = start.calls
    assert called_paths == daemon_paths
    assert kwargs["python"] == str(python), "never the caller's own interpreter"
    assert kwargs["python"] != sys.executable
    assert "PYTHONPATH" not in kwargs["environ"], "the client's worktree source must not reach the env's daemon"
    assert "TRW_USER_DIR" not in kwargs["environ"]


def test_a_non_stable_env_gets_its_own_interpreter_store_and_pythonpath(
    tmp_path: Path, home: Path, config: SharedMcpConfig, paths: SharedPaths, start: _Start
) -> None:
    stable, canary = _python(tmp_path, "stable"), _python(tmp_path, "canary")
    set_env_python(paths, "stable", stable)
    set_env_python(paths, "canary", canary, pythonpath="/wt/trw-mcp/src:/wt/trw-memory/src")
    canary_memory = tmp_path / "envs" / "canary" / "memory"
    canary_memory.mkdir(parents=True)

    _launch(tmp_path, config, DaemonPaths(user_memory_dir=canary_memory.resolve()))

    ((_, kwargs),) = start.calls
    assert kwargs["python"] == str(canary)
    assert kwargs["environ"]["PYTHONPATH"] == "/wt/trw-mcp/src:/wt/trw-memory/src"
    assert kwargs["environ"]["TRW_USER_DIR"] == str(tmp_path / "envs" / "canary")


def test_a_store_that_belongs_to_no_env_keeps_the_clients_own_start(
    tmp_path: Path, home: Path, config: SharedMcpConfig, paths: SharedPaths, start: _Start,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    set_env_python(paths, "stable", _python(tmp_path))
    elsewhere = tmp_path / "scratch-store" / "memory"
    elsewhere.mkdir(parents=True)
    monkeypatch.setenv("TRW_USER_DIR", str(elsewhere.parent))

    _launch(tmp_path, config, DaemonPaths(user_memory_dir=elsewhere.resolve()))

    assert start.calls == [(DaemonPaths(user_memory_dir=elsewhere.resolve()), {})]


def test_no_record_for_the_env_keeps_the_clients_own_start(
    tmp_path: Path, home: Path, config: SharedMcpConfig, start: _Start
) -> None:
    daemon_paths = DaemonPaths(user_memory_dir=(home / ".trw" / "memory").resolve())

    _launch(tmp_path, config, daemon_paths)

    assert start.calls == [(daemon_paths, {})]


def test_shared_mcp_off_is_the_rollback_lever_and_no_launcher_is_built(tmp_path: Path) -> None:
    assert _daemon_launch.shared_env_launcher(tmp_path / ".trw", SharedMcpConfig(enabled=False)) is None


def test_a_recorded_interpreter_that_is_gone_refuses_instead_of_falling_back(
    tmp_path: Path, home: Path, config: SharedMcpConfig, paths: SharedPaths, start: _Start
) -> None:
    set_env_python(paths, "stable", tmp_path / "envs" / "stable" / "venv-gone" / "bin" / "python")

    with pytest.raises(DaemonUnreachableError, match=r"venv-gone.*does not exist.*trw-mcp swap --env stable"):
        _launch(tmp_path, config, DaemonPaths(user_memory_dir=(home / ".trw" / "memory").resolve()))

    assert start.calls == [], "falling back to this interpreter is the bug"


def test_an_unreadable_env_map_refuses_instead_of_falling_back(
    tmp_path: Path, home: Path, config: SharedMcpConfig, paths: SharedPaths, start: _Start
) -> None:
    paths.root.mkdir(parents=True)
    (paths.root / "envs.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(DaemonUnreachableError, match=r"envs\.json"):
        _launch(tmp_path, config, DaemonPaths(user_memory_dir=(home / ".trw" / "memory").resolve()))

    assert start.calls == []


@pytest.mark.parametrize("entry", [None, 7, ["python"]], ids=["null", "int", "list"])
def test_a_malformed_interpreter_entry_refuses_like_a_missing_one(
    tmp_path: Path, home: Path, config: SharedMcpConfig, paths: SharedPaths, start: _Start, entry: Any
) -> None:
    paths.root.mkdir(parents=True)
    (paths.root / "envs.json").write_text(json.dumps({"stable": entry}), encoding="utf-8")

    with pytest.raises(DaemonUnreachableError, match="does not exist"):
        _launch(tmp_path, config, DaemonPaths(user_memory_dir=(home / ".trw" / "memory").resolve()))

    assert start.calls == []


class _Client:
    def __init__(self, token: str, **kwargs: Any) -> None:
        self.token, self.kwargs = token, kwargs


@pytest.fixture
def plain_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_memory.daemon.client.DaemonClient", _Client)


def _record_env_map(paths: SharedPaths, tmp_path: Path) -> None:
    set_env_python(paths, "stable", _python(tmp_path))


def test_daemon_client_passes_a_launcher_when_a_shared_env_is_recorded_and_enabled(
    tmp_path: Path, config: SharedMcpConfig, paths: SharedPaths, plain_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_env_map(paths, tmp_path)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: type("C", (), {"shared_mcp": config})())

    client = _daemon_launch.daemon_client("grant", tmp_path / "project" / ".trw", instance=(1, "a"))

    assert client.token == "grant"
    assert client.kwargs["instance"] == (1, "a") and callable(client.kwargs["launcher"])


def test_daemon_client_is_plain_when_shared_mcp_is_off_even_with_a_recorded_env(
    tmp_path: Path, paths: SharedPaths, plain_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_env_map(paths, tmp_path)
    off = SharedMcpConfig(enabled=False)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: type("C", (), {"shared_mcp": off})())

    client = _daemon_launch.daemon_client("grant", tmp_path / "project" / ".trw", keep_session=True)

    assert client.kwargs == {"keep_session": True}, "the rollback lever: no new keyword reaches a plain client"


def test_daemon_client_without_a_recorded_env_never_reads_the_config(
    tmp_path: Path, plain_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> None:
        raise AssertionError("the configless path read the project config")

    monkeypatch.setattr("trw_mcp.models.config.get_config", forbidden)

    client = _daemon_launch.daemon_client("grant", tmp_path / "project" / ".trw", keep_session=True)

    assert client.kwargs == {"keep_session": True}


def test_the_cheap_probe_names_the_file_the_records_module_writes(
    tmp_path: Path, config: SharedMcpConfig, paths: SharedPaths
) -> None:
    set_env_python(paths, "stable", _python(tmp_path))

    assert (tmp_path / "project" / ".trw" / _daemon_launch._ENV_MAP).is_file()
    assert paths.root / "envs.json" == tmp_path / "project" / ".trw" / _daemon_launch._ENV_MAP


def test_importing_and_using_daemon_client_without_a_shared_env_loads_no_config_or_records(tmp_path: Path) -> None:
    """The hook's fast recall read goes through here: TRWConfig costs ~110 ms (S3c) and must not load."""
    program = (
        "import sys\n"
        "from pathlib import Path\n"
        "from trw_mcp.shared_server._daemon_launch import daemon_client\n"
        f"daemon_client('grant', Path({str(tmp_path / '.trw')!r}))\n"
        "heavy = [m for m in ('trw_mcp.models.config', 'trw_mcp.shared_server._records', 'fastmcp') if m in sys.modules]\n"
        "print(heavy)\n"
    )
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True, timeout=120)

    assert done.stdout.strip().splitlines()[-1] == "[]"
