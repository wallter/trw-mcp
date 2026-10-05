"""After an upgrade, the doctor counts the servers still running the old code instead of claiming they restarted.

A 9.0.1 upgrade report: the installer said "Running MCP servers were signaled to restart", yet six servers launched
by live Claude Code sessions on the same checkout kept running pre-9.0 code, and pins.json listed only two of them.
The installer signals nothing (each server is a stdio child of its client). The ``stale_servers`` row counts every
server on the checkout (pinned, or running with the checkout as its working directory) that started before the
install stamp, and says how to load the update. The installer's end-of-run doctor prints it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

_SLEEP = "import time; time.sleep(60)"


@pytest.fixture
def procs() -> Iterator[list[subprocess.Popen[bytes]]]:
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for proc in started:
        proc.kill()
        proc.wait()


def _spawn(procs: list[subprocess.Popen[bytes]], argv: list[str], cwd: Path) -> int:
    proc = subprocess.Popen(argv, cwd=cwd)
    procs.append(proc)
    return proc.pid


def _console_script(tmp_path: Path) -> Path:
    """A file named ``trw-mcp`` the way pip writes one; the process table shows ``python .../bin/trw-mcp``."""
    script = tmp_path / "bin" / "trw-mcp"
    script.parent.mkdir(exist_ok=True)
    script.write_text(_SLEEP + "\n", encoding="utf-8")
    return script


def _stamp(project: Path, moment: datetime) -> None:
    (project / ".trw").mkdir(exist_ok=True)
    (project / ".trw" / "installed-version.json").write_text(
        json.dumps({"version": "9.0.1", "timestamp": moment.isoformat()}), encoding="utf-8"
    )


def _pin(project: Path, pid: int) -> None:
    runtime = project / ".trw" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    record = {"run_path": "/r", "created_ts": now, "last_heartbeat_ts": now, "pid": pid, "client_pid": 1}
    (runtime / "pins.json").write_text(json.dumps({"k": record}), encoding="utf-8")


@pytest.fixture
def checkout(tmp_path: Path, procs: list[subprocess.Popen[bytes]]) -> tuple[Path, set[int]]:
    """Two servers on the project (one only in the process table, one only in pins.json) and two that are not."""
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    script = _console_script(tmp_path)
    unpinned = _spawn(procs, [sys.executable, str(script)], project)
    pinned = _spawn(procs, [sys.executable, "-c", _SLEEP], elsewhere)
    _pin(project, pinned)
    _spawn(procs, [sys.executable, str(script), "doctor", "."], project)  # a one-shot verb, not a server
    _spawn(procs, [sys.executable, str(script)], elsewhere)  # another checkout's server
    return project, {unpinned, pinned}


@pytest.mark.skipif(os.name != "posix", reason="the process table is read with ps")  # skip-category: host-tool
def test_running_servers_finds_pinned_and_unpinned_servers_of_this_checkout_only(
    checkout: tuple[Path, set[int]],
) -> None:
    from trw_mcp.state._checkout_servers import running_servers

    project, expected = checkout

    assert running_servers(project) == expected


@pytest.mark.skipif(os.name != "posix", reason="the process table is read with ps")  # skip-category: host-tool
def test_servers_older_than_the_install_are_counted_with_the_reconnect_fix(checkout: tuple[Path, set[int]]) -> None:
    from trw_mcp.server._doctor_environment import stale_servers_row

    project, expected = checkout
    _stamp(project, datetime.now(timezone.utc) + timedelta(seconds=5))

    status, message = stale_servers_row(project)

    assert status == "WARN"
    assert message.startswith(f"{len(expected)} of {len(expected)} trw-mcp server(s)")
    assert "none was signaled" in message
    assert "fix: reconnect each client (/mcp in Claude Code)" in message  # the installer prints rows carrying a fix
    assert all(str(pid) in message for pid in expected)


@pytest.mark.skipif(os.name != "posix", reason="the process table is read with ps")  # skip-category: host-tool
def test_servers_started_after_the_install_pass(checkout: tuple[Path, set[int]]) -> None:
    from trw_mcp.server._doctor_environment import stale_servers_row

    project, expected = checkout
    _stamp(project, datetime.now(timezone.utc) - timedelta(hours=1))

    status, message = stale_servers_row(project)

    assert status == "PASS"
    assert message.startswith(f"{len(expected)} trw-mcp server(s) running")


def test_without_an_install_stamp_the_row_skips(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_environment import stale_servers_row

    assert stale_servers_row(tmp_path)[0] == "SKIP"


def test_an_unreadable_process_table_is_never_reported_as_no_servers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server._doctor_environment import stale_servers_row
    from trw_mcp.state import _checkout_servers

    monkeypatch.setattr(_checkout_servers, "_run", lambda argv: None)
    _stamp(tmp_path, datetime.now(timezone.utc))

    status, message = stale_servers_row(tmp_path)

    assert status == "SKIP"
    assert "could not read the process table" in message


def test_the_row_is_registered_with_the_doctor() -> None:
    from trw_mcp.server._subcommands_doctor import _CHECKS

    assert "stale_servers" in [name for name, _fn in _CHECKS]


# ── what the installer prints at the end of an upgrade ──────────────────────

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_stale_servers", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name != "posix", reason="the process table is read with ps")  # skip-category: host-tool
def test_the_installer_prints_the_stale_server_count_and_the_reconnect_fix(
    checkout: tuple[Path, set[int]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real doctor row, fed through the installer's end-of-run doctor report, as the user reads it."""
    from trw_mcp.server._doctor_environment import stale_servers_row

    project, expected = checkout
    _stamp(project, datetime.now(timezone.utc) + timedelta(seconds=5))
    status, message = stale_servers_row(project)
    installer = _installer()
    payload = json.dumps({"checks": [{"name": "stale_servers", "status": status, "message": message}]})
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout=payload, stderr="")
    )

    assert installer.run_install_doctor(installer.UI(interactive=False), "python3", project) is True
    installer.show_success_banner(installer.UI(interactive=True), "offline", [], is_reinstall=True)

    out = capsys.readouterr().out
    assert (
        f"{len(expected)} of {len(expected)} trw-mcp server(s) on this checkout started before the last install" in out
    )
    assert "fix: reconnect each client (/mcp in Claude Code)" in out
    assert "signaled to restart" not in out  # the claim the 9.0.1 report disproved
    assert "Running servers keep the old code: reconnect each client (/mcp) or restart it" in out
