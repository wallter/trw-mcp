"""Tests for BackendSyncClient sync cycle behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
    client._puller.pull_intel_state = AsyncMock(
        return_value=PullResult(
            state={"etag": "etag-1"},
            etag="etag-1",
            team_learnings=[{"source_learning_id": "remote-1", "sync_seq": 7}],
            sync_hints={},
            status_code=200,
        )
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
async def test_company_entries_do_not_advance_org_pull_cursor(tmp_path) -> None:
    """PRD-INFRA-139 P1-B: company rows page on their OWN cursor, not the org's.

    A company-tier entry (source=company_sync) rides in team_learnings with a
    high per-company sync_seq. It must NOT be folded into the org pull_seq (that
    is the disjoint-cursor bug that hid company rows); the org cursor advances
    only on the org's own team rows, while the company cursor advances from the
    server's advertised next_company_seq.
    """
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 4
    client._coordinator.get_last_company_pull_seq.return_value = 1
    client._pusher = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(
        return_value=PullResult(
            state={"etag": "etag-1"},
            etag="etag-1",
            team_learnings=[
                {"source_learning_id": "team-1", "sync_seq": 6},
                # Company row with a high per-company seq that must NOT poison org cursor.
                {
                    "source_learning_id": "co-1",
                    "sync_seq": 999,
                    "metadata": {"source": "company_sync", "company_seq": 999},
                },
            ],
            sync_hints={},
            status_code=200,
            next_company_seq=3,
        )
    )
    client._puller.merge_team_learnings.return_value = 2
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    # The pull request carried the independent company cursor.
    _, kwargs = client._puller.pull_intel_state.call_args
    assert kwargs["since_company_seq"] == 1

    # Org cursor advanced to the org row's seq (6), NOT the company seq (999).
    client._coordinator.record_sync_success.assert_called_once_with(
        pushed=0,
        pulled=2,
        push_seq=0,
        pull_seq=6,
        pull_completed=True,
    )
    # Company cursor advanced independently from the server's next_company_seq.
    client._coordinator.record_company_pull_seq.assert_called_once_with(3)


@pytest.mark.asyncio
async def test_run_one_cycle_offloads_blocking_local_sync_work(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Local scans/bookkeeping must not block foreground MCP requests."""
    from trw_mcp.sync import client as sync_client
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.outcomes import PendingOutcome
    from trw_mcp.sync.pull import PullResult
    from trw_mcp.sync.push import PushResult

    offloaded: list[object] = []

    async def fake_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        offloaded.append(func)
        return func(*args, **kwargs)

    def fake_load_pending_outcomes(*_args: Any, **_kwargs: Any) -> list[PendingOutcome]:
        return [
            PendingOutcome(
                payload={"session_id": "run-1"},
                line_no=1,
                run_dir=tmp_path / "runs" / "task" / "run-1",
                sync_hash="hash-1",
                run_id="run-1",
            )
        ]

    fake_write_markers = MagicMock()
    monkeypatch.setattr(sync_client.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(sync_client, "load_pending_outcomes", fake_load_pending_outcomes)
    monkeypatch.setattr(sync_client, "_write_synced_markers", fake_write_markers)

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_outcome_line.return_value = 0
    client._coordinator.get_last_pull_seq.return_value = 5
    client._cache = MagicMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(return_value=PullResult(status_code=304, not_modified=True))
    dirty_entry = SimpleNamespace(id="L-1", sync_seq=7)
    client._get_dirty_entries = MagicMock(return_value=[dirty_entry])
    client._mark_synced = MagicMock()
    client._fanout_push = AsyncMock(
        return_value=(
            {"localhost": {"error": None}},
            PushResult(pushed=1, failed=0, skipped=0),
            True,
        )
    )

    await client._run_one_cycle()

    assert client._get_dirty_entries in offloaded
    assert fake_load_pending_outcomes in offloaded
    assert client._mark_synced in offloaded
    assert fake_write_markers in offloaded
    client._mark_synced.assert_called_once_with([dirty_entry])
    fake_write_markers.assert_called_once()


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
    client._puller.pull_intel_state = AsyncMock(
        return_value=PullResult(
            state={"etag": "etag-1"},
            etag="etag-1",
            sync_hints={
                "next_poll_recommended_at": (datetime.now(tz=timezone.utc) + timedelta(seconds=5)).isoformat(),
                "polling_cap_seconds": 120,
                "interval_seconds": 120,
            },
            team_learnings=[],
            status_code=200,
        )
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
    client._puller.pull_intel_state = AsyncMock(
        return_value=PullResult(
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
    client._puller.pull_intel_state = AsyncMock(
        return_value=PullResult(
            state={"etag": "etag-1"},
            etag="etag-1",
            sync_hints={"polling_cap_seconds": 5400, "interval_seconds": 5400},
            team_learnings=[],
            status_code=200,
        )
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
    client._puller.pull_intel_state = AsyncMock(return_value=PullResult(status_code=304, not_modified=True))
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
    client._puller.pull_intel_state = AsyncMock(return_value=PullResult(status_code=304, not_modified=True))
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
    client._puller.pull_intel_state = AsyncMock(return_value=PullResult(status_code=304, not_modified=True))
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
    client._puller.pull_intel_state = AsyncMock(return_value=None)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    client._coordinator.record_sync_failure.assert_called_once_with("pull failed")
    client._coordinator.record_sync_success.assert_not_called()


@pytest.mark.asyncio
async def test_run_one_cycle_reports_partial_target_failures_truthfully(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Cycle summary counts payload failures as partial errors, not success."""
    from trw_mcp.sync import client as sync_client
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult
    from trw_mcp.sync.push import PushResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 3
    client._coordinator.get_last_outcome_line.return_value = 0
    client._coordinator.get_consecutive_failures.return_value = 1
    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(return_value=PullResult(status_code=304, not_modified=True))
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[SimpleNamespace(id="L-1", sync_seq=5)])
    client._mark_synced = MagicMock()
    client._fanout_push = AsyncMock(
        return_value=(
            {
                "example.com": {
                    "pushed": 0,
                    "skipped": 98,
                    "failed": 2,
                    "error": None,
                    "status": "partial_error",
                }
            },
            PushResult(pushed=0, failed=2, skipped=98),
            False,
        )
    )
    log = MagicMock()
    monkeypatch.setattr(sync_client, "logger", log)

    await client._run_one_cycle()

    cycle_reports = [
        call.kwargs for call in log.info.call_args_list if call.args and call.args[0] == "sync_cycle_report"
    ]
    assert cycle_reports
    assert cycle_reports[-1]["successful"] == 0
    assert cycle_reports[-1]["partial_error"] == 1
    assert cycle_reports[-1]["failed"] == 0
    assert cycle_reports[-1]["unhealthy"] == 1
    client._coordinator.record_sync_failure.assert_called_once_with("push failed: 2 entries")
    client._coordinator.record_pull_success.assert_called_once_with(pull_seq=3)
    assert client._next_sleep_seconds == 600.0
    assert client._scheduled_interval_seconds == 600.0
    client._mark_synced.assert_not_called()


def test_failure_backoff_delay_is_bounded() -> None:
    """Persistent sync failures back off without exceeding the fixed safety cap."""
    from trw_mcp.sync.client import _failure_backoff_delay_seconds

    assert _failure_backoff_delay_seconds(300, 1) == 600
    assert _failure_backoff_delay_seconds(300, 2) == 1200
    assert _failure_backoff_delay_seconds(300, 10) == 3600


@pytest.mark.asyncio
async def test_offload_sync_work_warns_for_pathological_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slow offloaded local work carries explicit warning telemetry."""
    from trw_mcp.sync import client as sync_client

    ticks = iter([0.0, 12.5])
    log = MagicMock()
    monkeypatch.setattr(sync_client, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(sync_client, "logger", log)

    result = await sync_client._offload_sync_work("load_pending_outcomes", lambda: "ok")

    assert result == "ok"
    log.warning.assert_called_once_with(
        "sync_local_work_offloaded",
        label="load_pending_outcomes",
        duration_ms=12500.0,
        slow=True,
        slow_threshold_ms=10000,
    )
    log.info.assert_not_called()
