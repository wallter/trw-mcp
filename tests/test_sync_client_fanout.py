"""Tests for fan-out sync target resolution and per-target failure isolation.

Covers:
- TRWConfig.resolved_sync_targets accessor
- BackendSyncClient fan-out across targets
- Per-target failure isolation (ConnectionError, 429, etc.)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        asyncio.get_event_loop().run_until_complete(client._run_one_cycle())
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
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 0
    client._coordinator.get_last_outcome_line.return_value = 0

    # Primary target pusher raises connection error.
    primary_pusher = MagicMock()
    primary_pusher.push_learnings.side_effect = ConnectionError("refused")
    primary_pusher.push_outcomes.side_effect = ConnectionError("refused")
    client._pusher = primary_pusher

    # Secondary target pusher succeeds.
    secondary_pusher = MagicMock()
    secondary_pusher.push_learnings.return_value = PushResult(pushed=1, failed=0, skipped=0)
    secondary_pusher.push_outcomes.return_value = PushResult(pushed=0, failed=0, skipped=0)
    client._pushers["localhost"] = secondary_pusher

    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(status_code=304, not_modified=True)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(
        return_value=[SimpleNamespace(id="L-1", sync_seq=5)],
    )
    client._mark_synced = MagicMock()

    # Must not propagate.
    await client._run_one_cycle()

    primary_pusher.push_learnings.assert_called_once()
    secondary_pusher.push_learnings.assert_called_once()
    # Any-target-succeeded path clears dirty.
    client._mark_synced.assert_called_once()
    # Success path does NOT record_sync_failure.
    client._coordinator.record_sync_failure.assert_not_called()


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

    primary = MagicMock()
    primary.push_learnings.side_effect = httpx.HTTPStatusError(
        "429", request=MagicMock(), response=MagicMock(status_code=429),
    )
    client._pusher = primary

    secondary = MagicMock()
    secondary.push_learnings.return_value = PushResult(pushed=1, failed=0, skipped=0)
    secondary.push_outcomes.return_value = PushResult(pushed=0, failed=0, skipped=0)
    client._pushers["localhost"] = secondary

    client._puller = MagicMock()
    client._puller.pull_intel_state.return_value = PullResult(status_code=304, not_modified=True)
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(
        return_value=[SimpleNamespace(id="L-1", sync_seq=5)],
    )
    client._mark_synced = MagicMock()

    await client._run_one_cycle()

    secondary.push_learnings.assert_called_once()
    client._mark_synced.assert_called_once()
