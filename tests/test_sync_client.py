"""Tests for BackendSyncClient sync loop behavior."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP

from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend
from trw_memory.sync.delta import DeltaTracker


def _make_config(**overrides: object) -> SimpleNamespace:
    base = {
        "sync_interval_seconds": 300,
        "backend_url": "http://example.com",
        "backend_api_key": "key",
        "sync_push_batch_size": 100,
        "sync_push_timeout_seconds": 10.0,
        "sync_pull_timeout_seconds": 5.0,
        "intel_cache_ttl_seconds": 3600,
        "intel_cache_enabled": True,
        "team_sync_enabled": True,
        "model_family": "opus",
        "framework_version": "v1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@contextmanager
def _acquired_lock() -> object:
    yield True


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


def test_get_dirty_entries_does_not_skip_low_seq_unsynced_updates(tmp_path) -> None:
    """Dirty discovery relies on unsynced state, not a global push watermark."""
    from trw_mcp.sync.client import BackendSyncClient

    backend = SQLiteBackend(tmp_path / "memory.db")
    backend.store(MemoryEntry(id="L-low", content="initial"))
    DeltaTracker.mark_synced(["L-low"], backend)
    backend.update("L-low", content="updated")

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)

    client._coordinator = MagicMock()
    client._coordinator.get_last_push_seq.return_value = 99

    with patch("trw_mcp.state._memory_connection.get_backend", return_value=backend):
        dirty = client._get_dirty_entries()

    assert [entry.id for entry in dirty] == ["L-low"]
    backend.close()


@pytest.mark.asyncio
async def test_run_one_cycle_records_highest_pushed_sync_seq(tmp_path) -> None:
    """Successful push cycles persist the highest synced local sequence."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 3
    client._pusher = MagicMock()
    client._pusher.push_learnings.return_value = SimpleNamespace(pushed=2, failed=0, skipped=0)
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(status_code=304, not_modified=True)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(
        return_value=[
            SimpleNamespace(id="L-1", sync_seq=2),
            SimpleNamespace(id="L-2", sync_seq=5),
        ]
    )
    client._mark_synced = MagicMock()

    await client._run_one_cycle()

    client._coordinator.record_sync_success.assert_called_once_with(
        pushed=2,
        pulled=0,
        push_seq=5,
        pull_seq=3,
        pull_completed=True,
    )


@pytest.mark.asyncio
async def test_run_one_cycle_keeps_entries_dirty_when_push_reports_failures(tmp_path) -> None:
    """Partial ingest failures must not clear local dirty state."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 3
    client._pusher = MagicMock()
    client._pusher.push_learnings.return_value = SimpleNamespace(pushed=1, failed=1, skipped=0)
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(status_code=304, not_modified=True)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(
        return_value=[
            SimpleNamespace(id="L-1", sync_seq=2),
            SimpleNamespace(id="L-2", sync_seq=5),
        ]
    )
    client._mark_synced = MagicMock()

    await client._run_one_cycle()

    client._mark_synced.assert_not_called()
    client._coordinator.record_sync_failure.assert_called_once_with("push failed: 1 entries")


@pytest.mark.asyncio
async def test_run_sync_loop_uses_updated_sleep_schedule(tmp_path, monkeypatch) -> None:
    """run_sync_loop sleeps using the dynamically updated next delay."""
    from trw_mcp.sync.client import BackendSyncClient

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) >= 2:
            raise asyncio.CancelledError

    async def fake_run_one_cycle(*, force: bool = False) -> None:
        client._next_sleep_seconds = 42.0

    monkeypatch.setattr("trw_mcp.sync.client.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(client, "_run_one_cycle", fake_run_one_cycle)

    await client.run_sync_loop()

    assert delays == [300, 42.0]


@pytest.mark.asyncio
async def test_create_app_registers_sync_lifespan() -> None:
    """The production FastMCP app wires the sync lifespan hook."""
    from trw_mcp.server._app import _build_sync_lifespan, create_app

    app = create_app(instructions="test", middleware=[])

    assert app._lifespan is _build_sync_lifespan


@pytest.mark.asyncio
async def test_sync_lifespan_starts_and_cancels_backend_sync_client(tmp_path, monkeypatch) -> None:
    """Configured sync lifecycles start and stop the background sync task."""
    from trw_mcp.server._app import _build_sync_lifespan

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_run_sync_loop() -> None:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    fake_client = MagicMock()
    fake_client.run_sync_loop = fake_run_sync_loop

    monkeypatch.setattr("trw_mcp.server._app._try_load_config", lambda: _make_config())
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: tmp_path)
    with patch("trw_mcp.sync.client.BackendSyncClient", return_value=fake_client):
        async with _build_sync_lifespan(FastMCP("test")):
            await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(cancelled.wait(), timeout=1)
