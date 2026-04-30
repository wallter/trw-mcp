"""Tests for pin-store load/save behavior and eviction handling."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs


def test_load_pin_store_empty_file_returns_empty_dict() -> None:
    """Missing pins.json → empty dict (cold start on a fresh project)."""
    from trw_mcp.state._pin_store import load_pin_store

    assert load_pin_store() == {}


def test_load_pin_store_malformed_json_fallback(tmp_path: Path) -> None:
    """Garbage in pins.json → empty dict + WARN event, never crash."""
    from trw_mcp.state._pin_store import load_pin_store, pin_store_path

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text("{not valid json at all", encoding="utf-8")

    with capture_logs() as logs:
        result = load_pin_store()

    assert result == {}
    events = [e for e in logs if e.get("event") == "pin_store_malformed_fallback"]
    assert events, f"Expected pin_store_malformed_fallback WARN, got {logs}"
    assert any(e.get("log_level") == "warning" for e in events)


def test_load_pin_store_root_not_dict_fallback() -> None:
    """pins.json whose root is a list (not a dict) → empty dict + WARN."""
    from trw_mcp.state._pin_store import load_pin_store, pin_store_path

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with capture_logs() as logs:
        result = load_pin_store()

    assert result == {}
    events = [e for e in logs if e.get("event") == "pin_store_malformed_fallback"]
    assert events and any(e.get("error") == "root_not_dict" for e in events)


def test_save_pin_store_writes_pretty_json() -> None:
    """save_pin_store round-trips and emits pretty-printed (indent=2) JSON."""
    from trw_mcp.state._pin_store import load_pin_store, pin_store_path, save_pin_store

    store = {
        "session-a": {
            "run_path": str(Path.cwd()),
            "created_ts": "2026-04-13T12:00:00.000000Z",
            "last_heartbeat_ts": "2026-04-13T12:00:00.000000Z",
            "client_hint": "claude-code",
            "pid": os.getpid(),
        }
    }
    save_pin_store(store)

    pins_path = pin_store_path()
    text = pins_path.read_text(encoding="utf-8")
    assert "\n" in text
    assert '  "session-a"' in text
    loaded = load_pin_store()
    assert loaded["session-a"]["run_path"] == str(Path.cwd())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_save_pin_store_mode_0600(tmp_path: Path) -> None:
    """pins.json is chmod 0600 after atomic write (NFR03)."""
    from trw_mcp.state._pin_store import pin_store_path, save_pin_store

    save_pin_store(
        {
            "s": {
                "run_path": str(tmp_path),
                "created_ts": "t",
                "last_heartbeat_ts": "t",
                "client_hint": None,
                "pid": os.getpid(),
            }
        }
    )
    pins_path = pin_store_path()
    mode = stat.S_IMODE(os.stat(pins_path).st_mode)
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


def test_save_pin_store_invalidates_cache_immediately(tmp_path: Path) -> None:
    """Cache is reset to None immediately after os.replace completes."""
    import trw_mcp.state._pin_store as ps_mod
    from trw_mcp.state._pin_store import load_pin_store, save_pin_store

    load_pin_store()
    save_pin_store(
        {
            "seed": {
                "run_path": str(tmp_path),
                "created_ts": "t",
                "last_heartbeat_ts": "t",
                "client_hint": None,
                "pid": os.getpid(),
            }
        }
    )
    assert ps_mod._pin_store_cache is None, (
        "Cache was not invalidated after save — write-after-read isolation hazard reintroduced."
    )


def test_save_pin_store_atomic_leaves_no_tmp_on_success(tmp_path: Path) -> None:
    """After a successful save, no ``pins.json.tmp`` remains."""
    from trw_mcp.state._pin_store import pin_store_path, save_pin_store

    save_pin_store(
        {
            "s": {
                "run_path": str(tmp_path),
                "created_ts": "t",
                "last_heartbeat_ts": "t",
                "client_hint": None,
                "pid": os.getpid(),
            }
        }
    )
    pins_path = pin_store_path()
    tmp_pins = pins_path.with_suffix(pins_path.suffix + ".tmp")
    assert not tmp_pins.exists(), "Orphan pins.json.tmp remained after save"


def test_save_pin_store_atomic_cleans_up_tmp_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If os.replace raises, the tmp file is cleaned up and no orphan remains."""
    import trw_mcp.state._pin_store as ps_mod
    from trw_mcp.state._pin_store import pin_store_path, save_pin_store

    real_replace = os.replace

    def _boom(src: Any, dst: Any) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(ps_mod.os, "replace", _boom)

    with pytest.raises(OSError, match="simulated rename failure"):
        save_pin_store(
            {
                "s": {
                    "run_path": str(tmp_path),
                    "created_ts": "t",
                    "last_heartbeat_ts": "t",
                    "client_hint": None,
                    "pid": os.getpid(),
                }
            }
        )

    monkeypatch.setattr(ps_mod.os, "replace", real_replace)

    pins_path = pin_store_path()
    tmp_pins = pins_path.with_suffix(pins_path.suffix + ".tmp")
    assert not tmp_pins.exists(), "Tmp file left behind after rename failure"


def test_load_pin_store_evicts_stale_run_paths(tmp_path: Path) -> None:
    """Entries whose run_path no longer exists are dropped with WARN."""
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, load_pin_store, pin_store_path

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    ghost = tmp_path / "does-not-exist"
    pins_path.write_text(
        json.dumps(
            {
                "ghost": {
                    "run_path": str(ghost),
                    "created_ts": "t",
                    "last_heartbeat_ts": "t",
                    "client_hint": None,
                    "pid": os.getpid(),
                },
                "live": {
                    "run_path": str(tmp_path),
                    "created_ts": "t",
                    "last_heartbeat_ts": "t",
                    "client_hint": None,
                    "pid": os.getpid(),
                },
            }
        ),
        encoding="utf-8",
    )
    invalidate_pin_store_cache()

    with capture_logs() as logs:
        result = load_pin_store()

    assert "ghost" not in result
    assert "live" in result
    evicted = [e for e in logs if e.get("event") == "pin_stale_run_path_evicted"]
    assert evicted, f"Expected pin_stale_run_path_evicted, got {logs}"
    assert any(e.get("pin_key") == "ghost" for e in evicted)


def test_load_pin_store_evicts_orphan_pid(tmp_path: Path) -> None:
    """Entries whose pid is dead are dropped with WARN."""
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, load_pin_store, pin_store_path

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(
        json.dumps(
            {
                "orphan": {
                    "run_path": str(tmp_path),
                    "created_ts": "t",
                    "last_heartbeat_ts": "t",
                    "client_hint": None,
                    "pid": 99999999,
                }
            }
        ),
        encoding="utf-8",
    )
    invalidate_pin_store_cache()

    with capture_logs() as logs:
        result = load_pin_store()

    if sys.platform != "win32":
        assert "orphan" not in result, "Orphan pid entry should have been evicted"
        evicted = [e for e in logs if e.get("event") == "pin_orphan_evicted"]
        assert evicted, f"Expected pin_orphan_evicted, got {logs}"
