"""The installer moves a checkout's learnings into the user store itself (6.1.0, operator request).

trw-mcp 6 reads learnings only from the user store, through the memory daemon. A
checkout upgraded from 5.x still keeps them in ``.trw/memory/memory.db``, where
nothing reads them until ``trw-mcp memory migrate --to user --apply`` runs. The
installer runs that command after update-project whenever ``holds_rows`` (the
same check update-project and doctor use) says the store holds learnings:

* migrated: the manifest and the rollback command are printed, with any client
  still running against the checkout to reconnect;
* nothing to migrate: nothing runs;
* a refusal (an id the user store holds with other content): the install exits
  non-zero naming the ids and the manual command, and nothing is forced;
* busy or uncertain (exit 2): the install exits non-zero and says to re-run once
  the listed clients are stopped;
* ``--no-migrate`` opts out; ``--script`` migrates by default, and an
  interactive run asks first.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests._install_trw_main_support import drive_main, make_project
from tests._layout import PACKAGE_ROOT

pytestmark = pytest.mark.unit

_TEMPLATE = PACKAGE_ROOT / "scripts" / "install-trw.template.py"
_MANIFEST = "/proj/.trw/memory/migration-20260924T000000000000Z.json"
_RECONNECT = "trw-mcp pid 7, launched by claude (pid 6): reconnect it"


@pytest.fixture
def installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_auto_migrate", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path: Path) -> Path:
    target = make_project(tmp_path)
    (target / ".trw" / "memory").mkdir()
    (target / ".trw" / "memory" / "memory.db").write_bytes(b"x")
    return target


class _Fakes:
    def __init__(self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch, *, holds: bool | None) -> None:
        self.probes: list[list[str]] = []
        self.migrations: list[list[str]] = []
        self.reply: tuple[int, str, str] = (0, "", "")

        def _probe(cmd: list[str], target_dir: str = "", timeout: int = 60) -> tuple[int, str]:
            self.probes.append(cmd)
            return ({True: 0, False: installer._HOLDS_NO_ROWS, None: 1}[holds], "")

        def _migrate(cmd: list[str], cwd: Path, target_dir: str = "") -> tuple[int, str, str]:
            self.migrations.append(cmd)
            return self.reply

        monkeypatch.setattr(installer, "_run_python_output", _probe)
        monkeypatch.setattr(installer, "_run_migrate_command", _migrate)


def _printed(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def _phase(installer: ModuleType, project: Path, **kwargs: Any) -> bool:
    ui = installer.UI(interactive=False, quiet=False)
    options: dict[str, Any] = {"migrate": True, "interactive": False}
    options.update(kwargs)
    return bool(installer.phase_migrate_store(ui, "/target/python", project, **options))


def test_a_store_with_learnings_is_migrated_and_the_rollback_is_printed(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fakes = _Fakes(installer, monkeypatch, holds=True)
    fakes.reply = (
        0,
        f"memory migrate: migrated; manifest {_MANIFEST}\n"
        f"memory migrate: still running on the old store, reconnect: {_RECONNECT}\n",
        "",
    )

    assert _phase(installer, project) is True

    (cmd,) = fakes.migrations
    assert cmd[:4] == ["/target/python", "-B", "-m", "trw_mcp.server"], "runs in the target interpreter"
    assert cmd[4:] == ["memory", "migrate", "--to", "user", "--apply", "--target-dir", str(project)]
    assert fakes.probes[0][-1] == str(project / ".trw" / "memory" / "memory.db")
    out = _printed(capsys)
    assert _MANIFEST in out
    assert f"trw-mcp memory migrate --to user --rollback {_MANIFEST} --target-dir {project}" in out
    assert _RECONNECT in out


@pytest.mark.parametrize("store", ["empty", "absent"])
def test_nothing_to_migrate_runs_nothing(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, project: Path, store: str
) -> None:
    fakes = _Fakes(installer, monkeypatch, holds=False)
    if store == "absent":
        (project / ".trw" / "memory" / "memory.db").unlink()

    assert _phase(installer, project) is True

    assert fakes.migrations == []
    assert len(fakes.probes) == (1 if store == "empty" else 0), "no store, no probe"


def test_a_collision_refusal_fails_the_install_naming_the_ids_and_never_forces(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fakes = _Fakes(installer, monkeypatch, holds=True)
    fakes.reply = (
        1,
        "",
        "memory migrate: the daemon refused (conflict): proj already holds different content for ['L-b']; "
        "run `trw-mcp doctor`\n",
    )

    assert _phase(installer, project) is False

    assert len(fakes.migrations) == 1, "a refusal is never retried"
    assert all("--force" not in part for part in fakes.migrations[0])
    out = _printed(capsys)
    assert "['L-b']" in out
    assert f"trw-mcp memory migrate --to user --apply --target-dir {project}" in out


def test_busy_fails_the_install_and_says_to_rerun_once_the_clients_stop(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fakes = _Fakes(installer, monkeypatch, holds=True)
    fakes.reply = (
        2,
        "",
        "memory migrate: memory.db is open in another process (database is locked); stop this checkout's "
        "other trw-mcp sessions, then retry\n"
        f"memory migrate: still running against this checkout: {_RECONNECT}\n",
    )

    assert _phase(installer, project) is False

    out = _printed(capsys)
    assert "open in another process" in out
    assert _RECONNECT in out
    assert "re-run" in out.lower()


def test_no_migrate_opts_out_and_prints_the_manual_command(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fakes = _Fakes(installer, monkeypatch, holds=True)

    assert _phase(installer, project, migrate=False) is True

    assert fakes.migrations == []
    assert f"trw-mcp memory migrate --to user --apply --target-dir {project}" in _printed(capsys)


@pytest.mark.parametrize("answer", [True, False], ids=["accepts", "declines"])
def test_an_interactive_run_asks_first(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, project: Path, answer: bool
) -> None:
    fakes = _Fakes(installer, monkeypatch, holds=True)
    fakes.reply = (0, f"memory migrate: migrated; manifest {_MANIFEST}\n", "")
    asked: list[str] = []
    monkeypatch.setattr(installer, "prompt_yes_no", lambda question, default="n": asked.append(default) or answer)

    assert _phase(installer, project, interactive=True) is True

    assert asked == ["y"]
    assert len(fakes.migrations) == (1 if answer else 0)


def test_an_unreadable_probe_fails_the_install_with_the_manual_command(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fakes = _Fakes(installer, monkeypatch, holds=None)

    assert _phase(installer, project) is False

    assert fakes.migrations == []
    assert f"trw-mcp memory migrate --to user --apply --target-dir {project}" in _printed(capsys)


# ── main(): default on, --no-migrate, and the exit code ───────────────


def _stub_phase(installer: ModuleType, monkeypatch: pytest.MonkeyPatch, result: bool) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _phase_stub(_ui: Any, _python: str, _target: Path, **kwargs: Any) -> bool:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(installer, "phase_migrate_store", _phase_stub)
    return calls


def test_script_mode_migrates_by_default_after_project_setup(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _stub_phase(installer, monkeypatch, True)

    run = drive_main(installer, monkeypatch, make_project(tmp_path))

    assert [c["migrate"] for c in calls] == [True]
    assert run.calls["project_setup"] and run.calls["doctor"]


def test_no_migrate_reaches_the_phase(installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _stub_phase(installer, monkeypatch, True)

    drive_main(installer, monkeypatch, make_project(tmp_path), extra_argv=("--no-migrate",))

    assert [c["migrate"] for c in calls] == [False]


def test_an_unfinished_migration_exits_1(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Never a green finish over learnings nothing reads."""
    calls = _stub_phase(installer, monkeypatch, False)

    with pytest.raises(SystemExit) as exited:
        drive_main(installer, monkeypatch, make_project(tmp_path))

    assert exited.value.code == 1
    assert len(calls) == 1


def test_the_probe_is_the_real_holds_rows_in_the_target_interpreter(installer: ModuleType, tmp_path: Path) -> None:
    """No fakes: the probe source imports trw-mcp's own check and answers for a real store."""
    import sys

    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    db = tmp_path / "memory.db"
    store = SQLiteBackend(db)
    try:
        probe = [sys.executable, "-B", "-c", installer._HOLDS_ROWS_SOURCE, str(db)]
        assert installer._run_python_output(probe)[0] == installer._HOLDS_NO_ROWS
        store.store(MemoryEntry(id="L-a", content="a learning", namespace="default"))
    finally:
        store.close()
    assert installer._run_python_output(probe)[0] == 0
