"""Tests for BackendSyncClient sync cycle behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tests._test_sync_client_support import _acquired_lock, _make_config


@pytest.mark.asyncio
async def test_run_one_cycle_pulls_even_without_dirty_entries(tmp_path) -> None:
    """A sync cycle still executes pull when there is nothing local to push."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 4
    client._pusher = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(
        state={"etag": "etag-1"},
        etag="etag-1",
        team_learnings=[{"source_learning_id": "remote-1", "sync_seq": 7}],
        sync_hints={},
        status_code=200,
    )
    client._puller.merge_team_learnings.return_value = 1
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    client._pusher.push_learnings.assert_not_called()
    client._puller.pull_intel_state.assert_called_once()
    client._puller.merge_team_learnings.assert_called_once()
    client._coordinator.record_sync_success.assert_called_once_with(
        pushed=0,
        pulled=1,
        push_seq=0,
        pull_seq=7,
        pull_completed=True,
    )


@pytest.mark.asyncio
async def test_run_one_cycle_applies_server_next_poll_hint(tmp_path) -> None:
    """Server hints adjust the next poll schedule using the backend cap."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 0
    client._pusher = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(
        state={"etag": "etag-1"},
        etag="etag-1",
        sync_hints={
            "next_poll_recommended_at": (datetime.now(tz=UTC) + timedelta(seconds=5)).isoformat(),
            "polling_cap_seconds": 120,
            "interval_seconds": 120,
        },
        team_learnings=[],
        status_code=200,
    )
    client._puller.merge_team_learnings.return_value = 0
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    assert client._next_sleep_seconds == 120
    assert client._scheduled_interval_seconds == 120
    assert client._next_cycle_force is False


@pytest.mark.asyncio
async def test_run_one_cycle_honors_significant_updates_with_immediate_repoll(tmp_path) -> None:
    """Significant update hints schedule an immediate forced follow-up pull."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 0
    client._pusher = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(
        state={"etag": "etag-1"},
        etag="etag-1",
        sync_hints={
            "significant_updates_available": True,
            "polling_cap_seconds": 300,
            "interval_seconds": 300,
        },
        team_learnings=[],
        status_code=200,
    )
    client._puller.merge_team_learnings.return_value = 0
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    assert client._next_sleep_seconds == 0.0
    assert client._scheduled_interval_seconds == 0.0
    assert client._next_cycle_force is True


@pytest.mark.asyncio
async def test_run_one_cycle_accepts_server_intervals_above_one_hour(tmp_path) -> None:
    """Server-approved schedules honor the backend/PRD 7200-second ceiling."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(sync_interval_seconds=30), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 0
    client._pusher = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(
        state={"etag": "etag-1"},
        etag="etag-1",
        sync_hints={"polling_cap_seconds": 5400, "interval_seconds": 5400},
        team_learnings=[],
        status_code=200,
    )
    client._puller.merge_team_learnings.return_value = 0
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    assert client._next_sleep_seconds == 5400
    assert client._scheduled_interval_seconds == 5400


@pytest.mark.asyncio
async def test_run_one_cycle_passes_scheduled_interval_to_coordinator(tmp_path) -> None:
    """Backend-scheduled delays are enforced through the coordinator gate."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._scheduled_interval_seconds = 120
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 0
    client._pusher = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(status_code=304, not_modified=True)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    client._coordinator.should_sync.assert_called_once_with(sync_interval=120)


@pytest.mark.asyncio
async def test_run_one_cycle_records_not_modified_pull_as_success(tmp_path) -> None:
    """304 pulls are successful syncs, not failures."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 5
    client._pusher = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(status_code=304, not_modified=True)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    client._coordinator.record_sync_success.assert_called_once_with(
        pushed=0,
        pulled=0,
        push_seq=0,
        pull_seq=5,
        pull_completed=True,
    )
    client._coordinator.record_sync_failure.assert_not_called()


@pytest.mark.asyncio
async def test_run_one_cycle_preserves_last_server_schedule_after_not_modified(tmp_path) -> None:
    """A 304 keeps the last applied server schedule instead of dropping to local defaults."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(sync_interval_seconds=30), tmp_path)
    client._last_applied_schedule_seconds = 120.0
    client._next_sleep_seconds = 0.0
    client._scheduled_interval_seconds = 0.0
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 5
    client._pusher = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(status_code=304, not_modified=True)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    assert client._next_sleep_seconds == 120.0
    assert client._scheduled_interval_seconds == 120.0


@pytest.mark.asyncio
async def test_run_one_cycle_records_pull_failures_as_failures(tmp_path) -> None:
    """Transport failures keep sync bookkeeping truthful."""
    from trw_mcp.sync.client import BackendSyncClient

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 5
    client._pusher = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = None
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    client._coordinator.record_sync_failure.assert_called_once_with("pull failed")
    client._coordinator.record_sync_success.assert_not_called()
