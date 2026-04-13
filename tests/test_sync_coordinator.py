"""Tests for SyncCoordinator — PRD-INFRA-051-FR08."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Create a temporary .trw directory."""
    d = tmp_path / ".trw"
    d.mkdir()
    return d


def test_should_sync_true_when_no_state_file(trw_dir: Path) -> None:
    """should_sync returns True when sync-state.json does not exist."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir, sync_interval=300)
    assert coord.should_sync() is True


def test_should_sync_true_when_interval_elapsed(trw_dir: Path) -> None:
    """should_sync returns True when last sync was longer ago than interval."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    old_time = (datetime.now(tz=timezone.utc) - timedelta(seconds=600)).isoformat()
    state = {"last_push_at": old_time, "version": 1}
    (trw_dir / "sync-state.json").write_text(json.dumps(state))

    coord = SyncCoordinator(trw_dir=trw_dir, sync_interval=300)
    assert coord.should_sync() is True


def test_should_sync_false_when_recent(trw_dir: Path) -> None:
    """should_sync returns False when last sync was recent."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    recent_time = datetime.now(tz=timezone.utc).isoformat()
    state = {"last_push_at": recent_time, "version": 1}
    (trw_dir / "sync-state.json").write_text(json.dumps(state))

    coord = SyncCoordinator(trw_dir=trw_dir, sync_interval=300)
    assert coord.should_sync() is False


def test_should_sync_true_when_malformed(trw_dir: Path) -> None:
    """should_sync returns True when state file is malformed."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    (trw_dir / "sync-state.json").write_text("not json")

    coord = SyncCoordinator(trw_dir=trw_dir, sync_interval=300)
    assert coord.should_sync() is True


def test_acquire_sync_lock_returns_true(trw_dir: Path) -> None:
    """First caller gets the lock."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    with coord.acquire_sync_lock() as acquired:
        assert acquired is True


def test_acquire_sync_lock_concurrent(trw_dir: Path) -> None:
    """Second concurrent caller does not get the lock."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord1 = SyncCoordinator(trw_dir=trw_dir)
    coord2 = SyncCoordinator(trw_dir=trw_dir)

    with coord1.acquire_sync_lock() as acquired1:
        assert acquired1 is True
        with coord2.acquire_sync_lock() as acquired2:
            assert acquired2 is False


def test_record_sync_success_writes_state(trw_dir: Path) -> None:
    """record_sync_success writes valid JSON to sync-state.json."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    coord.record_sync_success(pushed=5, pulled=0, push_seq=2)

    state_path = trw_dir / "sync-state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["last_push_seq"] == 2
    assert state["push_count"] == 1
    assert state["last_error"] is None


def test_record_sync_failure_writes_error(trw_dir: Path) -> None:
    """record_sync_failure records error info."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    coord.record_sync_failure("connection refused")

    state = json.loads((trw_dir / "sync-state.json").read_text())
    assert state["last_error"] == "connection refused"
    assert state["consecutive_failures"] == 1


def test_get_last_push_seq_default(trw_dir: Path) -> None:
    """get_last_push_seq returns 0 when no state exists."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    assert coord.get_last_push_seq() == 0


def test_get_last_push_seq_after_success(trw_dir: Path) -> None:
    """get_last_push_seq returns correct value after sync success."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    coord.record_sync_success(pushed=42, pulled=0, push_seq=3)
    assert coord.get_last_push_seq() == 3


def test_record_sync_success_keeps_highest_push_seq(trw_dir: Path) -> None:
    """last_push_seq tracks the highest synced local sequence, not push count."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    coord.record_sync_success(pushed=10, pulled=0, push_seq=4)
    coord.record_sync_success(pushed=2, pulled=0, push_seq=2)

    assert coord.get_last_push_seq() == 4
