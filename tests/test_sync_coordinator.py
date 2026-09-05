"""Tests for SyncCoordinator — PRD-INFRA-051-FR08."""

from __future__ import annotations

import json
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
    assert state["consecutive_failures"] == 0
    assert state["last_pull_seq"] == 0
    assert state["pull_count"] == 0


def test_record_sync_failure_writes_error(trw_dir: Path) -> None:
    """record_sync_failure records error info."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    coord.record_sync_failure("connection refused")

    state = json.loads((trw_dir / "sync-state.json").read_text())
    assert state["last_error"] == "connection refused"
    assert state["consecutive_failures"] == 1


def test_record_pull_success_preserves_last_error(trw_dir: Path) -> None:
    """Pull-only success updates pull state without clearing push failure context."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    coord.record_sync_failure("connection refused")
    coord.record_pull_success(pull_seq=7)

    state = json.loads((trw_dir / "sync-state.json").read_text())
    assert state["last_error"] == "connection refused"
    assert state["last_pull_seq"] == 7
    assert state["pull_count"] == 1
    assert "last_pull_at" in state


def test_record_outcome_push_success_tracks_high_water_line(trw_dir: Path) -> None:
    """Outcome sync persists the append-only line high-water mark."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    coord.record_outcome_push_success(4)
    coord.record_outcome_push_success(2)

    state = json.loads((trw_dir / "sync-state.json").read_text())
    assert state["last_outcome_line"] == 4
    assert coord.get_last_outcome_line() == 4


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


def test_company_pull_cursor_is_independent_of_org_cursor(trw_dir: Path) -> None:
    """PRD-INFRA-139 P1-B: the company cursor is disjoint from the org pull_seq.

    A large org cursor must not influence the company cursor (the disjoint-cursor
    bug that permanently hid company rows). They advance separately and never
    regress to a lower value.
    """
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    # Org cursor races far ahead.
    coord.record_sync_success(pushed=0, pulled=1, push_seq=0, pull_seq=9999, pull_completed=True)
    # Company cursor starts at a small per-company sequence.
    coord.record_company_pull_seq(2)

    assert coord.get_last_pull_seq() == 9999
    assert coord.get_last_company_pull_seq() == 2

    # Company cursor advances monotonically, never regresses.
    coord.record_company_pull_seq(5)
    coord.record_company_pull_seq(3)
    assert coord.get_last_company_pull_seq() == 5
    # The org cursor is untouched by company-cursor writes.
    assert coord.get_last_pull_seq() == 9999


def test_get_last_company_pull_seq_default(trw_dir: Path) -> None:
    """get_last_company_pull_seq returns 0 when no state exists."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    assert coord.get_last_company_pull_seq() == 0


def test_secondary_status_never_touches_failure_counter(trw_dir: Path) -> None:
    """PRD-FIX-125-FR01: a secondary's health is reported, never counted.

    ``consecutive_failures`` / ``last_push_at`` / ``push_count`` describe the
    PRIMARY target only. A permanently-401 local dev secondary pinned all three
    for 134 days before this split.
    """
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    coord.record_sync_success(pushed=8, pulled=0, push_seq=4)
    before = json.loads((trw_dir / "sync-state.json").read_text())

    coord.record_target_health(
        primary_target_label="api.trwframework.com",
        secondary_targets={
            "localhost": {
                "status": "partial_error",
                "failed": 8,
                "last_error": "HTTPStatusError: 401 Unauthorized",
                "last_error_at": "2026-09-03T19:16:47+00:00",
            }
        },
    )
    after = json.loads((trw_dir / "sync-state.json").read_text())

    assert after["consecutive_failures"] == before["consecutive_failures"] == 0
    assert after["last_push_at"] == before["last_push_at"]
    assert after["push_count"] == before["push_count"] == 1
    assert after["primary_target_label"] == "api.trwframework.com"
    assert after["secondary_targets"]["localhost"]["status"] == "partial_error"
    assert after["secondary_targets"]["localhost"]["failed"] == 8


def test_secondary_error_text_is_truncated(trw_dir: Path) -> None:
    """PRD-FIX-125-NFR03: a verbose remote error cannot bloat the hot-path file."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    coord.record_target_health(
        primary_target_label="api.trwframework.com",
        secondary_targets={"localhost": {"status": "error", "failed": 1, "last_error": "x" * 900}},
    )

    state = json.loads((trw_dir / "sync-state.json").read_text())
    assert len(state["secondary_targets"]["localhost"]["last_error"]) == 500
    assert state["secondary_targets"]["localhost"]["last_error_at"] is None


def test_reads_pre_fix_state_file_additively(trw_dir: Path) -> None:
    """PRD-FIX-125-NFR04: a v1 state file written before the fix loads unchanged."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    pre_fix = {
        "last_error": "1 of 2 targets failed",
        "consecutive_failures": 10653,
        "version": 1,
        "push_count": 12,
        "last_push_at": "2026-04-21T18:08:05.640262+00:00",
        "last_outcome_line": 36,
    }
    (trw_dir / "sync-state.json").write_text(json.dumps(pre_fix))

    coord = SyncCoordinator(trw_dir=trw_dir)
    assert coord.get_primary_target_label() is None
    assert coord.get_last_push_at() == "2026-04-21T18:08:05.640262+00:00"
    assert coord.get_consecutive_failures() == 10653

    # Writing the new keys preserves every pre-existing key and bumps nothing.
    coord.record_target_health(primary_target_label="api.trwframework.com", secondary_targets=None)
    state = json.loads((trw_dir / "sync-state.json").read_text())
    assert state["secondary_targets"] == {}
    assert state["version"] == 1
    for key, value in pre_fix.items():
        assert state[key] == value


def test_state_write_is_idempotent(trw_dir: Path) -> None:
    """PRD-FIX-125-NFR04: rewriting the same cycle result produces the same file."""
    from trw_mcp.sync.coordinator import SyncCoordinator

    coord = SyncCoordinator(trw_dir=trw_dir)
    secondaries = {"localhost": {"status": "partial_error", "failed": 8, "last_error": None}}
    coord.record_target_health(primary_target_label="api.trwframework.com", secondary_targets=secondaries)
    first = (trw_dir / "sync-state.json").read_text()
    coord.record_target_health(primary_target_label="api.trwframework.com", secondary_targets=secondaries)

    assert (trw_dir / "sync-state.json").read_text() == first
