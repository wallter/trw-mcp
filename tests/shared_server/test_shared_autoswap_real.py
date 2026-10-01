"""HOTSWAP-AUTO's real ports: installed-version reads, the interpreter probe, the locks and the persisted swap row."""

from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _autoswap, _autoswap_ports
from trw_mcp.shared_server._autoswap import HotSwap
from trw_mcp.shared_server._records import SharedPaths

pytestmark = pytest.mark.integration


@pytest.fixture
def paths(tmp_path: Path) -> SharedPaths:
    return SharedPaths.resolve(tmp_path / ".trw", SharedMcpConfig(envs_dir=str(tmp_path / "envs")))


def test_last_auto_swap_reads_swapping_as_swapped_once_the_record_names_another_server(paths: SharedPaths) -> None:
    row = {"from": "1", "to": "2", "at": "t", "outcome": "swapping", "from_pid": 10, "to_pid": None}
    _autoswap.write_last_auto_swap(paths, "stable", row)
    assert _autoswap.read_last_auto_swap(paths, "stable", live_pid=10)["outcome"] == "swapping"
    assert _autoswap.read_last_auto_swap(paths, "stable", live_pid=11)["outcome"] == "swapped"
    assert paths.root.joinpath("stable.last-auto-swap").stat().st_mode & 0o777 == 0o600


def test_last_auto_swap_absent_or_unreadable_reads_as_none(paths: SharedPaths) -> None:
    assert _autoswap.read_last_auto_swap(paths, "stable", live_pid=1) is None
    paths.root.mkdir(parents=True)
    paths.root.joinpath("stable.last-auto-swap").write_text("{not json", encoding="utf-8")
    assert _autoswap.read_last_auto_swap(paths, "stable", live_pid=1) is None


def test_the_surface_block_says_what_happened_and_is_absent_outside_a_shared_server(
    paths: SharedPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_autoswap, "_ACTIVE", None)
    assert _autoswap.surface_block() is None
    _autoswap.write_last_auto_swap(
        paths, "stable", {"from": "8.0.0", "to": "8.1.1", "at": "t", "outcome": "swapped", "from_pid": 1, "to_pid": 2}
    )
    monkeypatch.setattr(
        _autoswap,
        "_ACTIVE",
        _autoswap._Active(env="stable", paths=paths, booted={"trw-mcp": "8.1.1"}, status=lambda: {"state": "watching"}),
    )
    block = _autoswap.surface_block()
    assert block is not None
    assert block["booted_version"] == "8.1.1" and block["auto_swap"] == {"state": "watching"}
    assert block["last_auto_swap"]["from"] == "8.0.0" and block["last_auto_swap"]["outcome"] == "swapped"


def test_installed_versions_reads_the_watched_distributions() -> None:
    versions = _autoswap.installed_versions()
    assert versions["trw-mcp"] and versions["trw-memory"]
    assert set(versions) <= {"trw-mcp", "trw-memory", "trw-distill"}


def test_the_probe_reports_the_interpreters_versions_and_refuses_a_broken_one(tmp_path: Path) -> None:
    probed = _autoswap.probe_interpreter(sys.executable, None)
    assert probed is not None and probed["trw-mcp"] and probed["trw-memory"]
    assert _autoswap.probe_interpreter(str(tmp_path / "missing" / "python"), None) is None
    not_python = tmp_path / "python"
    not_python.write_text("#!/bin/sh\necho not-json\n", encoding="utf-8")
    not_python.chmod(0o755)
    assert _autoswap.probe_interpreter(str(not_python), None) is None


def test_the_booted_versions_match_what_a_probe_of_the_same_code_reports() -> None:
    import trw_memory

    import trw_mcp

    # The probe runs a clean environment (as the successor's), so it is pointed at the code this process imported.
    roots = os.pathsep.join(str(Path(m.__file__).resolve().parents[1]) for m in (trw_mcp, trw_memory) if m.__file__)
    assert _autoswap.booted_versions() == _autoswap.probe_interpreter(sys.executable, roots)


def test_the_claim_lock_is_busy_only_while_a_successor_holds_it(paths: SharedPaths) -> None:
    assert _autoswap.claim_busy(paths, "stable") is False
    paths.root.mkdir(parents=True, exist_ok=True)
    with open(paths.lock("stable"), "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert _autoswap.claim_busy(paths, "stable") is True
    assert _autoswap.claim_busy(paths, "stable") is False


def test_the_spawn_lock_is_exclusive_and_never_blocks(paths: SharedPaths) -> None:
    with _autoswap.spawn_lock(paths, "stable") as first:
        assert first is True
        with _autoswap.spawn_lock(paths, "stable") as second:
            assert second is False
    with _autoswap.spawn_lock(paths, "stable") as again:
        assert again is True


def test_the_disabled_flag_builds_no_watcher(paths: SharedPaths) -> None:
    door = SimpleNamespace(draining=False)
    off = SharedMcpConfig(auto_swap=False)
    assert _autoswap.build_hot_swap("stable", door, paths, off, project_root=paths.root) is None
    on = SharedMcpConfig(auto_swap=True)
    assert isinstance(_autoswap.build_hot_swap("stable", door, paths, on, project_root=paths.root), HotSwap)


def test_the_persisted_row_is_plain_json(paths: SharedPaths) -> None:
    _autoswap.write_last_auto_swap(paths, "stable", {"from": "1", "to": "2", "outcome": "swapped"})
    assert json.loads(paths.root.joinpath("stable.last-auto-swap").read_text(encoding="utf-8"))["to"] == "2"


def test_a_dist_info_without_metadata_reads_as_missing_not_as_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `pip install` mid-flight leaves a dist-info whose METADATA is not written: ``version()`` answers ``None``."""
    from trw_mcp.shared_server import _autoswap_ports

    real = _autoswap_ports.metadata.version
    monkeypatch.setattr(_autoswap_ports.metadata, "version", lambda name: None if name == "trw-mcp" else real(name))
    versions = _autoswap.installed_versions()
    assert "trw-mcp" not in versions and versions["trw-memory"]


async def test_the_server_starts_the_watcher_unless_the_flag_is_off(
    paths: SharedPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_run`` wires the watcher: its status reaches /admin/status and the drift advisory stops saying "restart"."""
    from trw_mcp.middleware import version_drift
    from trw_mcp.shared_server._server import Door, _start_hot_swap

    class _App:
        async def __call__(self, scope: object, receive: object, send: object) -> None: ...

    monkeypatch.setattr(_autoswap, "_ACTIVE", None)
    monkeypatch.setattr(version_drift, "_ACTION_OVERRIDE", None)
    door = Door(_App(), token="t", env="stable", version="1", max_inflight=1)
    assert _start_hot_swap(door, paths, SharedMcpConfig(auto_swap=False)) is None
    assert "auto_swap" not in door.status() and version_drift.build_advisory("1", "2")["action"].startswith("restart")
    assert _autoswap.surface_block()["auto_swap"] == {"enabled": False}  # type: ignore[index]

    task = _start_hot_swap(door, paths, SharedMcpConfig(auto_swap=True))
    try:
        assert task is not None
        assert door.status()["auto_swap"]["state"] == "watching"
        assert "hot-swaps" in version_drift.build_advisory("1", "2")["action"]
    finally:
        assert task is not None
        task.cancel()


def test_the_persisted_row_is_not_read_as_an_env_record(paths: SharedPaths) -> None:
    """``SharedPaths.envs()`` reads every ``*.json`` beside the records as an env; the swap row must not be one."""
    _autoswap.write_last_auto_swap(paths, "stable", {"from": "1", "to": "2", "outcome": "swapped"})
    assert paths.envs() == []


def test_the_daemon_drain_only_acts_on_the_instance_that_was_judged_older(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drain acts on whatever the record names now: a daemon replaced since is never drained."""
    import trw_memory.daemon as daemon_pkg
    from trw_memory.daemon import _upgrade
    from trw_memory.daemon._discovery import DaemonInfo

    def info(pid: int, version: str) -> DaemonInfo:
        return DaemonInfo(
            pid=pid, url="http://127.0.0.1:9/mcp", started_at=f"t{pid}", version=version, capabilities=["drain"]
        )

    judged, replacement = info(10, "5.0.9"), info(11, "5.9.9")
    monkeypatch.setattr(daemon_pkg, "read_live_discovery", lambda _paths: replacement)
    drains: list[str] = []
    monkeypatch.setattr(_upgrade, "drain_daemon", lambda *a, **k: drains.append("drained") or "")
    ports = _autoswap_ports.real_ports("stable", paths, tmp_path, pid=1)
    why = ports.drain_daemon(judged, "5.1.1")
    assert "changed" in why and drains == []


def test_the_daemon_drain_uses_a_short_window(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drain closes the daemon's door to other sessions, so a busy daemon is given up on after seconds."""
    import trw_memory.daemon as daemon_pkg
    from trw_memory.daemon import _grants, _upgrade
    from trw_memory.daemon._discovery import DaemonInfo

    judged = DaemonInfo(pid=10, url="http://127.0.0.1:9/mcp", started_at="t10", version="5.0.9", capabilities=["drain"])
    monkeypatch.setattr(daemon_pkg, "read_live_discovery", lambda _paths: judged)
    monkeypatch.setattr(_grants, "read_checkout_grant", lambda _root: "tok")
    seen: dict[str, object] = {}

    def drain(_paths: object, **kwargs: object) -> str:
        seen.update(kwargs)
        return ""

    monkeypatch.setattr(_upgrade, "drain_daemon", drain)
    ports = _autoswap_ports.real_ports("stable", paths, tmp_path, pid=1)
    assert ports.drain_daemon(judged, "5.1.1") == ""
    assert seen["timeout"] == 5.0 and seen["mine"] == "5.1.1"
