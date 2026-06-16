"""PRD-SEC-004-FR05/FR01: the background backend SYNC push is consent-gated.

The sync/ egress path (BackendSyncClient.run_sync_loop -> SyncPusher) was the
missed sibling of publisher.py: it POSTed full learning summary+detail and
session outcomes to the backend with no consent gate. These are behavior tests
asserting ZERO off-machine POST when consent is off, using a patched async
httpx transport (not mock-called assertions).

Two independent flags:
  - learning_sharing_enabled gates learning CONTENT (/v1/sync/learnings)
  - platform_telemetry_enabled gates session-outcome metrics (/v1/sync/outcomes)
Both default False (fail-closed for egress).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests._test_sync_client_support import _acquired_lock, _make_config


def _make_entry(entry_id: str, sync_seq: int = 1) -> Any:
    """Minimal MemoryEntry-like double with the fields the pusher reads."""
    entry = MagicMock()
    entry.sync_seq = sync_seq
    entry.id = entry_id
    entry.to_dict.return_value = {
        "id": entry_id,
        "summary": "PRIVATE learning summary that must not egress",
        "detail": "PRIVATE detail body",
        "impact": 0.9,
        "tags": ["secret"],
        "type": "pattern",
        "status": "active",
        "sync_hash": "a" * 64,
        "metadata": {},
        "vector_clock": {},
    }
    return entry


# ---------------------------------------------------------------------------
# SyncPusher defensive (egress-boundary) gate
# ---------------------------------------------------------------------------


class _TransportSpy:
    """Captures every httpx.AsyncClient.post and asserts none were learning POSTs."""

    def __init__(self) -> None:
        self.posts: list[str] = []

    def client_factory(self) -> MagicMock:
        spy = self

        class _Client:
            async def __aenter__(self_inner) -> Any:
                return self_inner

            async def __aexit__(self_inner, *exc: object) -> None:
                return None

            async def post(self_inner, url: str, **kwargs: object) -> Any:
                spy.posts.append(url)
                resp = MagicMock()
                resp.raise_for_status.return_value = None
                resp.json.return_value = {"inserted": 1, "updated": 0, "skipped": 0}
                return resp

        return MagicMock(side_effect=lambda *a, **k: _Client())


async def test_push_learnings_zero_post_when_sharing_disabled() -> None:
    """learning_sharing_enabled=False => push_learnings performs ZERO POST."""
    from trw_mcp.sync.push import SyncPusher

    spy = _TransportSpy()
    pusher = SyncPusher(
        backend_url="http://backend.test",
        api_key="k",
        client_id="sync-test",
        learning_sharing_enabled=False,
    )
    with patch("httpx.AsyncClient", spy.client_factory()):
        result = await pusher.push_learnings([_make_entry("L-1"), _make_entry("L-2")])

    assert spy.posts == [], "learning content must not egress when sharing disabled"
    assert result.pushed == 0
    assert result.failed == 0


async def test_push_learnings_default_is_fail_closed() -> None:
    """A pusher built WITHOUT explicit consent never transmits learning content."""
    from trw_mcp.sync.push import SyncPusher

    spy = _TransportSpy()
    pusher = SyncPusher(backend_url="http://backend.test", api_key="k", client_id="sync-test")
    with patch("httpx.AsyncClient", spy.client_factory()):
        result = await pusher.push_learnings([_make_entry("L-1")])

    assert spy.posts == []
    assert result.pushed == 0


async def test_push_outcomes_zero_post_when_telemetry_disabled() -> None:
    """platform_telemetry_enabled=False => push_outcomes performs ZERO POST."""
    from trw_mcp.sync.push import SyncPusher

    spy = _TransportSpy()
    pusher = SyncPusher(
        backend_url="http://backend.test",
        api_key="k",
        client_id="sync-test",
        platform_telemetry_enabled=False,
    )
    with patch("httpx.AsyncClient", spy.client_factory()):
        result = await pusher.push_outcomes([{"session_id": "s1", "learning_ids": ["L-1"]}])

    assert spy.posts == []
    assert result.pushed == 0


async def test_push_learnings_posts_when_sharing_enabled() -> None:
    """Positive control: learning content DOES egress when sharing is consented."""
    from trw_mcp.sync.push import SyncPusher

    spy = _TransportSpy()
    pusher = SyncPusher(
        backend_url="http://backend.test",
        api_key="k",
        client_id="sync-test",
        learning_sharing_enabled=True,
    )
    with patch("httpx.AsyncClient", spy.client_factory()):
        result = await pusher.push_learnings([_make_entry("L-1")])

    assert spy.posts == ["http://backend.test/v1/sync/learnings"]
    assert result.pushed == 1


# ---------------------------------------------------------------------------
# BackendSyncClient cycle-level gate (defaults: both flags OFF => no egress)
# ---------------------------------------------------------------------------


def _default_consent_config(**overrides: object) -> SimpleNamespace:
    """A config with BOTH consent flags OFF (the privacy-forward default)."""
    cfg = _make_config(**overrides)
    cfg.learning_sharing_enabled = False
    cfg.platform_telemetry_enabled = False
    return cfg


@pytest.mark.asyncio
async def test_cycle_does_not_load_dirty_when_sharing_disabled(tmp_path) -> None:
    """When sharing is off, the cycle never even loads dirty entries (no content
    enters the push pipeline) but pull/intel still runs."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="c1"):
        client = BackendSyncClient(_default_consent_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 0
    client._coordinator.get_last_company_pull_seq.return_value = 0
    client._pusher = MagicMock()
    client._pusher.push_learnings = AsyncMock()
    client._pusher.push_outcomes = AsyncMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(
        return_value=PullResult(state={}, etag="e", team_learnings=[], sync_hints={}, status_code=200)
    )
    client._puller.merge_team_learnings.return_value = 0
    client._cache = MagicMock()
    # If the gate failed, this MagicMock would be called and return a MagicMock
    # (truthy) — the assertion below proves it was NOT consulted.
    client._get_dirty_entries = MagicMock(return_value=[_make_entry("L-1")])

    await client._run_one_cycle()

    client._get_dirty_entries.assert_not_called()
    client._pusher.push_learnings.assert_not_called()
    client._pusher.push_outcomes.assert_not_called()
    # Pull path is unaffected by the content-egress gate.
    client._puller.pull_intel_state.assert_called_once()


@pytest.mark.asyncio
async def test_cycle_does_not_load_outcomes_when_telemetry_disabled(tmp_path) -> None:
    """When telemetry is off, the pending-outcome queue is never loaded."""
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="c1"):
        client = BackendSyncClient(_default_consent_config(), tmp_path)
    client._coordinator = MagicMock()
    client._coordinator.should_sync.return_value = True
    client._coordinator.acquire_sync_lock.return_value = _acquired_lock()
    client._coordinator.get_last_pull_seq.return_value = 0
    client._coordinator.get_last_company_pull_seq.return_value = 0
    client._pusher = MagicMock()
    client._pusher.push_outcomes = AsyncMock()
    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(
        return_value=PullResult(state={}, etag="e", team_learnings=[], sync_hints={}, status_code=200)
    )
    client._puller.merge_team_learnings.return_value = 0
    client._cache = MagicMock()
    client._get_dirty_entries = MagicMock(return_value=[])

    with patch("trw_mcp.sync.client.load_pending_outcomes") as load_mock:
        await client._run_one_cycle()

    load_mock.assert_not_called()
    client._pusher.push_outcomes.assert_not_called()


def test_client_resolves_consent_flags_fail_closed_when_absent(tmp_path) -> None:
    """A config object missing the flags resolves them fail-closed (False)."""
    from trw_mcp.sync.client import BackendSyncClient

    cfg = _make_config()
    # Simulate a legacy/stub config that never defined the consent flags.
    del cfg.learning_sharing_enabled
    del cfg.platform_telemetry_enabled
    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="c1"):
        client = BackendSyncClient(cfg, tmp_path)

    assert client._learning_sharing_enabled is False
    assert client._platform_telemetry_enabled is False


def test_client_still_constructs_when_consent_off(tmp_path) -> None:
    """Regression guard (L-wSK5): consent gating must NOT break sync-client
    construction / credential resolution — only CONTENT push is gated. The
    pushers and puller are still built so the pull/intel path keeps working."""
    from trw_mcp.sync.client import BackendSyncClient

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="c1"):
        client = BackendSyncClient(_default_consent_config(), tmp_path)

    assert client._targets, "sync targets must still resolve when consent is off"
    assert client._pusher is not None
    assert client._puller is not None
