"""Tests for persistent run pinning and pin-store coordination."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest


def test_pin_active_run_persists_to_disk(tmp_path: Path) -> None:
    """pin_active_run writes an entry to .trw/runtime/pins.json."""
    from trw_mcp.state._paths import pin_active_run
    from trw_mcp.state._pin_store import pin_store_path

    run_dir = tmp_path / "some-run"
    run_dir.mkdir()
    pin_active_run(run_dir, session_id="persistent-sid")

    pins_path = pin_store_path()
    assert pins_path.exists(), ".trw/runtime/pins.json should have been created"
    data = json.loads(pins_path.read_text(encoding="utf-8"))
    assert "persistent-sid" in data
    assert data["persistent-sid"]["run_path"] == str(run_dir.resolve())
    assert data["persistent-sid"]["pid"] == os.getpid()


def test_pin_persists_across_process_restart_simulation(tmp_path: Path) -> None:
    """Pin survives a simulated process restart (module-level cache cleared)."""
    from trw_mcp.state._paths import get_pinned_run, pin_active_run
    from trw_mcp.state._pin_store import invalidate_pin_store_cache

    run_dir = tmp_path / "restart-run"
    run_dir.mkdir()
    pin_active_run(run_dir, session_id="restart-sid")

    invalidate_pin_store_cache()

    result = get_pinned_run(session_id="restart-sid")
    assert result == run_dir.resolve()


def test_unpin_active_run_removes_disk_entry(tmp_path: Path) -> None:
    """unpin_active_run deletes the on-disk pin entry."""
    from trw_mcp.state._paths import pin_active_run, unpin_active_run
    from trw_mcp.state._pin_store import pin_store_path

    run_dir = tmp_path / "r"
    run_dir.mkdir()
    pin_active_run(run_dir, session_id="ephemeral")

    unpin_active_run(session_id="ephemeral")
    pins_path = pin_store_path()
    data = json.loads(pins_path.read_text(encoding="utf-8"))
    assert "ephemeral" not in data


def test_concurrent_threads_pin_distinct_sessions_no_corruption(tmp_path: Path) -> None:
    """Ten threads pin ten distinct sessions; final JSON has all ten entries."""
    from trw_mcp.state._paths import pin_active_run
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, pin_store_path

    run_dir = tmp_path / "shared-run"
    run_dir.mkdir()

    def _worker(sid: str) -> None:
        pin_active_run(run_dir, session_id=sid)

    threads = [threading.Thread(target=_worker, args=(f"sid-{i:02d}",)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    invalidate_pin_store_cache()
    data = json.loads(pin_store_path().read_text(encoding="utf-8"))
    for i in range(10):
        assert f"sid-{i:02d}" in data, f"missing sid-{i:02d} after concurrent pin"


def test_pin_store_cache_ttl_collapses_burst_reads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two reads within 1s hit disk exactly once (cache collapses burst)."""
    import trw_mcp.state._pin_store as ps_mod
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, load_pin_store, pin_store_path

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(
        json.dumps(
            {
                "k": {
                    "run_path": str(tmp_path),
                    "created_ts": "t",
                    "last_heartbeat_ts": "t",
                    "client_hint": None,
                    "pid": os.getpid(),
                }
            }
        ),
        encoding="utf-8",
    )
    invalidate_pin_store_cache()

    call_count = {"n": 0}
    real_load = json.load

    def _counting_load(fh: Any, *args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        return real_load(fh, *args, **kwargs)

    monkeypatch.setattr(ps_mod.json, "load", _counting_load)

    load_pin_store()
    load_pin_store()
    load_pin_store()

    assert call_count["n"] == 1, f"Expected 1 disk read across 3 calls within TTL, got {call_count['n']}"


def test_pin_store_save_acquires_file_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every save invokes _lock_ex on the pin-store lock-file FD."""
    import trw_mcp.state._pin_store as ps_mod
    from trw_mcp.state._pin_store import pin_store_lock_path, save_pin_store

    lock_calls: list[int] = []
    real_lock_ex = ps_mod._lock_ex

    def _counting_lock_ex(fd: int) -> None:
        lock_calls.append(fd)
        real_lock_ex(fd)

    monkeypatch.setattr(ps_mod, "_lock_ex", _counting_lock_ex)

    save_pin_store(
        {
            "sid": {
                "run_path": str(tmp_path),
                "created_ts": "t",
                "last_heartbeat_ts": "t",
                "client_hint": None,
                "pid": os.getpid(),
            }
        }
    )

    assert lock_calls, "save_pin_store did not call _lock_ex"
    assert pin_store_lock_path().exists(), "Lock file sentinel was not created"
