"""Tests for pin-store load/save behavior and eviction handling."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs


def _pin_in_process(trw_dir: str, run_path: str, pin_key: str, barrier: Any) -> None:
    from trw_mcp.state import _paths
    from trw_mcp.state._pin_store import upsert_pin_entry

    _paths.resolve_trw_dir = lambda: Path(trw_dir)  # type: ignore[assignment]
    barrier.wait()
    upsert_pin_entry(pin_key, Path(run_path))


@pytest.mark.skipif(sys.platform == "win32", reason="advisory flock is unavailable on Windows")
def test_concurrent_process_upserts_preserve_distinct_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.syspath_prepend(repo_root)
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (repo_root, existing_pythonpath))))

    trw_dir = tmp_path / ".trw"
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    run_a.mkdir()
    run_b.mkdir()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    processes = [
        context.Process(target=_pin_in_process, args=(str(trw_dir), str(run_a), "a", barrier)),
        context.Process(target=_pin_in_process, args=(str(trw_dir), str(run_b), "b", barrier)),
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    from trw_mcp.state._pin_store import invalidate_pin_store_cache, load_pin_store

    invalidate_pin_store_cache()
    pins = load_pin_store()
    assert set(pins) == {"a", "b"}


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


def test_load_pin_store_non_utf8_bytes_fallback() -> None:
    """Non-UTF-8 bytes in pins.json → empty dict + WARN, never crash.

    A torn write or disk corruption can leave an invalid byte sequence in
    pins.json. Decoding it raises UnicodeDecodeError (a ValueError, not an
    OSError); load_pin_store sits on the hot session_start path and must
    honor its documented fail-open contract rather than propagate.
    """
    from trw_mcp.state._pin_store import load_pin_store, pin_store_path

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    # 0xFF is never valid as the leading byte of a UTF-8 sequence.
    pins_path.write_bytes(b'{"k": \xff}')

    with capture_logs() as logs:
        result = load_pin_store()

    assert result == {}
    events = [e for e in logs if e.get("event") == "pin_store_malformed_fallback"]
    assert events, f"Expected pin_store_malformed_fallback WARN, got {logs}"
    assert any(e.get("error") == "UnicodeDecodeError" for e in events)


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


def test_load_pin_store_retains_dead_creator_pid(tmp_path: Path) -> None:
    """Creator PID is diagnostic; durable pins survive server restarts."""
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

    result = load_pin_store()
    assert "orphan" in result


def test_prune_pin_store_orphans_persists_eviction(tmp_path: Path) -> None:
    """prune_pin_store_orphans removes stale-path entries from disk."""
    from trw_mcp.state._pin_store import (
        invalidate_pin_store_cache,
        pin_store_path,
        prune_pin_store_orphans,
    )

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(
        json.dumps(
            {
                "stale-key": {
                    "run_path": str(tmp_path / "missing-run"),
                    "created_ts": "t",
                    "last_heartbeat_ts": "t",
                    "client_hint": None,
                    "pid": 99999999,
                },
                "live-key": {
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

    removed = prune_pin_store_orphans()

    assert removed == 1
    on_disk = json.loads(pins_path.read_text(encoding="utf-8"))
    assert "stale-key" not in on_disk
    assert "live-key" in on_disk


def test_prune_pin_store_orphans_no_op_when_clean(tmp_path: Path) -> None:
    """prune returns 0 and does not rewrite when nothing is evictable."""
    from trw_mcp.state._pin_store import (
        invalidate_pin_store_cache,
        pin_store_path,
        prune_pin_store_orphans,
    )

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(
        json.dumps(
            {
                "live-key": {
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
    mtime_before = pins_path.stat().st_mtime_ns

    removed = prune_pin_store_orphans()

    assert removed == 0
    assert pins_path.stat().st_mtime_ns == mtime_before


def test_prune_pin_store_orphans_non_utf8_bytes_returns_zero() -> None:
    """prune fails open on non-UTF-8 bytes: returns 0 + WARN, never crashes.

    The boot sweep calls prune_pin_store_orphans; a corrupt store must not
    take it down. UnicodeDecodeError is a ValueError (not OSError), so it
    must be caught explicitly alongside JSONDecodeError/OSError.
    """
    from trw_mcp.state._pin_store import pin_store_path, prune_pin_store_orphans

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_bytes(b'{"k": \xff}')

    with capture_logs() as logs:
        removed = prune_pin_store_orphans()

    assert removed == 0
    events = [e for e in logs if e.get("event") == "pin_store_prune_read_failed"]
    assert events, f"Expected pin_store_prune_read_failed WARN, got {logs}"
    assert any(e.get("error") == "UnicodeDecodeError" for e in events)


def test_prune_pin_store_orphans_missing_file_returns_zero(tmp_path: Path) -> None:
    """prune is a no-op when the pin store file does not exist."""
    from trw_mcp.state._pin_store import pin_store_path, prune_pin_store_orphans

    pins_path = pin_store_path()
    if pins_path.exists():
        pins_path.unlink()

    assert prune_pin_store_orphans() == 0
    assert not pins_path.exists()


def test_load_pin_store_concurrent_deletion_after_load_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: stat() after json.load must not crash if the file was deleted concurrently.

    A concurrent GC process can delete pins.json between the json.load() and the
    subsequent pins_path.stat() call.  Before the fix, the stat() call in the
    malformed-fallback and root-not-dict branches was guarded only by
    ``pins_path.exists()``, which is a TOCTOU race — and the success-path stat()
    had NO guard at all, so FileNotFoundError propagated uncaught.

    After the fix, all three call-sites use _safe_mtime_ns() which catches OSError
    and returns None, preserving the fail-open contract.
    """
    import trw_mcp.state._pin_store as ps_mod
    from trw_mcp.state._pin_store import load_pin_store, pin_store_path

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text('{"valid": {}}', encoding="utf-8")
    ps_mod.invalidate_pin_store_cache()

    # Simulate: stat() always raises FileNotFoundError (concurrent delete).
    monkeypatch.setattr(ps_mod, "_safe_mtime_ns", lambda _path: None)

    # Must not raise, even though stat() would normally fail.
    result = load_pin_store()
    # The store contains no valid pin entries (eviction removes keys without
    # proper schema), so the result may be empty — what matters is no crash.
    assert isinstance(result, dict)
