"""PRD-CORE-248 FR04 / NFR01 / NFR02 — the WAL checkpoint trigger-and-mode matrix.

The defect this replaces had three compounding parts:

1. ``_run_wal_maintenance`` cancelled the checkpoint whenever ``writer_count >= 2``
   — the ordinary steady state on a machine running two editors — so the size
   check was never reached at all.
2. The only call site was session-start auto-maintenance, so a long-lived server
   that never ran ``trw_session_start`` never checkpointed.
3. The trigger was size-only, so a stale WAL below the threshold could survive
   indefinitely.

Everything here drives the real ``maybe_checkpoint_wal`` /
``evaluate_wal_trigger`` / ``_run_wal_maintenance`` against a real SQLite store.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import cast

import pytest

from tests._structlog_capture import captured_structlog as captured_structlog

DEAD_PEER_PID = 4194303


def _trw_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".trw"
    (d / "memory").mkdir(parents=True, exist_ok=True)
    return d


def _seed_wal(db_path: Path, n_writes: int = 2000) -> sqlite3.Connection:
    """Create a real WAL-mode store and dirty its WAL. Caller closes the conn."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v BLOB)")
    payload = b"x" * 4096
    for _ in range(n_writes):
        conn.execute("INSERT INTO t(v) VALUES (?)", (payload,))
    conn.commit()
    return conn


def _write_writer_lock(trw_dir: Path, name: str, pid: int) -> None:
    writers = trw_dir / "memory" / "memory.db.writers"
    writers.mkdir(parents=True, exist_ok=True)
    (writers / name).write_text(f"{pid}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Clause 1: the trigger is size OR age
# ---------------------------------------------------------------------------


def test_trigger_matrix_size_age_both_and_neither(tmp_path: Path) -> None:
    """Due on size alone, on age alone, on both, and NOT due on neither."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import evaluate_wal_trigger, record_checkpoint_attempt

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    wal_path = db_path.with_suffix(".db-wal")
    cfg = TRWConfig(wal_checkpoint_threshold_mb=1, wal_checkpoint_max_age_seconds=3600)
    now = time.time()

    wal_path.write_bytes(b"\x00" * 1024)
    record_checkpoint_attempt(db_path, now=now)
    assert evaluate_wal_trigger(trw_dir, cfg, now=now).reason == "not_due"

    wal_path.write_bytes(b"\x00" * (2 * 1024 * 1024))
    assert evaluate_wal_trigger(trw_dir, cfg, now=now).reason == "size"

    wal_path.write_bytes(b"\x00" * 1024)
    record_checkpoint_attempt(db_path, now=now - 7200)
    assert evaluate_wal_trigger(trw_dir, cfg, now=now).reason == "age"

    wal_path.write_bytes(b"\x00" * (2 * 1024 * 1024))
    assert evaluate_wal_trigger(trw_dir, cfg, now=now).reason == "size_and_age"


def test_missing_or_unparseable_timestamp_is_treated_as_due(tmp_path: Path) -> None:
    """A store with no usable checkpoint record is due, never assumed fresh."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import checkpoint_marker_path, evaluate_wal_trigger

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    db_path.with_suffix(".db-wal").write_bytes(b"\x00" * 1024)
    cfg = TRWConfig(wal_checkpoint_threshold_mb=100)

    assert evaluate_wal_trigger(trw_dir, cfg).reason == "never_checkpointed"

    checkpoint_marker_path(db_path).write_text("not-a-number", encoding="utf-8")
    assert evaluate_wal_trigger(trw_dir, cfg).due is True

    # A future timestamp (clock skew / restored backup) is not "very fresh".
    checkpoint_marker_path(db_path).write_text(f"{time.time() + 10_000:.3f}", encoding="utf-8")
    assert evaluate_wal_trigger(trw_dir, cfg).due is True


# ---------------------------------------------------------------------------
# Clause 3: pressure changes the mode, never whether it runs
# ---------------------------------------------------------------------------


def test_age_trigger_runs_passive_under_writer_pressure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two live writers -> the checkpoint STILL runs, in PASSIVE, never cancelled."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import get_config
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    holder = _seed_wal(db_path)
    backend = SQLiteBackend(db_path)
    mc._backend = backend
    modes: list[str] = []
    real = backend.checkpoint_wal

    def spy(mode: str = "TRUNCATE", **kwargs: object) -> object:
        modes.append(mode)
        return real(mode, **kwargs)  # type: ignore[arg-type]

    try:
        # A live PEER writer alongside this process's own registered lock.
        _write_writer_lock(trw_dir, "peer.lock", os.getppid())
        monkeypatch.setattr(backend, "checkpoint_wal", spy)
        cfg = get_config()
        monkeypatch.setattr(cfg, "wal_checkpoint_threshold_mb", 100)  # age trigger only

        result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))

        assert result.get("checkpointed") is True, "writer pressure must not cancel the checkpoint"
        assert modes == ["PASSIVE"], f"under pressure the mode must be PASSIVE, got {modes}"
        assert result.get("mode") == "passive"
    finally:
        mc.reset_backend()
        holder.close()


def test_truncate_requested_only_when_sole_live_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clause 4: exactly this process in the live-writer set permits TRUNCATE."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import get_config
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    holder = _seed_wal(db_path)
    backend = SQLiteBackend(db_path)  # registers THIS pid in the writer registry
    mc._backend = backend
    modes: list[str] = []
    real = backend.checkpoint_wal

    def spy(mode: str = "TRUNCATE", **kwargs: object) -> object:
        modes.append(mode)
        return real(mode, **kwargs)  # type: ignore[arg-type]

    try:
        monkeypatch.setattr(backend, "checkpoint_wal", spy)
        cfg = get_config()
        monkeypatch.setattr(cfg, "wal_checkpoint_threshold_mb", 1)

        result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))

        assert modes == ["TRUNCATE"]
        assert result.get("checkpointed") is True
    finally:
        mc.reset_backend()
        holder.close()


def test_dead_peer_lock_does_not_suppress_the_permit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale lock naming a dead pid is not a live writer, so TRUNCATE stays permitted."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    import trw_mcp.state._memory_connection as mc
    from trw_mcp.state._wal_triggers import sole_live_writer

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    backend = SQLiteBackend(db_path)
    mc._backend = backend
    try:
        _write_writer_lock(trw_dir, "ghost.lock", DEAD_PEER_PID)
        assert sole_live_writer(trw_dir, db_path) is True
        _write_writer_lock(trw_dir, "peer.lock", os.getppid())
        assert sole_live_writer(trw_dir, db_path) is False
    finally:
        mc.reset_backend()


def test_daemon_owned_store_is_never_sole_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-CORE-253: a live daemon serving this store is another writer."""
    import trw_mcp.state._wal_triggers as wt

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    monkeypatch.setattr(wt, "_daemon_owns", lambda _p: True)
    monkeypatch.setattr(
        "trw_mcp.state.memory_pressure.live_memory_writer_pids",
        lambda *_a, **_k: [os.getpid()],
    )
    assert wt.sole_live_writer(trw_dir, db_path) is False


def test_daemon_owns_fails_closed_on_invalid_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, object]]
) -> None:
    """An untrusted daemon.json is not "no daemon": it must not permit a WAL reset.

    Folding DiscoveryInvalid into absent would let this process TRUNCATE a WAL
    a live daemon still holds open on a corrupt-or-unreadable record alone.
    """
    pytest.importorskip("trw_memory.daemon")
    from trw_memory.daemon import DaemonPaths

    import trw_mcp.state._wal_triggers as wt

    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    paths = DaemonPaths.resolve()
    paths.discovery.write_text("{not valid json", encoding="utf-8")
    paths.discovery.chmod(0o600)

    assert wt._daemon_owns(paths.store) is True
    assert any(log.get("event") == "daemon_record_invalid" for log in captured_structlog)


def test_maintenance_step_no_longer_cancels_on_pressure(tmp_path: Path) -> None:
    """The deleted branch: _run_wal_maintenance takes no deferral state at all."""
    import inspect

    from trw_mcp.tools._ceremony_maintenance_steps import _run_wal_maintenance

    params = set(inspect.signature(_run_wal_maintenance).parameters)
    assert params == {"trw_dir", "maintenance"}, "writer-pressure deferral parameters must be gone, not merely unused"
    source = inspect.getsource(_run_wal_maintenance)
    assert "defer_memory_heavy" not in source.split('"""')[-1]


# ---------------------------------------------------------------------------
# NFR01: an idle evaluation is one stat, zero connections
# ---------------------------------------------------------------------------


def test_idle_sweep_no_trigger_opens_no_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A not-due evaluation must never open a SQLite connection."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._wal_idle_sweep import checkpoint_after_commit
    from trw_mcp.state._wal_triggers import record_checkpoint_attempt

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    db_path.with_suffix(".db-wal").write_bytes(b"\x00" * 1024)
    record_checkpoint_attempt(db_path, now=time.time())
    cfg = get_config()
    monkeypatch.setattr(cfg, "wal_checkpoint_threshold_mb", 100)

    opens = 0

    def _counting_connect(*args: object, **kwargs: object) -> object:
        nonlocal opens
        opens += 1
        raise AssertionError("a not-due evaluation must not open a connection")

    monkeypatch.setattr(sqlite3, "connect", _counting_connect)
    checkpoint_after_commit(trw_dir)
    assert opens == 0


def test_sweeper_thread_is_named_and_daemonised(tmp_path: Path) -> None:
    """The idle sweep runs on the named daemon thread, matching trw-boot-gc."""
    from trw_mcp.state import _wal_idle_sweep

    _wal_idle_sweep._sweeper_thread = None
    thread = _wal_idle_sweep.start_wal_checkpoint_sweeper(_trw_dir(tmp_path))
    assert thread is not None
    assert thread.name == "trw-wal-checkpoint"
    assert thread.daemon is True
    # A second start is a no-op rather than a second sweeper.
    assert _wal_idle_sweep.start_wal_checkpoint_sweeper(_trw_dir(tmp_path)) is None


# ---------------------------------------------------------------------------
# NFR02: fail-open, and a failed checkpoint does not advance the clock
# ---------------------------------------------------------------------------


def test_checkpoint_failure_is_fail_open_and_does_not_advance_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising checkpoint returns an error dict and leaves the timestamp untouched."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._wal_triggers import checkpoint_marker_path, record_checkpoint_attempt
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    db_path.with_suffix(".db-wal").write_bytes(b"\x00" * (11 * 1024 * 1024))
    stale = time.time() - 999_999
    record_checkpoint_attempt(db_path, now=stale)
    before = checkpoint_marker_path(db_path).read_text(encoding="utf-8")

    monkeypatch.setattr(get_config(), "wal_checkpoint_threshold_mb", 1)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))

    result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))

    assert result.get("error") is True
    assert checkpoint_marker_path(db_path).read_text(encoding="utf-8") == before, (
        "a failed checkpoint must leave the age trigger due"
    )


def test_busy_checkpoint_still_advances_the_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """US-001 AC3: busy=1 means the checkpoint RAN, so the clock advances."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import get_config
    from trw_mcp.state._wal_triggers import checkpoint_marker_path, record_checkpoint_attempt
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    holder = _seed_wal(db_path)
    backend = SQLiteBackend(db_path)
    mc._backend = backend
    stale = time.time() - 999_999
    record_checkpoint_attempt(db_path, now=stale)
    try:
        monkeypatch.setattr(
            backend,
            "checkpoint_wal",
            lambda *a, **k: {"busy": 1, "checkpointed": 0, "mode": "PASSIVE"},
        )
        monkeypatch.setattr(get_config(), "wal_checkpoint_threshold_mb", 1)
        result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))
        assert result.get("busy") == 1
        assert float(checkpoint_marker_path(db_path).read_text(encoding="utf-8")) > stale
    finally:
        mc.reset_backend()
        holder.close()


def test_commit_hook_failure_never_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, object]]
) -> None:
    """The write-commit evaluation point is fail-open (NFR02)."""
    from trw_mcp.state import _wal_idle_sweep

    monkeypatch.setattr(
        "trw_mcp.state._wal_triggers.evaluate_wal_trigger",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    _wal_idle_sweep.checkpoint_after_commit(_trw_dir(tmp_path))
    assert any(log.get("event") == "wal_checkpoint_after_commit_failed" for log in captured_structlog)


def test_marker_write_failure_is_logged_not_raised(tmp_path: Path, captured_structlog: list[dict[str, object]]) -> None:
    """An unwritable marker path warns and returns (NFR02)."""
    from trw_mcp.state._wal_triggers import record_checkpoint_attempt

    blocked = tmp_path / "not-a-dir"
    blocked.write_text("file", encoding="utf-8")
    record_checkpoint_attempt(blocked / "memory.db")
    assert any(log.get("event") == "wal_checkpoint_marker_write_failed" for log in captured_structlog)


# ---------------------------------------------------------------------------
# Review finding 4: attempt clock vs effective clock
# ---------------------------------------------------------------------------


def test_a_checkpoint_that_reclaims_nothing_advances_only_the_attempt_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The split that lets the doctor see a store checkpointing to no effect.

    A busy PASSIVE checkpoint that writes back zero frames and leaves the file
    the same size RAN — so the age trigger's clock advances and it does not
    hot-loop — but it ACCOMPLISHED nothing, so the effective clock must not move.
    """
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import get_config
    from trw_mcp.state._wal_triggers import (
        checkpoint_marker_path,
        effective_checkpoint_marker_path,
        last_checkpoint_age_seconds,
        last_effective_checkpoint_age_seconds,
    )
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    holder = _seed_wal(db_path)
    backend = SQLiteBackend(db_path)
    mc._backend = backend
    try:
        monkeypatch.setattr(
            backend, "checkpoint_wal", lambda *a, **k: {"busy": 1, "checkpointed": 0, "mode": "PASSIVE"}
        )
        monkeypatch.setattr(get_config(), "wal_checkpoint_threshold_mb", 1)

        result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))

        assert result.get("checkpointed") is True
        assert checkpoint_marker_path(db_path).exists()
        assert last_checkpoint_age_seconds(db_path) is not None
        assert not effective_checkpoint_marker_path(db_path).exists(), (
            "a checkpoint that reclaimed nothing must not advance the effective clock"
        )
        assert last_effective_checkpoint_age_seconds(db_path) is None
    finally:
        mc.reset_backend()
        holder.close()


def test_a_checkpoint_that_writes_frames_back_advances_both_clocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-vacuity: the effective clock DOES move when the checkpoint does work."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import get_config
    from trw_mcp.state._wal_triggers import (
        last_checkpoint_age_seconds,
        last_effective_checkpoint_age_seconds,
    )
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    holder = _seed_wal(db_path)
    backend = SQLiteBackend(db_path)
    mc._backend = backend
    try:
        monkeypatch.setattr(get_config(), "wal_checkpoint_threshold_mb", 1)
        result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))
        assert cast("int", result["pages_checkpointed"]) > 0
        assert last_checkpoint_age_seconds(db_path) is not None
        assert last_effective_checkpoint_age_seconds(db_path) is not None
    finally:
        mc.reset_backend()
        holder.close()


def test_a_failed_checkpoint_advances_neither_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02, both clocks: an error leaves the trigger due and the doctor honest."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._wal_triggers import checkpoint_marker_path, effective_checkpoint_marker_path
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    db_path.with_suffix(".db-wal").write_bytes(b"\x00" * (11 * 1024 * 1024))
    monkeypatch.setattr(get_config(), "wal_checkpoint_threshold_mb", 1)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))

    assert cast("dict[str, object]", maybe_checkpoint_wal(trw_dir)).get("error") is True
    assert not checkpoint_marker_path(db_path).exists()
    assert not effective_checkpoint_marker_path(db_path).exists()


def test_no_resetting_mode_survives_on_an_unsafe_engine_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OQ-1 reversal, through the trw-mcp path: a sole writer still gets PASSIVE.

    trw-mcp still REQUESTS TRUNCATE when it is the sole live writer (FR04
    clause 4 is about what is asked for), but trw-memory refuses to execute it
    below SQLite 3.51.3 with no caller escape.
    """
    from trw_memory.storage import _dbapi
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import get_config
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    monkeypatch.setattr(_dbapi, "is_wal_reset_safe", lambda: False)
    trw_dir = _trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    holder = _seed_wal(db_path)
    backend = SQLiteBackend(db_path)
    mc._backend = backend
    requested: list[str] = []
    real = backend.checkpoint_wal

    def spy(mode: str = "TRUNCATE") -> object:
        requested.append(mode)
        return real(mode)

    try:
        monkeypatch.setattr(backend, "checkpoint_wal", spy)
        monkeypatch.setattr(get_config(), "wal_checkpoint_threshold_mb", 1)
        result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))
        assert requested == ["TRUNCATE"], "the sole writer still asks"
        assert result.get("mode") == "passive", "and the unsafe engine still refuses"
    finally:
        mc.reset_backend()
        holder.close()
