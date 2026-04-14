"""Tests for PRD-CORE-141 Waves 1 & 2: TRWCallContext + persistent pin store.

Covers FR01 (TRWCallContext frozen dataclass), FR02 (resolve_pin_key with
four-layer fallback + ctx probing + kill switch), FR04 (persistent pin
store at ``.trw/runtime/pins.json`` with atomic writes, file locking,
1-second read cache, and eviction passes), FR13 (config fields).
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs


# ---------------------------------------------------------------------------
# FR01 — TRWCallContext value object
# ---------------------------------------------------------------------------


def test_trw_call_context_is_frozen() -> None:
    """TRWCallContext is a frozen dataclass; setters raise FrozenInstanceError."""
    from trw_mcp.state._paths import TRWCallContext

    ctx = TRWCallContext(
        session_id="abc",
        client_hint="claude-code",
        explicit=False,
        fastmcp_session="abc",
    )
    assert ctx.session_id == "abc"
    assert ctx.client_hint == "claude-code"
    assert ctx.explicit is False
    assert ctx.fastmcp_session == "abc"

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.session_id = "other"  # type: ignore[misc]


def test_trw_call_context_accepts_none_hints() -> None:
    """client_hint and fastmcp_session may be None."""
    from trw_mcp.state._paths import TRWCallContext

    ctx = TRWCallContext(
        session_id="abc",
        client_hint=None,
        explicit=True,
        fastmcp_session=None,
    )
    assert ctx.client_hint is None
    assert ctx.fastmcp_session is None


# ---------------------------------------------------------------------------
# FR02 — Four-layer pin-key resolver
# ---------------------------------------------------------------------------


def test_resolve_pin_key_layer_1_explicit_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit arg beats env, ctx, and process."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.setenv("TRW_SESSION_ID", "env-id")
    ctx = SimpleNamespace(session_id="ctx-id")

    result = resolve_pin_key(ctx=ctx, explicit="explicit-id")
    assert result == "explicit-id"


def test_resolve_pin_key_layer_2_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TRW_SESSION_ID env var returned when no explicit, no ctx."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.setenv("TRW_SESSION_ID", "abc")
    assert resolve_pin_key(ctx=None) == "abc"


def test_resolve_pin_key_layer_3_ctx_session_id_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ctx.session_id probe succeeds and logs source=ctx, ctx_attr_path=session_id."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    ctx = SimpleNamespace(session_id="x")

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=ctx)

    assert result == "x"
    resolved_events = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(
        e.get("source") == "ctx" and e.get("ctx_attr_path") == "session_id"
        for e in resolved_events
    ), f"Expected pin_resolved source=ctx ctx_attr_path=session_id, got {resolved_events}"


def test_resolve_pin_key_layer_3_ctx_request_context_meta_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ctx.request_context.meta.session_id probe when ctx.session_id missing."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    meta = SimpleNamespace(session_id="y")
    request_context = SimpleNamespace(meta=meta)
    # Deliberately omit session_id from top-level ctx so first probe fails.
    ctx = SimpleNamespace(request_context=request_context)

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=ctx)

    assert result == "y"
    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(
        e.get("source") == "ctx"
        and e.get("ctx_attr_path") == "request_context.meta.session_id"
        for e in resolved
    ), f"Expected ctx_attr_path=request_context.meta.session_id, got {resolved}"


def test_resolve_pin_key_layer_3_ctx_request_id_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ctx.request_id probe when other ctx paths missing."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    ctx = SimpleNamespace(request_id="z")

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=ctx)

    assert result == "z"
    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(
        e.get("source") == "ctx" and e.get("ctx_attr_path") == "request_id"
        for e in resolved
    ), f"Expected ctx_attr_path=request_id, got {resolved}"


class _ExplodingCtx:
    """Ctx object where every attribute access raises AttributeError."""

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"no attribute {name}")


def test_resolve_pin_key_all_probes_fail_logs_warn_and_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All ctx probes fail → fastmcp_context_probe_error WARN fires, process fallback."""
    from trw_mcp.state._paths import get_session_id, resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)

    # Build a ctx that raises AttributeError on any attribute access.
    ctx = _ExplodingCtx()

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=ctx)

    # Process-level fallback
    assert result == get_session_id()

    warns = [e for e in logs if e.get("event") == "fastmcp_context_probe_error"]
    assert warns, f"Expected fastmcp_context_probe_error WARN in {logs}"
    assert any(e.get("log_level") == "warning" for e in warns)

    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(e.get("source") == "process" for e in resolved)


def test_resolve_pin_key_layer_4_process_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No explicit, no env, no ctx → process-level _session_id."""
    from trw_mcp.state._paths import get_session_id, resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=None)

    assert result == get_session_id()
    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(e.get("source") == "process" for e in resolved)


# ---------------------------------------------------------------------------
# Precedence ordering
# ---------------------------------------------------------------------------


def test_resolve_pin_key_precedence_explicit_beats_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.setenv("TRW_SESSION_ID", "env-id")
    assert resolve_pin_key(ctx=None, explicit="explicit-id") == "explicit-id"


def test_resolve_pin_key_precedence_env_beats_ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.setenv("TRW_SESSION_ID", "env-id")
    ctx = SimpleNamespace(session_id="ctx-id")
    assert resolve_pin_key(ctx=ctx) == "env-id"


def test_resolve_pin_key_precedence_ctx_beats_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.state._paths import get_session_id, resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    ctx = SimpleNamespace(session_id="ctx-id")
    result = resolve_pin_key(ctx=ctx)
    assert result == "ctx-id"
    assert result != get_session_id()


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


def test_ctx_isolation_disabled_reverts_to_process_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ctx_isolation_enabled=False, resolver ignores ctx and returns process UUID."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import get_session_id, resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    cfg = get_config()
    monkeypatch.setattr(cfg, "ctx_isolation_enabled", False, raising=False)

    ctx = SimpleNamespace(session_id="ctx-id-should-be-ignored")

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=ctx)

    assert result == get_session_id()
    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(
        e.get("source") == "process" and e.get("kill_switch") is True
        for e in resolved
    ), f"Expected kill_switch=True pin_resolved event, got {resolved}"


# ---------------------------------------------------------------------------
# FR13 — Config fields round-trip
# ---------------------------------------------------------------------------


def test_config_sweep_fields_round_trip() -> None:
    """All seven new PRD-CORE-141 config fields exist with documented defaults."""
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig()
    assert cfg.run_staleness_hours == 48
    assert cfg.run_staleness_grace_hours == 12
    assert cfg.pin_ttl_hours == 24
    assert cfg.run_archive_hours == 720
    assert cfg.cleanup_on_boot is True
    assert cfg.checkpoint_suggest_hours == 4
    assert cfg.ctx_isolation_enabled is True


def test_config_sweep_fields_env_var_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config fields reachable via TRW_<UPPER> env vars."""
    from trw_mcp.models.config import TRWConfig

    monkeypatch.setenv("TRW_RUN_STALENESS_HOURS", "72")
    monkeypatch.setenv("TRW_CTX_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("TRW_PIN_TTL_HOURS", "6")

    cfg = TRWConfig()
    assert cfg.run_staleness_hours == 72
    assert cfg.ctx_isolation_enabled is False
    assert cfg.pin_ttl_hours == 6


# ---------------------------------------------------------------------------
# FR04 — Persistent pin store (.trw/runtime/pins.json)
# ---------------------------------------------------------------------------


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
    from trw_mcp.state._pin_store import (
        load_pin_store,
        pin_store_path,
        save_pin_store,
    )

    store = {
        "session-a": {
            "run_path": str(Path.cwd()),  # use an existing dir to survive eviction
            "created_ts": "2026-04-13T12:00:00.000000Z",
            "last_heartbeat_ts": "2026-04-13T12:00:00.000000Z",
            "client_hint": "claude-code",
            "pid": os.getpid(),
        }
    }
    save_pin_store(store)

    pins_path = pin_store_path()
    text = pins_path.read_text(encoding="utf-8")
    # Pretty-printed has newlines and leading indentation.
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

    # Prime the cache by a load
    load_pin_store()
    # Force cache to a known non-None state via a load after populating disk
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
    # Immediately after save, the cache MUST be None.
    assert ps_mod._pin_store_cache is None, (
        "Cache was not invalidated after save — write-after-read isolation "
        "hazard reintroduced."
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


def test_save_pin_store_atomic_cleans_up_tmp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    # Restore replace so teardown fixtures can clean up.
    monkeypatch.setattr(ps_mod.os, "replace", real_replace)

    pins_path = pin_store_path()
    tmp_pins = pins_path.with_suffix(pins_path.suffix + ".tmp")
    assert not tmp_pins.exists(), "Tmp file left behind after rename failure"


def test_load_pin_store_evicts_stale_run_paths(tmp_path: Path) -> None:
    """Entries whose run_path no longer exists are dropped with WARN."""
    from trw_mcp.state._pin_store import (
        invalidate_pin_store_cache,
        load_pin_store,
        pin_store_path,
    )

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
    from trw_mcp.state._pin_store import (
        invalidate_pin_store_cache,
        load_pin_store,
        pin_store_path,
    )

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    # 99999999 — far outside any plausible pid range
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
    else:
        # On Windows without psutil, orphan pid check degrades to skip — entry kept.
        # (documented behavior in _is_pid_alive)
        pass


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

    # Simulate process restart: drop the in-memory cache so the next read
    # comes from disk.
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


def test_concurrent_threads_pin_distinct_sessions_no_corruption(
    tmp_path: Path,
) -> None:
    """Ten threads pin ten distinct sessions; final JSON has all ten entries."""
    from trw_mcp.state._paths import pin_active_run
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, pin_store_path

    run_dir = tmp_path / "shared-run"
    run_dir.mkdir()

    def _worker(sid: str) -> None:
        pin_active_run(run_dir, session_id=sid)

    threads = [
        threading.Thread(target=_worker, args=(f"sid-{i:02d}",))
        for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Force a fresh read from disk.
    invalidate_pin_store_cache()
    data = json.loads(pin_store_path().read_text(encoding="utf-8"))
    for i in range(10):
        assert f"sid-{i:02d}" in data, f"missing sid-{i:02d} after concurrent pin"


def test_pin_store_cache_ttl_collapses_burst_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two reads within 1s hit disk exactly once (cache collapses burst)."""
    import trw_mcp.state._pin_store as ps_mod
    from trw_mcp.state._pin_store import (
        invalidate_pin_store_cache,
        load_pin_store,
        pin_store_path,
    )

    # Seed disk state and wipe cache.
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

    def _counting_load(fh: Any, *a: Any, **kw: Any) -> Any:
        call_count["n"] += 1
        return real_load(fh, *a, **kw)

    monkeypatch.setattr(ps_mod.json, "load", _counting_load)

    load_pin_store()
    load_pin_store()
    load_pin_store()

    assert call_count["n"] == 1, (
        f"Expected 1 disk read across 3 calls within TTL, got {call_count['n']}"
    )


def test_pin_store_save_acquires_file_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every save invokes _lock_ex on the pin-store lock-file FD.

    Tests the cross-process coordination path without spinning up a
    second process.  We wrap _lock_ex and count calls.
    """
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
