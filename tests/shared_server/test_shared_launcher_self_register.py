"""A shared server registers itself as its store's launcher at boot (LAUNCHER-RECORD-SELF-REGISTER).

An env swapped before launcher records existed has none until its next `swap`; the server, and a hot-swap
successor (the same boot path), fill the gap so the daemon-autostart race stays closed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from trw_memory import __version__ as memory_version
from trw_memory.daemon import DaemonPaths
from trw_memory.daemon._launcher_record import read_launcher_record, write_launcher_record

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _ops, _server
from trw_mcp.shared_server._records import SharedPaths, set_env_python

pytestmark = pytest.mark.integration


@pytest.fixture
def paths(tmp_path: Path) -> SharedPaths:
    return SharedPaths.resolve(
        tmp_path / "project" / ".trw", SharedMcpConfig(enabled=True, envs_dir=str(tmp_path / "envs"))
    )


def _store(paths: SharedPaths) -> DaemonPaths:
    memory = paths.user_dir("dev") / "memory"  # type: ignore[operator]
    memory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return DaemonPaths(user_memory_dir=memory)


def test_a_server_running_the_recorded_interpreter_writes_the_missing_record(paths: SharedPaths) -> None:
    set_env_python(paths, "dev", Path(sys.executable), pythonpath="/src/tree")
    store = _store(paths)

    assert _ops.register_launcher(paths, "dev") is True

    record = read_launcher_record(store)
    assert record is not None
    assert (record.python, record.version, record.pythonpath) == (sys.executable, memory_version, "/src/tree")


def test_a_current_record_is_left_alone(paths: SharedPaths) -> None:
    set_env_python(paths, "dev", Path(sys.executable))
    store = _store(paths)
    write_launcher_record(store, Path("/swap/wrote/this"), memory_version)

    assert _ops.register_launcher(paths, "dev") is False

    record = read_launcher_record(store)
    assert record is not None and record.python == "/swap/wrote/this"


def test_a_process_that_is_not_the_recorded_interpreter_writes_nothing(paths: SharedPaths) -> None:
    set_env_python(paths, "dev", Path("/not/this/python"))
    store = _store(paths)

    assert _ops.register_launcher(paths, "dev") is False
    assert read_launcher_record(store) is None


def test_serve_shared_registers_before_it_binds(paths: SharedPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring: boot (and so a hot-swap successor, which boots the same way) calls the registration."""
    import trw_memory.daemon._loopback as loopback

    from trw_mcp.models import config as config_module
    from trw_mcp.state import _paths as state_paths

    calls: list[str] = []
    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda: SimpleNamespace(shared_mcp=SharedMcpConfig(enabled=True), ctx_isolation_enabled=True),
    )
    monkeypatch.setattr(state_paths, "resolve_trw_dir", lambda: paths.root.parent)
    monkeypatch.setattr(_server, "prepare_process_env", lambda: None)
    monkeypatch.setattr(_server, "ensure_token", lambda _paths: "tok")
    monkeypatch.setattr(_server, "SharedPaths", SimpleNamespace(resolve=lambda *_a: paths))
    monkeypatch.setattr(_server, "_register_launcher", lambda _paths, env: calls.append(env))

    def stop(_port: int) -> None:
        raise RuntimeError("bind reached")

    monkeypatch.setattr(loopback, "bind_loopback_socket", stop)

    with pytest.raises(RuntimeError, match="bind reached"):
        _server.serve_shared(env="dev", successor=False)

    assert calls == ["dev"]
