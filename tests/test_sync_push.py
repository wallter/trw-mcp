"""Tests for SyncPusher — PRD-INFRA-051-FR09."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_mock_entry(
    entry_id: str = "L-001",
    sync_hash: str = "abc123",
    sync_seq: int = 1,
    summary: str = "test",
) -> MagicMock:
    """Create a mock MemoryEntry for push testing."""
    entry = MagicMock()
    entry.id = entry_id
    entry.sync_hash = sync_hash
    entry.sync_seq = sync_seq
    entry.to_dict.return_value = {
        "id": entry_id,
        "sync_hash": sync_hash,
        "sync_seq": sync_seq,
        "content": summary,
        "summary": summary,
        "detail": None,
        "importance": 0.5,
        "tags": ["test"],
        "type": "pattern",
        "status": "active",
    }
    return entry


def test_push_empty_returns_zero() -> None:
    """Push with no entries returns zero counts."""
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(backend_url="http://localhost:5002", api_key="test", client_id="sync-test")
    result = pusher.push_learnings([])
    assert result.pushed == 0
    assert result.failed == 0
    assert result.skipped == 0


def test_push_unreachable_returns_failed() -> None:
    """Push to unreachable URL returns failed count, never raises."""
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(
        backend_url="http://unreachable.invalid:9999",
        api_key="test",
        timeout=1.0,
        client_id="sync-test",
    )
    entries = [_make_mock_entry(f"L-{i}") for i in range(3)]
    result = pusher.push_learnings(entries)
    assert result.failed == 3
    assert result.pushed == 0


def test_push_outcomes_empty_returns_zero() -> None:
    """Push outcomes with no entries returns zero counts."""
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(backend_url="http://localhost:5002", api_key="test", client_id="sync-test")
    result = pusher.push_outcomes([])
    assert result.pushed == 0


def test_serialize_entry_format() -> None:
    """Serialized entry has required fields for backend API."""
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(backend_url="http://localhost:5002", api_key="test", client_id="sync-test")
    entry = _make_mock_entry("L-test", sync_hash="hash123", summary="test discovery")
    serialized = pusher._serialize_entry(entry)

    assert serialized["source_learning_id"] == "L-test"
    assert serialized["sync_hash"] == "hash123"
    assert "summary" in serialized
    assert "tags" in serialized


def test_push_result_model() -> None:
    """PushResult is a valid Pydantic model."""
    from trw_mcp.sync.push import PushResult

    result = PushResult(pushed=5, failed=1, skipped=2)
    assert result.pushed == 5
    assert result.failed == 1
    assert result.skipped == 2


def test_push_outcomes_unreachable_returns_failed() -> None:
    """Push outcomes to unreachable URL returns failed count."""
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(
        backend_url="http://unreachable.invalid:9999",
        api_key="test",
        timeout=1.0,
        client_id="sync-test",
    )
    result = pusher.push_outcomes([{"session_id": "s1", "learning_ids": ["L-1"]}])
    assert result.failed > 0


def test_push_batch_boundary_failure_logs_warning_with_traceback() -> None:
    """Learning push boundary failures log warning + traceback and stay fail-open."""
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(backend_url="http://example.com", api_key="key", client_id="sync-test")
    entries = [_make_mock_entry("L-1"), _make_mock_entry("L-2")]

    with (
        patch("httpx.Client") as mock_client_cls,
        patch("trw_mcp.sync.push.logger.warning") as mock_warning,
    ):
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.side_effect = RuntimeError("boom")

        result = pusher.push_learnings(entries)

    assert result.failed == 2
    mock_warning.assert_called_once_with(
        "sync_push_failed",
        batch_index=0,
        count=2,
        exc_info=True,
    )


def test_push_outcomes_boundary_failure_logs_warning_with_traceback() -> None:
    """Outcome push boundary failures log warning + traceback and stay fail-open."""
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(backend_url="http://example.com", api_key="key", client_id="sync-test")
    outcomes = [{"session_id": "s1", "learning_ids": ["L-1"]}]

    with (
        patch("httpx.Client") as mock_client_cls,
        patch("trw_mcp.sync.push.logger.warning") as mock_warning,
    ):
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.side_effect = RuntimeError("boom")

        result = pusher.push_outcomes(outcomes)

    assert result.failed == 1
    mock_warning.assert_called_once_with(
        "sync_push_outcomes_failed",
        count=1,
        exc_info=True,
    )


def test_push_uses_stable_configured_client_id() -> None:
    """Push payload uses the shared stable sync client id."""
    from trw_mcp.sync.push import SyncPusher

    entry = _make_mock_entry("L-1")
    pusher = SyncPusher(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-claude-code-inst-123",
    )

    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        response = MagicMock()
        response.json.return_value = {"inserted": 1, "updated": 0, "skipped": 0}
        response.raise_for_status.return_value = None
        mock_client.post.return_value = response

        pusher.push_learnings([entry])

    _, kwargs = mock_client.post.call_args
    assert kwargs["json"]["client_id"] == "sync-claude-code-inst-123"
