"""INC-147 cycle side: a rejected learning is held back with its reason, the rest are acknowledged.

The pusher returns the entries the backend refused (``PushResult.rejected``).
The cycle marks every other accepted entry synced, records the refused ones in
``sync-state.json`` with the server's reason, and leaves them out of later
pushes until they are edited or the operator asks for a retry, so one bad
learning no longer pins the whole project's push.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from tests._test_sync_client_support import _make_config
from trw_mcp.sync._team_merge_result import TeamMergeResult
from trw_mcp.sync.coordinator import SyncCoordinator


def _client(tmp_path):  # type: ignore[no-untyped-def]
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.pull import PullResult

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(platform_telemetry_enabled=False), tmp_path)
    client._puller = MagicMock()
    client._puller.pull_intel_state = AsyncMock(return_value=PullResult(status_code=304, not_modified=True))
    client._puller.merge_team_learnings.return_value = TeamMergeResult()
    client._cache = MagicMock()
    client._mark_synced = MagicMock()
    return client


def _primary_result(**learnings: object):  # type: ignore[no-untyped-def]
    from trw_mcp.sync._client_push import TargetPushOutcome
    from trw_mcp.sync.push import PushResult

    result = TargetPushOutcome(learnings=PushResult(**learnings))
    report = {"example.com": {"pushed": result.pushed, "skipped": 0, "failed": result.failed, "error": None}}
    report["example.com"]["status"] = "success" if result.failed == 0 else "partial_error"
    return report, result


def test_the_coordinator_keeps_rejections_with_their_reason(tmp_path) -> None:
    coordinator = SyncCoordinator(trw_dir=tmp_path)
    coordinator.update_rejected(add={"L-1": (4, "HTTP 422: type: bad"), "L-2": (7, "HTTP 422: x")})
    coordinator.update_rejected(drop=["L-2"])

    held = coordinator.rejected_entries()
    assert list(held) == ["L-1"]
    assert held["L-1"]["sync_seq"] == 4
    assert held["L-1"]["reason"] == "HTTP 422: type: bad"
    assert coordinator.clear_rejected() == 1
    assert coordinator.rejected_entries() == {}


@pytest.mark.asyncio
async def test_a_rejected_entry_is_held_and_the_rest_are_marked_synced(tmp_path) -> None:
    client = _client(tmp_path)
    dirty = [SimpleNamespace(id=f"L-{i}", sync_seq=i + 1, namespace="default", tags=[], metadata={}) for i in range(3)]
    client._get_dirty_entries = MagicMock(return_value=dirty)
    client._fanout_push = AsyncMock(
        return_value=_primary_result(pushed=2, rejected={"L-1": "HTTP 422: type: Input should be 'pattern'"})
    )

    await client._run_one_cycle(force=True)

    client._mark_synced.assert_called_once_with([dirty[0], dirty[2]])
    held = SyncCoordinator(trw_dir=tmp_path).rejected_entries()
    assert held == {
        "L-1": {"sync_seq": 2, "reason": "HTTP 422: type: Input should be 'pattern'", "at": held["L-1"]["at"]}
    }
    assert SyncCoordinator(trw_dir=tmp_path).get_consecutive_failures() == 0


@pytest.mark.asyncio
async def test_an_accepted_entry_leaves_the_rejected_ledger(tmp_path) -> None:
    client = _client(tmp_path)
    SyncCoordinator(trw_dir=tmp_path).update_rejected(add={"L-0": (1, "HTTP 422: old")})
    dirty = [
        SimpleNamespace(id="L-0", sync_seq=2, namespace="default", tags=[], metadata={})
    ]  # edited since it was refused
    client._get_dirty_entries = MagicMock(return_value=dirty)
    client._fanout_push = AsyncMock(return_value=_primary_result(pushed=1))

    await client._run_one_cycle(force=True)

    assert SyncCoordinator(trw_dir=tmp_path).rejected_entries() == {}


def test_a_held_entry_is_left_out_of_the_dirty_page_until_it_is_edited(
    fake_memory_store: FakeMemoryStore, tmp_path
) -> None:
    from trw_mcp.sync.client import BackendSyncClient

    fake_memory_store.put("refused", FAKE_NAMESPACE, {"entry_id": "L-bad"})
    fake_memory_store.put("fine", FAKE_NAMESPACE, {"entry_id": "L-ok"})
    bad = fake_memory_store.rows[(FAKE_NAMESPACE, "L-bad")]
    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        client = BackendSyncClient(_make_config(), tmp_path)
    client._coordinator.update_rejected(add={"L-bad": (bad.sync_seq, "HTTP 422: bad")})

    assert [entry.id for entry in client._get_dirty_entries()] == ["L-ok"]

    fake_memory_store.put("refused, then fixed", FAKE_NAMESPACE, {"entry_id": "L-bad"})
    assert sorted(entry.id for entry in client._get_dirty_entries()) == ["L-bad", "L-ok"]


@pytest.mark.asyncio
async def test_a_failed_push_records_the_servers_reason(tmp_path) -> None:
    client = _client(tmp_path)
    client._get_dirty_entries = MagicMock(
        return_value=[SimpleNamespace(id="L-0", sync_seq=1, namespace="default", tags=[], metadata={})]
    )
    report = {
        "example.com": {
            "pushed": 0,
            "skipped": 0,
            "failed": 1,
            "error": "HTTP 503: maintenance window",
            "status": "partial_error",
        }
    }
    from trw_mcp.sync._client_push import TargetPushOutcome

    client._fanout_push = AsyncMock(return_value=(report, TargetPushOutcome()))

    await client._run_one_cycle(force=True)

    coordinator = SyncCoordinator(trw_dir=tmp_path)
    assert coordinator.get_consecutive_failures() == 1
    assert "HTTP 503: maintenance window" in str(coordinator._read_state()["last_error"])


@pytest.mark.asyncio
async def test_fanout_carries_the_primary_error_into_the_report() -> None:
    from trw_mcp.sync._client_push import fanout_push
    from trw_mcp.sync.push import PushResult

    pusher = MagicMock()
    pusher.push_learnings = AsyncMock(return_value=PushResult(failed=1, last_error="HTTP 503: down"))
    target = SimpleNamespace(url="http://example.com", api_key="k", label="example.com")
    report, _ = await fanout_push(
        client_id="c",
        targets=[target],
        primary_pusher=pusher,
        pusher_map={"example.com": pusher},
        batch_size=10,
        timeout=1.0,
        dirty=[SimpleNamespace(id="L-0", sync_seq=1, namespace="default", tags=[], metadata={})],  # type: ignore[list-item]
        outcomes=[],
    )
    assert report["example.com"]["error"] == "HTTP 503: down"


def test_the_widened_dirty_page_never_asks_past_the_daemon_page_limit(
    fake_memory_store: FakeMemoryStore, tmp_path
) -> None:
    """Codex r1: the daemon refuses a dirty page over its limit, which would read as 'nothing pending'."""
    from trw_mcp.sync._client_runtime import MAX_DIRTY_PAGE_REQUEST, get_dirty_entries

    held = {f"L-{i}": {"sync_seq": 1, "reason": "x"} for i in range(900)}
    get_dirty_entries(client_id="c", trw_dir=tmp_path, held=held)
    get_dirty_entries(client_id="c", trw_dir=tmp_path, page_size=10_000)

    limits = [args[1] for name, args in fake_memory_store.calls if name == "page_dirty"]
    assert limits
    assert max(limits) <= MAX_DIRTY_PAGE_REQUEST == 1000
