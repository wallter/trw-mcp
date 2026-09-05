"""PRD-CORE-248 FR06 — the ``memory_wal`` doctor row.

Before this there was no WAL or writer visibility in diagnostics at all, so a
starved store (25.0 MiB WAL against a 10 MB threshold, checkpoint permanently
cancelled by writer pressure) was invisible until someone read the code.

The row is exercised through the real doctor catalogue, not by calling the
sibling in isolation, so a row that exists but is never registered fails here.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest


def _seed_project(tmp_path: Path, *, wal_bytes: int) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    db_path = trw_dir / "memory" / "memory.db"
    db_path.write_bytes(b"")
    db_path.with_suffix(".db-wal").write_bytes(b"\x00" * wal_bytes)
    return tmp_path


def _row(target: Path, name: str = "memory_wal") -> object:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import _doctor_core

    results = _doctor_core(target, TRWConfig())
    matches = [r for r in results if r.name == name]
    assert matches, f"doctor produced no {name} row; got {[r.name for r in results]}"
    return matches[0]


def test_memory_wal_row_reports_size_writers_and_checkpoint_age(tmp_path: Path) -> None:
    """The row carries all three values and WARNs under an oversized-and-stale store."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import record_checkpoint_attempt, record_effective_checkpoint

    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    db_path = target / ".trw" / "memory" / "memory.db"
    record_checkpoint_attempt(db_path, now=time.time())
    record_effective_checkpoint(db_path, now=time.time() - cfg.wal_checkpoint_max_age_seconds - 60)
    # Two live writers, so the count is non-trivial.
    writers = target / ".trw" / "memory" / "memory.db.writers"
    writers.mkdir(parents=True, exist_ok=True)
    (writers / "self.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (writers / "peer.lock").write_text(f"{os.getppid()}\n", encoding="utf-8")

    row = _row(target)

    assert row.status == "WARN"
    assert "15.0 MiB" in row.message
    assert "2 live writer(s)" in row.message
    assert "last checkpoint attempt" in row.message
    assert "last EFFECTIVE checkpoint" in row.message


def test_unknown_checkpoint_age_reads_unknown_and_passes(tmp_path: Path) -> None:
    """US-004 AC2: a store with no persisted timestamp reports unknown, status PASS."""
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb - 5) * 1024 * 1024)

    row = _row(target)

    assert row.status == "PASS", "an unknown age on a store that never needed a checkpoint is not a fault"
    assert "last EFFECTIVE checkpoint unknown ago" in row.message


def test_oversized_but_freshly_checkpointed_is_pass(tmp_path: Path) -> None:
    """A big WAL that was just checkpointed is a busy store, not a fault."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import record_effective_checkpoint

    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    record_effective_checkpoint(target / ".trw" / "memory" / "memory.db", now=time.time())

    assert _row(target).status == "PASS"


def test_no_wal_file_reports_zero_and_passes(tmp_path: Path) -> None:
    """Negative case: a store with no WAL reports zero size and PASSes."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)

    row = _row(tmp_path)

    assert row.status == "PASS"
    assert "WAL 0.0 MiB" in row.message


def test_memory_wal_check_opens_no_sqlite_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR06: a diagnostic about contention must not add a writer to the store."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import _check_memory_wal

    target = _seed_project(tmp_path, wal_bytes=1024)
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *a, **k: pytest.fail("memory_wal must not open a SQLite connection"),
    )
    result = _check_memory_wal(target, TRWConfig())
    assert result.name == "memory_wal"


def test_memory_wal_is_registered_in_the_doctor_catalogue() -> None:
    """A row nobody dispatches is not a row (wiring assertion)."""
    from trw_mcp.server._subcommands_doctor import _CHECKS

    assert ("memory_wal", "_check_memory_wal") in _CHECKS


def test_warn_reads_the_effective_clock_not_the_attempt_clock(tmp_path: Path) -> None:
    """Review finding 4: a store that checkpoints hourly and reclaims nothing must WARN.

    This is the unsafe-engine steady state — only PASSIVE may run, PASSIVE never
    truncates — and it is exactly the case a row reading the ATTEMPT clock could
    never report, because that clock is always fresh.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import record_checkpoint_attempt, record_effective_checkpoint

    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    db_path = target / ".trw" / "memory" / "memory.db"
    # Attempted seconds ago; last actually accomplished anything, long ago.
    record_checkpoint_attempt(db_path, now=time.time() - 5)
    record_effective_checkpoint(db_path, now=time.time() - cfg.wal_checkpoint_max_age_seconds - 600)

    row = _row(target)

    assert row.status == "WARN"
    assert "nothing reclaimed" in row.message


def test_warn_names_the_engine_remedy_when_the_engine_cannot_reset(tmp_path: Path) -> None:
    """OQ-1 reversal: the operator is told WHY the WAL cannot shrink, and what to do."""
    from trw_memory.storage._wal_checkpoint import WAL_RESET_UNSAFE_REMEDY

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._doctor_memory_wal import memory_wal_row
    from trw_mcp.state._wal_triggers import record_effective_checkpoint

    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    record_effective_checkpoint(
        target / ".trw" / "memory" / "memory.db",
        now=time.time() - cfg.wal_checkpoint_max_age_seconds - 600,
    )

    status, message = memory_wal_row(target)

    assert status == "WARN"
    # This box runs SQLite 3.51.1, below the 3.51.3 fix, so the engine branch is live.
    assert WAL_RESET_UNSAFE_REMEDY in message
    assert "journal_size_limit" in message, "the row must reconcile the 10 MB trigger with the 64 MiB cap"
