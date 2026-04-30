"""Tests for BackendSyncClient runtime, push, and lifespan behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend
from trw_memory.sync.delta import DeltaTracker

from tests._test_sync_client_support import _acquired_lock, _make_config


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
async def test_run_one_cycle_pushes_pending_outcomes_after_learning_sync(tmp_path) -> None:
    """Successful cycles upload newly appended recall outcomes after learnings."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    run_meta = tmp_path / "runs" / "task-1" / "run-outcome" / "meta"
    run_meta.mkdir(parents=True)
    (run_meta / "run.yaml").write_text(
        (
            "session_metrics:\n"
            "  status: success\n"
            "  rework_rate:\n"
            "    rework_rate: 0.1\n"
            "    total_files: 1\n"
            "  learning_exposure:\n"
            "    ids:\n"
            "      - L-outcome\n"
        ),
        encoding="utf-8",
    )

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 3
    client._coordinator.get_last_outcome_line.return_value = 0
    client._pusher = MagicMock()
    client._pusher.push_learnings.return_value = SimpleNamespace(pushed=1, failed=0, skipped=0)
    client._pusher.push_outcomes.return_value = SimpleNamespace(pushed=1, failed=0, skipped=0)
    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(status_code=304, not_modified=True)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[SimpleNamespace(id="L-1", sync_seq=5)])
    client._mark_synced = MagicMock()

    await client._run_one_cycle()

    client._pusher.push_outcomes.assert_called_once()
    pushed_outcome = client._pusher.push_outcomes.call_args.args[0][0]
    assert pushed_outcome["learning_ids"] == ["L-outcome"]
    assert pushed_outcome["build_passed"] is True
    client._coordinator.record_outcome_push_success.assert_called_once_with(1)


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
    client._coordinator.record_pull_success.assert_called_once_with(pull_seq=3)
    client._coordinator.record_sync_success.assert_not_called()


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
