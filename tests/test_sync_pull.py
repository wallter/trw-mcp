"""Tests for SyncPuller — PRD-INFRA-053."""

from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock, patch


def test_pull_result_model() -> None:
    """PullResult is a valid Pydantic model with expected defaults."""
    from trw_mcp.sync.pull import PullResult

    result = PullResult()
    assert result.state is None
    assert result.etag is None
    assert result.sync_hints is None
    assert result.team_learnings is None
    assert result.status_code == 0

    result2 = PullResult(
        state={"bandit_params": {"L-1": 1.2}},
        etag="abc123",
        sync_hints={"next_poll_recommended_at": "2026-04-06T12:00:00Z"},
        team_learnings=[{"id": "L-99", "summary": "team tip"}],
        status_code=200,
    )
    assert result2.state is not None
    assert result2.etag == "abc123"
    assert result2.status_code == 200
    assert len(result2.team_learnings) == 1  # type: ignore[arg-type]


def test_pull_empty_returns_none() -> None:
    """Pull from unreachable URL returns None (fail-open)."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://unreachable.invalid:9999",
        api_key="test-key",
        timeout=1.0,
        client_id="sync-test",
    )
    result = puller.pull_intel_state()
    assert result is None


def test_puller_never_raises() -> None:
    """SyncPuller never raises exceptions on any error path."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://unreachable.invalid:9999",
        api_key="test-key",
        timeout=0.5,
        client_id="sync-test",
    )
    assert puller.pull_intel_state(etag="stale-etag") is None
    assert puller.pull_intel_state(since_seq=999) is None
    assert puller.pull_intel_state(model_family="opus", trw_version="0.38.2") is None


def test_puller_constructor_strips_trailing_slash() -> None:
    """Backend URL has trailing slash stripped for clean path joining."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com/api/",
        api_key="test-key",
        client_id="sync-test",
    )
    assert puller._backend_url == "http://example.com/api"


def test_puller_default_timeout() -> None:
    """Default timeout is 5.0 seconds."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-test")
    assert puller._timeout == 5.0


def test_puller_warns_on_insecure_non_local_http_url() -> None:
    """Non-local plain HTTP backends emit an advisory security warning once."""
    from trw_mcp.sync.pull import SyncPuller

    with patch("trw_mcp.sync.pull.logger.warning") as mock_warning:
        SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-test")

    mock_warning.assert_called_once_with("sync_pull_insecure_url", url="http://example.com")


def test_pull_sends_client_id_and_logs_structured_events() -> None:
    """Pull includes client_id/query params and emits start/complete events."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
    )

    with (
        patch("httpx.Client") as mock_client_cls,
        patch("trw_mcp.sync.pull.logger.info") as mock_info,
    ):
        mock_client = mock_client_cls.return_value.__enter__.return_value
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "etag": "etag-1",
            "sync_hints": {"polling_cap_seconds": 60},
            "team_learnings": [{"source_learning_id": "remote-1"}],
        }
        mock_client.get.return_value = response

        result = puller.pull_intel_state(
            etag="cached-etag",
            since_seq=7,
            model_family="opus",
            trw_version="v1",
        )

    assert result is not None
    _, kwargs = mock_client.get.call_args
    assert kwargs["headers"]["If-None-Match"] == '"cached-etag"'
    assert kwargs["params"] == {
        "since_seq": 7,
        "client_id": "sync-client-1",
        "model_family": "opus",
        "trw_version": "v1",
    }
    assert mock_info.call_args_list[0].kwargs["event_type"] == "sync_pull_start"
    assert mock_info.call_args_list[1].kwargs["event_type"] == "sync_pull_complete"
    assert mock_info.call_args_list[1].kwargs["team_learnings_count"] == 1


def test_pull_boundary_failure_logs_structured_warning() -> None:
    """Boundary failures log structured warning + traceback and still fail open."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-client-1")

    with (
        patch("httpx.Client") as mock_client_cls,
        patch("trw_mcp.sync.pull.logger.warning") as mock_warning,
    ):
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.get.side_effect = RuntimeError("boom")

        assert puller.pull_intel_state(since_seq=3) is None

    args, kwargs = mock_warning.call_args
    assert args == ("sync_pull_error",)
    assert kwargs["event_type"] == "sync_pull_error"
    assert kwargs["error_type"] == "RuntimeError"
    assert kwargs["since_seq"] == 3
    assert kwargs["client_id"] == "sync-client-1"
    assert kwargs["exc_info"] is True


def test_pull_not_modified_returns_distinct_result() -> None:
    """304 responses are distinguishable from transport failures."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-client-1")

    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        response = MagicMock()
        response.status_code = 304
        mock_client.get.return_value = response

        result = puller.pull_intel_state(etag="etag-1", since_seq=7)

    assert result is not None
    assert result.not_modified is True
    assert result.status_code == 304


def test_sync_modules_follow_structlog_conventions() -> None:
    """Sync modules keep the established structlog naming/keyword conventions."""
    import trw_mcp.sync.cache as cache_module
    import trw_mcp.sync.client as client_module
    import trw_mcp.sync.pull as pull_module

    for module in (cache_module, client_module, pull_module):
        source = inspect.getsource(module)
        assert "structlog.get_logger(__name__)" in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "logger":
                continue
            if node.func.attr not in {"debug", "info", "warning", "exception"}:
                continue
            assert all(keyword.arg != "event" for keyword in node.keywords)
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                event_name = node.args[0].value
                assert event_name == event_name.lower()
                assert "-" not in event_name


def test_merge_team_learnings_inserts_team_sync_entries(tmp_path) -> None:
    """Pulled team learnings are inserted locally with attribution metadata."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend
    from trw_memory.sync.delta import DeltaTracker

    from trw_mcp.sync.pull import SyncPuller

    backend = SQLiteBackend(tmp_path / "memory.db", dim=8)
    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
        trw_dir=tmp_path,
    )

    with patch("trw_mcp.state._memory_connection.get_backend", return_value=backend):
        merged = puller.merge_team_learnings(
            [
                {
                    "source_learning_id": "remote-1",
                    "summary": "shared tip",
                    "detail": "backend detail",
                    "impact": 0.8,
                    "tags": ["sync"],
                    "type": "pattern",
                    "status": "active",
                    "sync_seq": 7,
                    "vector_clock": {"remote-a": 2},
                    "metadata": {"origin": "backend"},
                }
            ]
        )

    assert merged == 1
    stored = backend.get("team-sync-remote-1")
    assert stored is not None
    assert stored.source == "team_sync"
    assert stored.remote_id == "remote-1"
    assert stored.metadata["origin"] == "backend"
    assert stored.metadata["team_sync_pull_seq"] == "7"
    assert stored.vector_clock == {"remote-a": 2}
    assert DeltaTracker.get_dirty_entries(backend) == []


def test_merge_team_learnings_resolves_conflicts(tmp_path) -> None:
    """Existing pulled entries are merged with vector-clock conflict resolution."""
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend
    from trw_memory.sync.delta import DeltaTracker

    from trw_mcp.sync.pull import SyncPuller

    backend = SQLiteBackend(tmp_path / "memory.db", dim=8)
    backend.store(
        MemoryEntry(
            id="team-sync-remote-1",
            remote_id="remote-1",
            content="shared tip",
            detail="local detail",
            tags=["local"],
            importance=0.4,
            vector_clock={"local-a": 1},
            source="team_sync",
            metadata={"team_sync_pull_seq": "5"},
        )
    )
    DeltaTracker.mark_synced(["team-sync-remote-1"], backend)

    puller = SyncPuller(
        backend_url="http://example.com",
        api_key="key",
        client_id="sync-client-1",
        trw_dir=tmp_path,
    )

    with patch("trw_mcp.state._memory_connection.get_backend", return_value=backend):
        merged = puller.merge_team_learnings(
            [
                {
                    "source_learning_id": "remote-1",
                    "summary": "shared tip",
                    "detail": "remote detail",
                    "impact": 0.9,
                    "tags": ["remote"],
                    "type": "pattern",
                    "status": "active",
                    "sync_seq": 9,
                    "vector_clock": {"remote-b": 1},
                    "metadata": {"origin": "backend"},
                }
            ]
        )

    assert merged == 1
    stored = backend.get("team-sync-remote-1")
    assert stored is not None
    assert stored.source == "team_sync"
    assert "local detail" in stored.detail
    assert "remote detail" in stored.detail
    assert stored.tags == ["local", "remote"]
    assert stored.importance == 0.9
    assert stored.metadata["team_sync_pull_seq"] == "9"
    assert DeltaTracker.get_dirty_entries(backend) == []
