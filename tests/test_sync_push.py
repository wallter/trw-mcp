"""Tests for SyncPusher — PRD-INFRA-051-FR09."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


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
        "vector_clock": {"sync-test": sync_seq},
        "metadata": {"source": "unit-test", "installation_id": "install-123"},
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
    assert serialized["vector_clock"] == {"sync-test": 1}
    assert serialized["metadata"]["source"] == "unit-test"
    assert serialized["metadata"]["installation_id"] != "install-123"


def test_serialize_entry_anonymizes_summary_and_detail() -> None:
    """Entry serialization strips PII and local paths before upload."""
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(backend_url="http://localhost:5002", api_key="test", client_id="sync-test")
    pusher._project_root = "/tmp/project"
    entry = _make_mock_entry("L-private", sync_hash="hash123")
    entry.to_dict.return_value["summary"] = "Email me at support@example.com"
    entry.to_dict.return_value["detail"] = "See /tmp/project/secret.txt for token sk_abcdefghijklmnopqrstuvwxyz1234"

    serialized = pusher._serialize_entry(entry)

    assert "support@example.com" not in serialized["summary"]
    assert "<email>" in serialized["summary"]
    assert "/tmp/project/secret.txt" not in serialized["detail"]
    assert "<project>" in serialized["detail"]
    assert "sk_abcdefghijklmnopqrstuvwxyz1234" not in serialized["detail"]


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
    args, kwargs = mock_warning.call_args
    assert args == ("sync_push_error",)
    assert kwargs["event_type"] == "sync_push_error"
    assert kwargs["client_id"] == "sync-test"
    assert kwargs["count"] == 2
    assert kwargs["exc_info"] is True


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
    args, kwargs = mock_warning.call_args
    assert args == ("sync_push_outcomes_error",)
    assert kwargs["event_type"] == "sync_push_outcomes_error"
    assert kwargs["client_id"] == "sync-test"
    assert kwargs["count"] == 1
    assert kwargs["exc_info"] is True


def test_push_outcomes_batches_requests() -> None:
    """Outcome pushes respect configured batch size."""
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(
        backend_url="http://example.com",
        api_key="key",
        batch_size=2,
        client_id="sync-test",
    )
    outcomes = [
        {"session_id": "s1", "learning_ids": ["L-1"]},
        {"session_id": "s2", "learning_ids": ["L-2"]},
        {"session_id": "s3", "learning_ids": ["L-3"]},
    ]

    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        response1 = MagicMock()
        response1.json.return_value = {"inserted": 2}
        response1.raise_for_status.return_value = None
        response2 = MagicMock()
        response2.json.return_value = {"inserted": 1}
        response2.raise_for_status.return_value = None
        mock_client.post.side_effect = [response1, response2]

        result = pusher.push_outcomes(outcomes)

    assert result.pushed == 3
    assert result.failed == 0
    assert mock_client.post.call_count == 2
    first_payload = mock_client.post.call_args_list[0].kwargs["json"]
    second_payload = mock_client.post.call_args_list[1].kwargs["json"]
    assert len(first_payload["outcomes"]) == 2
    assert len(second_payload["outcomes"]) == 1


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


def test_push_treats_backend_reported_errors_as_batch_failure() -> None:
    """Server-reported ingest errors keep the whole batch dirty for retry."""
    from trw_mcp.sync.push import SyncPusher

    entries = [_make_mock_entry("L-1"), _make_mock_entry("L-2")]
    pusher = SyncPusher(backend_url="http://example.com", api_key="key", client_id="sync-client-1")

    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        response = MagicMock()
        response.json.return_value = {"inserted": 1, "updated": 0, "skipped": 0, "errors": 1}
        response.raise_for_status.return_value = None
        mock_client.post.return_value = response

        result = pusher.push_learnings(entries)

    assert result.pushed == 0
    assert result.skipped == 0
    assert result.failed == 2


def test_push_logs_structured_start_and_complete_events() -> None:
    """Successful pushes emit client-aware start/complete telemetry."""
    from trw_mcp.sync.push import SyncPusher

    entry = _make_mock_entry("L-1")
    pusher = SyncPusher(backend_url="http://example.com", api_key="key", client_id="sync-client-1")

    with (
        patch("httpx.Client") as mock_client_cls,
        patch("trw_mcp.sync.push.logger.info") as mock_info,
    ):
        mock_client = mock_client_cls.return_value.__enter__.return_value
        response = MagicMock()
        response.json.return_value = {"inserted": 1, "updated": 0, "skipped": 0}
        response.raise_for_status.return_value = None
        mock_client.post.return_value = response

        result = pusher.push_learnings([entry])

    assert result.pushed == 1
    assert mock_info.call_args_list[0].args == ("sync_push_start",)
    assert mock_info.call_args_list[0].kwargs["event_type"] == "sync_push_start"
    assert mock_info.call_args_list[-1].args == ("sync_push_complete",)
    assert mock_info.call_args_list[-1].kwargs["event_type"] == "sync_push_complete"
    assert mock_info.call_args_list[-1].kwargs["client_id"] == "sync-client-1"


def test_resolve_sync_client_id_anonymizes_installation_id() -> None:
    """Derived sync client ids do not expose the raw installation id."""
    from trw_mcp.sync.identity import resolve_sync_client_id

    fake_cfg = MagicMock()
    fake_cfg.client_profile.client_id = "claude-code"

    with (
        patch("trw_mcp.sync.identity.get_config", return_value=fake_cfg),
        patch("trw_mcp.sync.identity.resolve_installation_id", return_value="install-123"),
    ):
        client_id = resolve_sync_client_id()

    assert client_id.startswith("sync-claude-code-")
    assert "install-123" not in client_id
