"""PRD-INFRA-200 FR02 / NFR02 through the served entrypoint: ``install-trw.py --upgrade``.

The real ``main()`` runs (``drive_main`` stubs only pip, the network and the
project phases); the daemon stop runs for real in a child interpreter against a
planted ``daemon.json`` naming a real live process, so the identity check and
the version comparison are production's own.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest
from trw_memory import __version__ as memory_version
from trw_memory.daemon import DaemonInfo, DaemonPaths
from trw_memory.daemon._paths import write_secret_file
from trw_memory.storage._pid_liveness import process_start

from tests._install_trw_main_support import drive_main, make_project
from tests._install_trw_pip_target_contract_support import _load_installer_module

_REPO = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPO / "trw-mcp" / "scripts" / "install-trw.template.py"


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


@pytest.fixture
def sleeper() -> Iterator[subprocess.Popen[bytes]]:
    """A live process standing in for the serving daemon."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield process
    process.kill()
    process.wait()


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated TRW user dir, and the child interpreter importing THIS checkout's packages."""
    user = tmp_path / "user"
    monkeypatch.setenv("TRW_USER_DIR", str(user))
    source = os.pathsep.join(str(_REPO / pkg / "src") for pkg in ("trw-mcp", "trw-memory"))
    monkeypatch.setenv("PYTHONPATH", source + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return user


def _plant(user: Path, pid: int, *, version: str, start: str | None) -> None:
    paths = DaemonPaths(user_memory_dir=user / "memory")
    paths.user_memory_dir.mkdir(mode=0o700, parents=True)
    user.chmod(0o700)
    info = DaemonInfo(
        pid=pid,
        url="http://127.0.0.1:9/mcp",
        started_at=datetime.now(timezone.utc).isoformat(),
        version=version,
        process_start=start,
    )
    write_secret_file(paths.discovery, info.model_dump_json())


def _project(tmp_path: Path) -> Path:
    target = make_project(tmp_path)
    (target / ".mcp.json").write_text('{"mcpServers": {"trw": {"command": ".venv/bin/trw-mcp"}}}', encoding="utf-8")
    (target / ".codex").mkdir()
    (target / ".codex" / "config.toml").write_text('[mcp_servers.trw]\ncommand = "trw-mcp"\n', encoding="utf-8")
    return target


def _fresh_project(tmp_path: Path) -> Path:
    """A project with no earlier TRW install (no prior config)."""
    target = tmp_path / "fresh"
    (target / ".git").mkdir(parents=True)
    return target


def _upgrade(installer: ModuleType, monkeypatch: pytest.MonkeyPatch, target: Path, *argv: str) -> None:
    drive_main(installer, monkeypatch, target, extra_argv=argv, stop_daemon=True)


def test_upgrade_stops_an_older_daemon_and_names_every_client_to_reconnect(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    user_dir: Path,
    sleeper: subprocess.Popen[bytes],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _plant(user_dir, sleeper.pid, version="0.0.1", start=process_start(sleeper.pid))
    target = _project(tmp_path)

    _upgrade(installer, monkeypatch, target, "--upgrade")

    sleeper.wait(timeout=10)  # raises TimeoutExpired: the upgrade left the outdated daemon running
    out = capsys.readouterr().out
    assert f"stopped pid {sleeper.pid}" in out
    assert "reconnect the client configured by .mcp.json" in out
    assert "reconnect the client configured by .codex/config.toml" in out
    assert "reconnect the client configured by .cursor/mcp.json" not in out, "only configs that exist are named"


def test_upgrade_leaves_a_daemon_already_serving_the_installed_version_running(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    user_dir: Path,
    sleeper: subprocess.Popen[bytes],
) -> None:
    _plant(user_dir, sleeper.pid, version=memory_version, start=process_start(sleeper.pid))

    _upgrade(installer, monkeypatch, _project(tmp_path), "--upgrade")

    assert sleeper.poll() is None


def test_a_fresh_install_does_not_touch_the_daemon(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    user_dir: Path,
    sleeper: subprocess.Popen[bytes],
) -> None:
    """E2E-INC-134: "ordinary" is a fresh install, or a same-version daemon; neither is ever stopped."""
    _plant(user_dir, sleeper.pid, version="0.0.1", start=process_start(sleeper.pid))
    monkeypatch.setattr(installer, "_probe_installed_version", lambda *_a, **_k: None)

    _upgrade(installer, monkeypatch, _fresh_project(tmp_path))

    assert sleeper.poll() is None


def test_a_plain_install_over_an_existing_one_stops_a_strictly_older_daemon(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    user_dir: Path,
    sleeper: subprocess.Popen[bytes],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """E2E-INC-134 (b): `curl install.sh | bash` never passes --upgrade, and left every 7.0.x user's daemon running."""
    _plant(user_dir, sleeper.pid, version="0.0.1", start=process_start(sleeper.pid))

    _upgrade(installer, monkeypatch, _project(tmp_path))

    sleeper.wait(timeout=10)  # raises TimeoutExpired: the plain run left the older daemon running
    assert f"stopped pid {sleeper.pid}" in capsys.readouterr().out


def test_a_plain_install_over_an_existing_one_leaves_a_same_version_daemon(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    user_dir: Path,
    sleeper: subprocess.Popen[bytes],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _plant(user_dir, sleeper.pid, version=memory_version, start=process_start(sleeper.pid))

    _upgrade(installer, monkeypatch, _project(tmp_path))

    assert sleeper.poll() is None
    assert "Reconnect every MCP client" not in capsys.readouterr().out, "a plain run with nothing to stop is silent"


def test_a_plain_install_never_stops_a_newer_daemon(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    user_dir: Path,
    sleeper: subprocess.Popen[bytes],
) -> None:
    _plant(user_dir, sleeper.pid, version="999.0.0", start=process_start(sleeper.pid))

    _upgrade(installer, monkeypatch, _project(tmp_path))

    assert sleeper.poll() is None


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (None, "kill"),  # a 4.0 record: the pid may name any process, so the remedy is printed instead
        ("not-the-sleepers-start", None),  # a reused pid: the record's daemon is gone
    ],
)
def test_upgrade_never_signals_a_pid_whose_identity_is_not_proven(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    user_dir: Path,
    sleeper: subprocess.Popen[bytes],
    capsys: pytest.CaptureFixture[str],
    start: str | None,
    expected: str | None,
) -> None:
    _plant(user_dir, sleeper.pid, version="0.0.1", start=start)

    _upgrade(installer, monkeypatch, _project(tmp_path), "--upgrade")

    assert sleeper.poll() is None, "a process not proven to be the daemon was signalled"
    out = capsys.readouterr().out
    if expected is not None:
        assert f"{expected} {sleeper.pid}" in out


def test_a_failed_check_is_reported_not_swallowed(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    user_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A target interpreter without the stop verb (an older trw-memory) says so and names the manual remedy."""
    monkeypatch.setattr(installer, "_DAEMON_STOP_SOURCE", "raise ImportError('no stop_outdated_daemon')\n")

    _upgrade(installer, monkeypatch, _project(tmp_path), "--upgrade")

    out = capsys.readouterr().out
    assert "Could not check the trw-memory daemon (ImportError: no stop_outdated_daemon)" in out
    assert "daemon.json" in out
    assert "Reconnect every MCP client" in out, "a failed daemon check must not suppress the reconnect instruction"


def test_the_check_keeps_the_callers_user_dir_under_a_pip_target(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pip runtime env rewrites XDG_DATA_HOME, which would move the memory dir away from the daemon's record."""
    seen: dict[str, str] = {}

    def _capture(_python: str, _target: Path, env: dict[str, str], _older_only: bool) -> tuple[None, str]:
        seen.update(env)
        return None, "captured"

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(installer, "_daemon_stop_verdict", _capture)

    installer.stop_outdated_memory_daemon(sys.executable, tmp_path, installer.UI(), pip_target=str(tmp_path / "pt"))

    assert seen["XDG_DATA_HOME"] == str(tmp_path / "xdg")
    assert seen["PYTHONPATH"].startswith(str(tmp_path / "pt") + os.pathsep)


def test_the_upgrade_stops_the_older_daemon_before_the_project_is_set_up(
    installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-131 a: the stop ran AFTER update-project and the doctor, so each of them met the old daemon
    (daemon_version_mismatch x4, doctor FAIL memory_backend). It runs right after the packages are installed."""
    from tests._install_trw_main_support import drive_main, make_project

    run = drive_main(installer, monkeypatch, make_project(tmp_path), extra_argv=("--upgrade",))

    assert "stop_daemon" in run.order
    assert run.order.index("stop_daemon") < run.order.index("project_setup")
    assert run.order.index("stop_daemon") < run.order.index("doctor")
    assert run.order.count("stop_daemon") == 1  # one drain path, not a second one


def test_a_plain_install_over_an_existing_one_drains_before_project_setup(
    installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strictly-older restriction is pinned by the real-stop tests above; this pins where the drain runs."""
    from tests._install_trw_main_support import drive_main, make_project

    run = drive_main(installer, monkeypatch, make_project(tmp_path))

    assert run.order.count("stop_daemon") == 1
    assert run.order.index("stop_daemon") < run.order.index("project_setup")


def test_a_fresh_plain_install_does_not_drain(
    installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._install_trw_main_support import drive_main

    monkeypatch.setattr(installer, "_probe_installed_version", lambda *_a, **_k: None)

    run = drive_main(installer, monkeypatch, _fresh_project(tmp_path))

    assert "stop_daemon" not in run.order
