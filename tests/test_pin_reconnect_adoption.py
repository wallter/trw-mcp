"""PRD-INFRA-189 FR07/FR08: the run pin survives ``/mcp``; a superseded server says so.

Claude Code gives an MCP server ``CLAUDE_CODE_SESSION_ID`` as it stood at SPAWN.
After an interactive ``/resume`` the old server keeps pinning under the stale id;
the server ``/mcp`` spawns resolves the live id. Both are children of the same
client process, which is what these tests model with ``client_pid``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from trw_mcp.state import _paths_pin_mgmt
from trw_mcp.state._paths import get_pinned_run
from trw_mcp.state._pin_store import invalidate_pin_store_cache, load_pin_store, pin_store_path
from trw_mcp.state._process_identity import process_start_time

_OLD_KEY = "d5bbbfd0-0240-4e0e-9b6a-751278eb0602"
_NEW_KEY = "fb48ac50-1053-421e-8999-e635cc1a7174"
_CLIENT_START = process_start_time(os.getppid())


@pytest.fixture(autouse=True)
def _fresh_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_paths_pin_mgmt, "_sibling_adoption_done", False)
    monkeypatch.setattr(_paths_pin_mgmt, "_superseded_logged", False)


def _write_pins(entries: dict[str, dict[str, object]]) -> None:
    path = pin_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")
    invalidate_pin_store_cache()


_UNSET = object()


def _entry(
    run: Path,
    *,
    pid: int,
    client_pid: int | None,
    created: str = "2026-09-18T04:07:32.000000Z",
    client_start: object = _UNSET,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "run_path": str(run),
        "created_ts": created,
        "last_heartbeat_ts": "2099-01-01T00:00:00.000000Z",
        "client_hint": None,
        "pid": pid,
    }
    if client_pid is not None:
        entry["client_pid"] = client_pid
        entry["client_start"] = _CLIENT_START if client_start is _UNSET else client_start
    return entry


def test_the_real_parent_has_a_readable_start_time() -> None:
    """Every positive test below is vacuous if the birth time cannot be read here."""
    assert isinstance(_CLIENT_START, str) and _CLIENT_START


def test_reconnected_server_adopts_the_same_clients_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_pins({_OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid())})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _NEW_KEY)

    with capture_logs() as logs:
        assert get_pinned_run(session_id=_NEW_KEY) == run

    invalidate_pin_store_cache()
    store = load_pin_store()
    assert store[_NEW_KEY]["run_path"] == str(run)
    assert store[_NEW_KEY]["client_pid"] == os.getppid()
    assert _OLD_KEY in store, "the older server may still be live; its pin stays"
    assert any(e["event"] == "pin_adopted_from_client_sibling" for e in logs)


def test_another_clients_pin_is_never_adopted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_pins({_OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid() + 1)})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _NEW_KEY)

    assert get_pinned_run(session_id=_NEW_KEY) is None


def test_a_recycled_client_pid_is_not_adopted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead client's retained pin, its pid now reused by an unrelated client (Codex review P1)."""
    run = tmp_path / "run"
    run.mkdir()
    _write_pins(
        {_OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid(), client_start="Thu Jan  1 00:00:00 1970")}
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _NEW_KEY)

    assert get_pinned_run(session_id=_NEW_KEY) is None
    invalidate_pin_store_cache()
    assert _NEW_KEY not in load_pin_store()


def test_a_pin_without_client_start_is_not_adopted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pins written before the birth time was recorded cannot prove their client: fail closed."""
    run = tmp_path / "run"
    run.mkdir()
    legacy = _entry(run, pid=os.getpid() + 1, client_pid=os.getppid())
    del legacy["client_start"]
    _write_pins({_OLD_KEY: legacy})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _NEW_KEY)

    assert get_pinned_run(session_id=_NEW_KEY) is None


def test_an_unreadable_client_start_adopts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_pins({_OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid(), client_start=None)})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _NEW_KEY)
    monkeypatch.setattr(_paths_pin_mgmt, "process_start_time", lambda _pid: None)

    assert get_pinned_run(session_id=_NEW_KEY) is None


def test_a_key_not_from_the_client_variable_does_not_adopt(tmp_path: Path) -> None:
    """Codex publishes no session variable and hosts several threads per process."""
    run = tmp_path / "run"
    run.mkdir()
    _write_pins({_OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid())})

    assert get_pinned_run(session_id="ctx-uuid-of-a-codex-thread") is None


def test_legacy_entries_without_client_pid_are_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_pins({_OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=None)})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _NEW_KEY)

    assert get_pinned_run(session_id=_NEW_KEY) is None


def test_adoption_happens_once_per_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_pins({_OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid())})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _NEW_KEY)
    assert get_pinned_run(session_id=_NEW_KEY) == run

    # The adopted pin is later taken away (e.g. trw_adopt_run by another session).
    _write_pins({_OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid())})
    assert get_pinned_run(session_id=_NEW_KEY) is None


def test_written_pins_record_the_client_process(tmp_path: Path) -> None:
    from trw_mcp.state._pin_store import upsert_pin_entry

    run = tmp_path / "run"
    run.mkdir()
    record = upsert_pin_entry("k", run)
    assert record["client_pid"] == os.getppid()
    assert record["client_start"] == _CLIENT_START


def test_the_older_server_logs_superseded_once(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_pins(
        {
            _OLD_KEY: _entry(run, pid=os.getpid(), client_pid=os.getppid(), created="2026-09-17T00:00:00.000000Z"),
            _NEW_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid(), created="2026-09-18T00:00:00.000000Z"),
        }
    )

    with capture_logs() as logs:
        assert get_pinned_run(session_id=_OLD_KEY) == run
        assert get_pinned_run(session_id=_OLD_KEY) == run

    superseded = [e for e in logs if e["event"] == "superseded_by_newer_server"]
    assert len(superseded) == 1
    assert superseded[0]["newer_pid"] == os.getpid() + 1
    assert f"kill {os.getpid()}" in superseded[0]["remedy"]


def test_the_newer_server_does_not_log_superseded(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_pins(
        {
            _OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid(), created="2026-09-17T00:00:00.000000Z"),
            _NEW_KEY: _entry(run, pid=os.getpid(), client_pid=os.getppid(), created="2026-09-18T00:00:00.000000Z"),
        }
    )

    with capture_logs() as logs:
        get_pinned_run(session_id=_NEW_KEY)

    assert not [e for e in logs if e["event"] == "superseded_by_newer_server"]


def test_predating_writers_row_names_the_kill_remedy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server import _doctor_predating_writers as module

    writers = tmp_path / "memory" / "memory.db.writers"
    writers.mkdir(parents=True)
    (writers / f"{os.getpid()}.lock").write_text(f"{os.getpid()}\n1000.0\n", encoding="utf-8")
    monkeypatch.setattr(module, "_install_epoch", lambda: (2000.0, "dist_info_mtime"))

    status, message = module.predating_writers_row(tmp_path)

    assert status == "WARN"
    assert "`kill <pid>`" in message
    assert "not the current connection" in message


@pytest.mark.parametrize("own_pin_present", [True, False])
def test_explicit_unpin_prevents_first_sibling_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, own_pin_present: bool
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    entries = {_OLD_KEY: _entry(run, pid=os.getpid() + 1, client_pid=os.getppid())}
    if own_pin_present:
        entries[_NEW_KEY] = _entry(run, pid=os.getpid(), client_pid=os.getppid())
    _write_pins(entries)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _NEW_KEY)
    _paths_pin_mgmt.unpin_active_run(session_id=_NEW_KEY)
    assert get_pinned_run(session_id=_NEW_KEY) is None
    assert _OLD_KEY in load_pin_store()
