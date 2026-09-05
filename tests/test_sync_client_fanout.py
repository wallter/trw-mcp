"""Tests for fan-out sync target resolution and per-target failure isolation.

Covers:
- TRWConfig.resolved_sync_targets accessor
- BackendSyncClient fan-out across targets
- Per-target failure isolation (ConnectionError, 429, etc.)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from pydantic import SecretStr

from trw_mcp.models.config import TRWConfig


class TestResolvedSyncTargets:
    def test_platform_urls_only_returns_all_with_platform_key(self) -> None:
        config = TRWConfig(
            backend_url="",
            backend_api_key="",
            platform_urls=["https://api.trwframework.com", "http://localhost:5002"],
            platform_api_key=SecretStr("pk"),
        )
        targets = config.resolved_sync_targets
        assert targets == [
            ("https://api.trwframework.com", "pk"),
            ("http://localhost:5002", "pk"),
        ]

    def test_backend_url_override_wins_first_slot(self) -> None:
        config = TRWConfig(
            backend_url="http://override.example",
            backend_api_key="ek",
            platform_urls=["https://api.trwframework.com", "http://localhost:5002"],
            platform_api_key=SecretStr("pk"),
        )
        targets = config.resolved_sync_targets
        assert targets[0] == ("http://override.example", "ek")
        assert ("https://api.trwframework.com", "pk") in targets
        assert ("http://localhost:5002", "pk") in targets

    def test_duplicate_urls_first_occurrence_wins(self) -> None:
        config = TRWConfig(
            backend_url="https://api.trwframework.com",
            backend_api_key="ek",
            platform_urls=["https://api.trwframework.com/", "http://localhost:5002"],
            platform_api_key=SecretStr("pk"),
        )
        targets = config.resolved_sync_targets
        urls = [t[0] for t in targets]
        # Explicit override with key "ek" wins; trailing slash dupe is collapsed.
        assert urls[0] == "https://api.trwframework.com"
        assert targets[0][1] == "ek"
        assert "http://localhost:5002" in urls
        assert len(targets) == 2

    def test_empty_platform_and_backend_returns_empty(self) -> None:
        config = TRWConfig(backend_url="", backend_api_key="", platform_urls=[])
        assert config.resolved_sync_targets == []

    def test_target_with_empty_api_key_is_dropped(self) -> None:
        config = TRWConfig(
            backend_url="",
            backend_api_key="",
            platform_urls=["https://api.trwframework.com"],
            platform_api_key=SecretStr(""),
        )
        assert config.resolved_sync_targets == []

    def test_resolved_backend_url_returns_first_target(self) -> None:
        config = TRWConfig(
            backend_url="",
            platform_urls=["https://api.trwframework.com", "http://localhost:5002"],
            platform_api_key=SecretStr("pk"),
        )
        assert config.resolved_backend_url == "https://api.trwframework.com"
        assert config.resolved_backend_api_key == "pk"


def _make_fanout_config() -> SimpleNamespace:
    return SimpleNamespace(
        sync_interval_seconds=300,
        sync_push_batch_size=100,
        sync_push_timeout_seconds=10.0,
        sync_pull_timeout_seconds=5.0,
        intel_cache_ttl_seconds=3600,
        intel_cache_enabled=True,
        team_sync_enabled=True,
        model_family="opus",
        framework_version="v1",
        # PRD-SEC-004: fan-out push tests exercise the CONSENTED path.
        learning_sharing_enabled=True,
        platform_telemetry_enabled=True,
        resolved_sync_targets=[
            ("https://api.trwframework.com", "pk"),
            ("http://localhost:5002", "pk"),
        ],
        resolved_backend_url="https://api.trwframework.com",
        resolved_backend_api_key="pk",
    )


def _acquired_lock():
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        yield True

    return _cm()


class TestFanoutInitLogging:
    def test_targets_resolved_log_emitted_at_init(self, tmp_path, caplog) -> None:
        from trw_mcp.sync.client import BackendSyncClient

        with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="c1"):
            client = BackendSyncClient(_make_fanout_config(), tmp_path)
        assert len(client._targets) == 2
        labels = [t.label for t in client._targets]
        assert labels == ["api.trwframework.com", "localhost"]

    def test_empty_targets_cycle_is_noop(self, tmp_path) -> None:
        from trw_mcp.sync.client import BackendSyncClient

        config = SimpleNamespace(
            sync_interval_seconds=300,
            sync_push_batch_size=100,
            sync_push_timeout_seconds=10.0,
            sync_pull_timeout_seconds=5.0,
            intel_cache_ttl_seconds=3600,
            intel_cache_enabled=True,
            team_sync_enabled=True,
            model_family="",
            framework_version="",
            resolved_sync_targets=[],
            resolved_backend_url="",
            resolved_backend_api_key="",
        )
        with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="c1"):
            client = BackendSyncClient(config, tmp_path)
        assert client._targets == []

        import asyncio

        # No-op cycle should return without error and without touching coordinator.
        client._coordinator = MagicMock()
        asyncio.run(client._run_one_cycle())
        client._coordinator.acquire_sync_lock.assert_not_called()


@pytest.mark.asyncio
async def test_per_target_failure_isolation(tmp_path) -> None:
    """First target raises ConnectionError; second succeeds; no exception propagates."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult
    from trw_mcp.sync.push import PushResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="c1"):
        client = BackendSyncClient(_make_fanout_config(), tmp_path)

    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.side_effect = _acquired_lock
    client._coordinator.get_last_pull_seq.return_value = 0
    client._coordinator.get_last_outcome_line.return_value = 0

    # PRD-FIX-087: pusher methods are now async — use AsyncMock per method.
    primary_pusher = MagicMock()
    primary_pusher.push_learnings = AsyncMock(side_effect=ConnectionError("refused"))
    primary_pusher.push_outcomes = AsyncMock(side_effect=ConnectionError("refused"))
    client._pusher = primary_pusher

    secondary_pusher = MagicMock()
    secondary_pusher.push_learnings = AsyncMock(return_value=PushResult(pushed=1, failed=0, skipped=0))
    secondary_pusher.push_outcomes = AsyncMock(return_value=PushResult(pushed=0, failed=0, skipped=0))
    client._pushers["localhost"] = secondary_pusher

    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(return_value=PullResult(status_code=304, not_modified=True))
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(
        return_value=[SimpleNamespace(id="L-1", sync_seq=5)],
    )
    client._mark_synced = MagicMock()

    pending_outcome = SimpleNamespace(payload={"session_id": "s1"}, line_no=1)
    # Must not propagate, and unacknowledged learnings/outcomes retry next cycle.
    with patch("trw_mcp.sync.client.load_pending_outcomes", return_value=[pending_outcome]):
        await client._run_one_cycle()
        await client._run_one_cycle()

    assert primary_pusher.push_learnings.call_count == 2
    assert secondary_pusher.push_learnings.call_count == 2
    # PRD-FIX-125-FR01: acknowledgement is keyed on the PRIMARY, which failed
    # here, so nothing is acked and the failure names the primary rather than a
    # target count (the pre-FR01 message was "1 of 2 targets failed", which read
    # identically whether the healthy production primary or the throwaway local
    # secondary was the broken one).
    client._mark_synced.assert_not_called()
    assert client._coordinator.record_sync_failure.call_args_list == [
        call("primary target api.trwframework.com push error"),
        call("primary target api.trwframework.com push error"),
    ]
    client._coordinator.record_outcome_push_success.assert_not_called()


@pytest.mark.asyncio
async def test_fanout_reports_partial_error_for_payload_failures() -> None:
    """Payload-level push failures are unhealthy even when the target call returns."""
    from trw_mcp.sync._client_push import TargetPushOutcome, fanout_push
    from trw_mcp.sync.push import PushResult

    target = SimpleNamespace(label="localhost", url="http://localhost:5002", api_key="pk")
    pusher = MagicMock()
    pusher.push_learnings = AsyncMock(return_value=PushResult(pushed=0, failed=2, skipped=98))

    report, aggregate = await fanout_push(
        client_id="c1",
        targets=[target],
        primary_pusher=pusher,
        pusher_map={"localhost": pusher},
        batch_size=100,
        timeout=5.0,
        dirty=[SimpleNamespace(id="L-1", sync_seq=1)],
        outcomes=[],
    )

    assert report["localhost"]["status"] == "partial_error"
    assert report["localhost"]["failed"] == 2
    assert report["localhost"]["error"] is None
    # The primary did not succeed, so its aggregate is zero-valued in BOTH kinds.
    assert aggregate == TargetPushOutcome()


@pytest.mark.asyncio
async def test_429_on_one_target_does_not_stop_others(tmp_path) -> None:
    """Rate-limit/HTTP error on first target leaves second target unaffected."""
    import httpx

    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult
    from trw_mcp.sync.push import PushResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="c1"):
        client = BackendSyncClient(_make_fanout_config(), tmp_path)

    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 0
    client._coordinator.get_last_outcome_line.return_value = 0

    # PRD-FIX-087: pusher methods are now async.
    primary = MagicMock()
    primary.push_learnings = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "429",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )
    )
    primary.push_outcomes = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "429",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )
    )
    client._pusher = primary

    secondary = MagicMock()
    secondary.push_learnings = AsyncMock(return_value=PushResult(pushed=1, failed=0, skipped=0))
    secondary.push_outcomes = AsyncMock(return_value=PushResult(pushed=0, failed=0, skipped=0))
    client._pushers["localhost"] = secondary

    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(return_value=PullResult(status_code=304, not_modified=True))
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(
        return_value=[SimpleNamespace(id="L-1", sync_seq=5)],
    )
    client._mark_synced = MagicMock()

    await client._run_one_cycle()

    secondary.push_learnings.assert_called_once()
    client._mark_synced.assert_not_called()
    # PRD-FIX-125-FR01: primary-keyed failure message (was "1 of 2 targets failed").
    client._coordinator.record_sync_failure.assert_called_once_with("primary target api.trwframework.com push error")


def _fanout_client(tmp_path):
    """A two-target client (primary api.trwframework.com, secondary localhost)."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="c1"):
        client = BackendSyncClient(_make_fanout_config(), tmp_path)

    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.side_effect = _acquired_lock
    client._coordinator.get_last_pull_seq.return_value = 0
    client._coordinator.get_last_outcome_line.return_value = 0
    client._coordinator.get_consecutive_failures.return_value = 0
    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(return_value=PullResult(status_code=304, not_modified=True))
    client._cache = MagicMock()
    client._mark_synced = MagicMock()
    return client


def _pusher(learnings, outcomes):
    """A pusher double whose two async push methods return fixed PushResults."""
    pusher = MagicMock()
    pusher.push_learnings = AsyncMock(return_value=learnings)
    pusher.push_outcomes = AsyncMock(return_value=outcomes)
    return pusher


@pytest.mark.asyncio
async def test_secondary_failure_does_not_fail_the_cycle(tmp_path) -> None:
    """PRD-FIX-125-FR01: a failing secondary must not pin the pipeline counter.

    Reproduces the measured live shape: over 161/161 retained cycles the
    production primary returned ``status=success, failed=0`` while the local dev
    secondary returned ``status=partial_error, failed=8`` (HTTP 401). The
    pre-FR01 all-targets rule recorded every one of those cycles as a failure,
    which is how ``consecutive_failures`` reached 10653 over 134 days.
    """
    from trw_mcp.sync.push import PushResult

    client = _fanout_client(tmp_path)
    client._pusher = _pusher(PushResult(pushed=8, failed=0, skipped=0), PushResult())
    client._pushers["localhost"] = _pusher(PushResult(pushed=0, failed=8, skipped=0), PushResult())
    dirty = [SimpleNamespace(id=f"L-{i}", sync_seq=i) for i in range(1, 9)]
    client._get_dirty_entries = MagicMock(return_value=dirty)

    with patch("trw_mcp.sync.client.load_pending_outcomes", return_value=[]):
        await client._run_one_cycle()

    assert client._coordinator.record_sync_failure.call_count == 0
    assert client._coordinator.record_sync_success.call_count == 1
    # The primary accepted all 8, so all 8 are acknowledged.
    client._mark_synced.assert_called_once_with(dirty)
    # The secondary's health is reported, not gating.
    health = client._coordinator.record_target_health.call_args.kwargs
    assert health["primary_target_label"] == "api.trwframework.com"
    assert health["secondary_targets"]["localhost"]["status"] == "partial_error"
    assert health["secondary_targets"]["localhost"]["failed"] == 8
    assert "api.trwframework.com" not in health["secondary_targets"]


@pytest.mark.asyncio
async def test_ack_slice_uses_primary_push_result(tmp_path) -> None:
    """PRD-FIX-125-FR01 / RISK-005: never ack what the PRIMARY did not accept.

    The primary succeeds but accepts nothing (``pushed=0, skipped=0`` — the live
    shape) while the secondary accepts all 8. ``_fanout_push`` previously
    returned the first *successful* target whose counts were non-zero, so the
    secondary's 8 became the acknowledgement slice and 8 entries were marked
    synced against a primary that had taken none of them.
    """
    from trw_mcp.sync.push import PushResult

    client = _fanout_client(tmp_path)
    client._pusher = _pusher(PushResult(pushed=0, failed=0, skipped=0), PushResult())
    client._pushers["localhost"] = _pusher(PushResult(pushed=8, failed=0, skipped=0), PushResult())
    dirty = [SimpleNamespace(id=f"L-{i}", sync_seq=i) for i in range(1, 9)]
    client._get_dirty_entries = MagicMock(return_value=dirty)

    with patch("trw_mcp.sync.client.load_pending_outcomes", return_value=[]):
        await client._run_one_cycle()

    client._mark_synced.assert_called_once_with([])
    assert client._coordinator.record_sync_success.call_args.kwargs["pushed"] == 0


@pytest.mark.asyncio
async def test_primary_failure_message_names_the_primary(tmp_path) -> None:
    """PRD-FIX-125-FR01: a partial_error on the PRIMARY is still a primary failure."""
    from trw_mcp.sync.push import PushResult

    client = _fanout_client(tmp_path)
    client._pusher = _pusher(PushResult(pushed=0, failed=3, skipped=0), PushResult())
    client._pushers["localhost"] = _pusher(PushResult(pushed=3, failed=0, skipped=0), PushResult())
    client._get_dirty_entries = MagicMock(return_value=[SimpleNamespace(id="L-1", sync_seq=1)])

    with patch("trw_mcp.sync.client.load_pending_outcomes", return_value=[]):
        await client._run_one_cycle()

    client._coordinator.record_sync_success.assert_not_called()
    message = client._coordinator.record_sync_failure.call_args.args[0]
    assert "api.trwframework.com" in message
    assert "partial_error" in message
    client._mark_synced.assert_not_called()


@pytest.mark.asyncio
async def test_empty_report_is_not_success(tmp_path) -> None:
    """PRD-FIX-125-FR01: a report with no primary entry is a failure, not success."""
    from trw_mcp.sync._client_push import TargetPushOutcome

    client = _fanout_client(tmp_path)
    client._get_dirty_entries = MagicMock(return_value=[SimpleNamespace(id="L-1", sync_seq=1)])
    client._fanout_push = AsyncMock(return_value=({}, TargetPushOutcome()))

    with patch("trw_mcp.sync.client.load_pending_outcomes", return_value=[]):
        await client._run_one_cycle()

    client._coordinator.record_sync_success.assert_not_called()
    assert "api.trwframework.com" in client._coordinator.record_sync_failure.call_args.args[0]
    client._mark_synced.assert_not_called()


@pytest.mark.asyncio
async def test_ack_slice_does_not_count_outcome_inserts_toward_learnings(tmp_path) -> None:
    """PRD-FIX-125-FR01: each ack path slices its OWN list by its OWN kind's count.

    The per-target result used to be one summed ``PushResult`` over both kinds,
    so a cycle that pushed BOTH learnings and outcomes added the outcome inserts
    to the learning slice: primary accepts 2 of 5 learnings and inserts 8
    outcomes, and ``dirty[: 2 + 8]`` marked all 5 synced — three of them never
    accepted by the primary, and permanently un-retried because they were no
    longer dirty.
    """
    from trw_mcp.sync.outcomes import PendingOutcome
    from trw_mcp.sync.push import PushResult

    client = _fanout_client(tmp_path)
    client._pusher = _pusher(
        PushResult(pushed=2, failed=0, skipped=0),
        PushResult(pushed=8, failed=0, skipped=0),
    )
    client._pushers["localhost"] = _pusher(PushResult(), PushResult())
    dirty = [SimpleNamespace(id=f"L-{i}", sync_seq=i) for i in range(1, 6)]
    client._get_dirty_entries = MagicMock(return_value=dirty)
    outcomes = [PendingOutcome(payload={"session_id": f"s{i}"}, line_no=i) for i in range(1, 9)]

    with patch("trw_mcp.sync.client.load_pending_outcomes", return_value=outcomes):
        await client._run_one_cycle()

    # Exactly the 2 the primary accepted — not 2 + the 8 outcome inserts.
    client._mark_synced.assert_called_once_with(dirty[:2])


@pytest.mark.asyncio
async def test_outcomes_are_not_acked_when_the_primary_never_sent_them(tmp_path) -> None:
    """PRD-FIX-125-FR01: telemetry consent OFF must not look like acceptance.

    ``push_outcomes`` returns an empty, zero-failure ``PushResult`` without
    sending anything when ``platform_telemetry_enabled`` is False. A whole-batch
    acknowledgement keyed on "no failures" would read that as success and mark
    outcomes synced that never left the machine, so the consent flag is checked
    rather than inferred from the counts.
    """
    from trw_mcp.sync.outcomes import PendingOutcome
    from trw_mcp.sync.push import PushResult

    client = _fanout_client(tmp_path)
    client._platform_telemetry_enabled = False
    client._pusher = _pusher(PushResult(), PushResult())
    client._pushers["localhost"] = _pusher(PushResult(), PushResult())
    client._get_dirty_entries = MagicMock(return_value=[])
    outcomes = [PendingOutcome(payload={"session_id": "s1"}, line_no=7)]

    with patch("trw_mcp.sync.client.load_pending_outcomes", return_value=outcomes):
        await client._run_one_cycle()

    client._coordinator.record_outcome_push_success.assert_not_called()


@pytest.mark.asyncio
async def test_deduplicated_outcomes_still_advance_the_watermark(tmp_path) -> None:
    """PRD-FIX-125-FR01: a re-offer the primary already holds is acceptance.

    ``push_outcomes`` counts only ``inserted`` and has no ``skipped`` concept, so
    a batch the backend already has returns ``pushed: 0, failed: 0`` — the exact
    shape measured live on 2026-09-03 for a batch of 8. Slicing this path by
    ``pushed`` the way the learnings path is sliced would acknowledge nothing
    forever and re-offer the same payload every cycle, which is the defect
    PRD-FIX-125 exists to close rather than reproduce. A partial acceptance is
    not representable here: ``push_outcomes`` fails a batch as a unit, and any
    failure makes the primary ``partial_error``, which this branch already
    excludes.
    """
    from trw_mcp.sync.outcomes import PendingOutcome
    from trw_mcp.sync.push import PushResult

    client = _fanout_client(tmp_path)
    client._pusher = _pusher(PushResult(), PushResult(pushed=0, failed=0, skipped=0))
    client._pushers["localhost"] = _pusher(PushResult(), PushResult(pushed=0, failed=8, skipped=0))
    client._get_dirty_entries = MagicMock(return_value=[])
    outcomes = [PendingOutcome(payload={"session_id": f"s{i}"}, line_no=i) for i in range(30, 38)]

    with patch("trw_mcp.sync.client.load_pending_outcomes", return_value=outcomes):
        await client._run_one_cycle()

    client._coordinator.record_outcome_push_success.assert_called_once_with(37)


@pytest.mark.asyncio
async def test_outcome_failure_on_the_primary_acks_nothing(tmp_path) -> None:
    """PRD-FIX-125-FR01: a failed outcome batch is a primary failure, not a partial ack."""
    from trw_mcp.sync.outcomes import PendingOutcome
    from trw_mcp.sync.push import PushResult

    client = _fanout_client(tmp_path)
    client._pusher = _pusher(PushResult(), PushResult(pushed=0, failed=8, skipped=0))
    client._pushers["localhost"] = _pusher(PushResult(), PushResult())
    client._get_dirty_entries = MagicMock(return_value=[])
    outcomes = [PendingOutcome(payload={"session_id": f"s{i}"}, line_no=i) for i in range(30, 38)]

    with patch("trw_mcp.sync.client.load_pending_outcomes", return_value=outcomes):
        await client._run_one_cycle()

    client._coordinator.record_outcome_push_success.assert_not_called()
    assert "api.trwframework.com" in client._coordinator.record_sync_failure.call_args.args[0]
