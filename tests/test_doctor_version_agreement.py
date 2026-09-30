"""CODEX-P0-C step 3: the doctor names a launcher older than this build or the daemon, and its rows agree.

R2 of ``.trw/swarm/r8/audit/CODEX-P0-C-DIAGNOSIS.md``: a bare ``trw-mcp`` in ``.codex/config.toml`` resolved to a 7.0.1
install; ``memory_backend`` failed, ``memory_daemon`` and ``version_status`` stayed green, and the remedy said to stop the
NEWER daemon. The direction decides the remedy: the client is upgraded, the newer daemon is left alone.
"""

from __future__ import annotations

import os
import stat
from importlib.metadata import version
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("stub_cli_version_probes")


def _fake_venv(root: Path, mcp: str, memory: str) -> Path:
    site = root / "lib" / "python3.11" / "site-packages"
    for dist, ver in (("trw_mcp", mcp), ("trw_memory", memory)):
        info = site / f"{dist}-{ver}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: {ver}\n", encoding="utf-8")
    (root / "bin").mkdir()
    (root / "bin" / "python").write_text("", encoding="utf-8")
    return root


def _console_script(venv: Path) -> Path:
    script = venv / "bin" / "trw-mcp"
    script.write_text(f"#!{venv}/bin/python\nprint('x')\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _codex_project(tmp_path: Path, command: str) -> Path:
    project = tmp_path / "proj"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(f'[mcp_servers.trw]\ncommand = "{command}"\n', encoding="utf-8")
    return project


def test_launcher_versions_read_a_console_script_and_an_exec_shim(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_version_skew import launcher_versions

    script = _console_script(_fake_venv(tmp_path / "venv", "7.0.1", "4.0.1"))
    assert launcher_versions(str(script), tmp_path) == ("7.0.1", "4.0.1")
    shim = tmp_path / "shim"
    shim.write_text(f'#!/usr/bin/env bash\nexec "{script}" "$@"\n', encoding="utf-8")
    assert launcher_versions(str(shim), tmp_path) == ("7.0.1", "4.0.1")


def test_launcher_versions_are_none_for_what_is_not_a_venv_script(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_version_skew import launcher_versions

    plain = tmp_path / "plain.sh"
    plain.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    assert launcher_versions(str(plain), tmp_path) is None
    assert launcher_versions(str(tmp_path / "missing"), tmp_path) is None


def test_a_launcher_older_than_this_build_fails_with_its_versions_and_the_upgrade_remedy(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_launcher_divergence import launcher_divergence_row

    script = _console_script(_fake_venv(tmp_path / "old", "0.0.1", "0.0.1"))
    status, message = launcher_divergence_row(_codex_project(tmp_path, str(script)))
    assert status == "FAIL", message
    assert ".codex/config.toml" in message and "trw-mcp 0.0.1 / trw-memory 0.0.1" in message
    assert "upgrade this client" in message and "do not stop the newer daemon" in message


def test_a_launcher_at_this_build_does_not_fail(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_launcher_divergence import launcher_divergence_row

    script = _console_script(_fake_venv(tmp_path / "same", version("trw-mcp"), version("trw-memory")))
    status, message = launcher_divergence_row(_codex_project(tmp_path, str(script)))
    assert status == "PASS", message  # a user project (not a dev checkout) stays green on the location rule


def test_an_older_patch_launcher_warns_instead_of_failing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The client's own refusal rule is a different MAJOR; a lower minor/patch still works, so it is a WARN."""
    import trw_mcp.server._doctor_launcher_divergence as row_module

    running = {"trw-mcp": "8.0.5", "trw-memory": "5.0.5"}
    monkeypatch.setattr("importlib.metadata.version", lambda name: running[name])
    script = _console_script(_fake_venv(tmp_path / "patch", "8.0.1", "5.0.1"))
    status, message = row_module.launcher_divergence_row(_codex_project(tmp_path, str(script)))
    assert status == "WARN" and "same major" in message, message


def _live_daemon(monkeypatch: pytest.MonkeyPatch, user_dir: Path, daemon_version: str) -> None:
    import trw_memory.daemon as daemon_pkg
    import trw_memory.daemon.client as daemon_client
    from trw_memory.daemon import DaemonInfo, DaemonPaths

    paths = DaemonPaths.resolve()
    info = DaemonInfo(
        schema_version=1,
        pid=os.getpid(),
        url="http://127.0.0.1:1/mcp",
        started_at="2026-09-30T00:00:00+00:00",
        version=daemon_version,
        process_start=None,
        capabilities=[],
    )

    async def _answered(*_a: object, **_k: object) -> str:
        return "answered"

    monkeypatch.setattr(daemon_pkg, "read_discovery_result", lambda _p: info)
    monkeypatch.setattr(DaemonInfo, "is_live", lambda _self, _lock: True)
    monkeypatch.setattr(daemon_client, "probe_endpoint", _answered)
    assert paths.user_memory_dir  # the fixture's isolated user dir is in effect


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "userhome"
    path.mkdir()
    monkeypatch.setenv("TRW_USER_DIR", str(path))
    return path


def test_a_newer_daemon_fails_the_daemon_row_and_says_to_upgrade_the_client(
    user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    _live_daemon(monkeypatch, user_dir, "999.0.0")
    status, message = memory_daemon_row(user_dir)
    assert status == "FAIL", message
    assert "NEWER" in message and "upgrade THIS client" in message
    assert "stop process" not in message, "never advise evicting the newer service"


def test_a_newer_patch_daemon_is_a_warning_not_a_failure(user_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_memory

    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    major = trw_memory.__version__.split(".")[0]
    _live_daemon(monkeypatch, user_dir, f"{major}.999.0")
    status, message = memory_daemon_row(user_dir)
    assert status == "WARN" and "same major" in message, message


def test_the_version_status_row_no_longer_greens_a_daemon_skew(user_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server import _subcommands_release
    from trw_mcp.server._doctor_version_status import version_status_row

    monkeypatch.setattr(
        _subcommands_release, "collect_version_status", lambda _root: {"compatible": True, "mismatches": []}
    )
    _live_daemon(monkeypatch, user_dir, "999.0.0")
    status, message = version_status_row(user_dir)
    assert status == "WARN" and "runs trw-memory 999.0.0" in message and "upgrade THIS client" in message


def test_the_three_rows_agree_that_a_skewed_daemon_is_not_healthy(
    user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_memory

    from trw_mcp.server import _subcommands_release
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row
    from trw_mcp.server._doctor_version_status import version_status_row

    monkeypatch.setattr(
        _subcommands_release, "collect_version_status", lambda _root: {"compatible": True, "mismatches": []}
    )
    for daemon in ("999.0.0", "0.0.1"):
        _live_daemon(monkeypatch, user_dir, daemon)
        assert memory_daemon_row(user_dir)[0] != "PASS"
        assert version_status_row(user_dir)[0] != "PASS"
    monkeypatch.setattr("trw_mcp.server._doctor_version_status.stale_editable_metadata", list)
    _live_daemon(monkeypatch, user_dir, trw_memory.__version__)
    assert memory_daemon_row(user_dir)[0] == "PASS"
    assert version_status_row(user_dir)[0] == "PASS"
