"""Tests for BackendSyncClient sync cycle behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests._test_sync_client_support import _acquired_lock, _make_config
from trw_mcp.sync._team_merge_result import TeamMergeResult


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
    client._puller.merge_team_learnings.return_value = TeamMergeResult(attempted=1, inserted=1)
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
    client._puller.merge_team_learnings.return_value = TeamMergeResult(attempted=2, inserted=2)
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
    from trw_mcp.sync._client_push import TargetPushOutcome
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
    # PRD-FIX-125-FR01: the cycle verdict is keyed on the PRIMARY target's own
    # report entry, so the stub report must be keyed by the configured target
    # label ("example.com" from _make_config). The pre-FR01 rule quantified over
    # report VALUES and never looked at the key, which let this stub drift.
    client._fanout_push = AsyncMock(
        return_value=(
            {"example.com": {"error": None}},
            TargetPushOutcome(learnings=PushResult(pushed=1, failed=0, skipped=0)),
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
    client._puller.merge_team_learnings.return_value = TeamMergeResult(attempted=0, inserted=0)
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
    client._puller.merge_team_learnings.return_value = TeamMergeResult(attempted=0, inserted=0)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    await client._run_one_cycle()

    assert client._next_sleep_seconds == 0.0
    assert client._scheduled_interval_seconds == 0.0
    assert client._next_cycle_force is True


@pytest.mark.asyncio
async def test_run_one_cycle_accepts_server_intervals_above_one_hour(tmp_path) -> None:
    """Server-approved schedules honor the server-side PRD 7200-second ceiling."""
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
    client._puller.merge_team_learnings.return_value = TeamMergeResult(attempted=0, inserted=0)
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
    from trw_mcp.sync._client_push import TargetPushOutcome
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
            TargetPushOutcome(learnings=PushResult(pushed=0, failed=2, skipped=98)),
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


async def _cycle_with(tmp_path: Any, *, merge: TeamMergeResult, team_sync_enabled: bool = True) -> MagicMock:
    """Run one cycle over a single team learning at sync_seq 7, cursor at 4."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    config = _make_config()
    config.team_sync_enabled = team_sync_enabled
    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(config, tmp_path)
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
    client._puller.merge_team_learnings.return_value = merge
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])
    await client._run_one_cycle()
    return client._coordinator


def _recorded_pull_seq(coordinator: MagicMock) -> int:
    call = coordinator.record_sync_success.call_args or coordinator.record_pull_success.call_args
    return int(call.kwargs["pull_seq"])


class TestThePullCursorNeverPassesAnUnjudgedItem:
    """Pulls are ``since_seq`` bounded, so the cursor is a one-way door.

    ``next_pull_seq`` was computed from everything PULLED without ever consulting
    ``merge_result``, so an item the merge never saw was stepped over and never
    offered again — permanently discarded, with no number reported anywhere.

    Reported by a cross-family sweep 2026-09-12. The tell that this was oversight
    rather than design: the same block already raises its log level on
    ``merge_result.rejected`` and carries a comment that "a cycle that pulled 50
    and applied 1 is not a completed cycle" — someone saw the discrepancy and
    fixed the REPORTING half while the cursor kept advancing over it.
    """

    @pytest.mark.asyncio
    async def test_team_sync_disabled_does_not_consume_team_learnings(self, tmp_path) -> None:
        """The sharpest arm. ``merge_team_learnings`` is only called when team
        sync is enabled, but the pull and the cursor ran unconditionally — so
        every team learning that arrived while the feature was OFF advanced the
        cursor past itself. Enabling team sync later could not recover them."""
        coordinator = await _cycle_with(tmp_path, merge=TeamMergeResult(), team_sync_enabled=False)
        assert _recorded_pull_seq(coordinator) == 4, "team learnings were consumed while team sync was disabled"

    @pytest.mark.asyncio
    async def test_an_unavailable_merge_holds_the_whole_batch(self, tmp_path) -> None:
        """``unavailable`` means the merge could not run at all — a whole batch
        skipped at once, with nothing judged."""
        coordinator = await _cycle_with(tmp_path, merge=TeamMergeResult(attempted=1, unavailable=True))
        assert _recorded_pull_seq(coordinator) == 4

    @pytest.mark.asyncio
    async def test_a_failed_item_holds_the_cursor(self, tmp_path) -> None:
        """``failed`` raised while storing: never judged, so never passed over."""
        coordinator = await _cycle_with(tmp_path, merge=TeamMergeResult(attempted=1, failed=1))
        assert _recorded_pull_seq(coordinator) == 4

    @pytest.mark.asyncio
    async def test_a_judged_rejection_still_advances(self, tmp_path) -> None:
        """Non-vacuity partner, and the reason the arms are not treated alike.

        ``invalid`` / ``skipped_no_id`` / ``quarantined`` are DECISIONS about the
        item — it would be judged identically next time, so holding the cursor
        would make a poison-pill loop that stalls sync forever on one bad row.
        """
        coordinator = await _cycle_with(tmp_path, merge=TeamMergeResult(attempted=1, quarantined=1))
        assert _recorded_pull_seq(coordinator) == 7

    @pytest.mark.asyncio
    async def test_a_clean_apply_still_advances(self, tmp_path) -> None:
        """The other half of the partner: the ordinary path must keep moving, or
        the fix would have frozen every cursor in place."""
        coordinator = await _cycle_with(tmp_path, merge=TeamMergeResult(attempted=1, inserted=1))
        assert _recorded_pull_seq(coordinator) == 7


async def _client_after_cycle(tmp_path: Any, *, merge: TeamMergeResult) -> Any:
    """Like ``_cycle_with`` but returns the client, for cache/ETag assertions."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    config = _make_config()
    config.team_sync_enabled = True
    config.intel_cache_enabled = True
    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(config, tmp_path)
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
    client._puller.merge_team_learnings.return_value = merge
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])
    await client._run_one_cycle()
    return client


class TestAHeldCursorNeverCachesTheEtag:
    """PRD-FIX-138-FR02. The cache recorded the response ETag BEFORE the merge ran.

    When the merge then held the cursor, the next pull sent If-None-Match with
    that ETag, the server answered 304, and the 304 arm booked a success — so the
    held batch was never re-offered. Held cursor + cached ETag = permanent stall.
    """

    @pytest.mark.asyncio
    async def test_a_blocked_item_is_a_judged_decision_and_advances(self, tmp_path) -> None:
        """FR01 partner: the new ``blocked`` arm sits with the judged rejections."""
        coordinator = await _cycle_with(tmp_path, merge=TeamMergeResult(attempted=1, blocked=1))
        assert _recorded_pull_seq(coordinator) == 7

    @pytest.mark.asyncio
    async def test_a_held_cursor_withholds_the_etag(self, tmp_path) -> None:
        client = await _client_after_cycle(tmp_path, merge=TeamMergeResult(attempted=1, failed=1))
        client._cache.update.assert_called_once_with({"etag": "etag-1"}, etag=None)

    @pytest.mark.asyncio
    async def test_an_unavailable_merge_withholds_the_etag(self, tmp_path) -> None:
        client = await _client_after_cycle(tmp_path, merge=TeamMergeResult(attempted=1, unavailable=True))
        client._cache.update.assert_called_once_with({"etag": "etag-1"}, etag=None)

    @pytest.mark.asyncio
    async def test_an_advancing_cursor_still_caches_the_etag(self, tmp_path) -> None:
        """Non-vacuity: the conditional-request optimisation survives on the happy path."""
        client = await _client_after_cycle(tmp_path, merge=TeamMergeResult(attempted=1, inserted=1))
        client._cache.update.assert_called_once_with({"etag": "etag-1"}, etag="etag-1")
