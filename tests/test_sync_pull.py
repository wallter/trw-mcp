"""Tests for SyncPuller — PRD-INFRA-053."""

from __future__ import annotations

import pytest


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
    )
    # Various argument combinations should all return None without raising
    assert puller.pull_intel_state(etag="stale-etag") is None
    assert puller.pull_intel_state(since_seq=999) is None
    assert puller.pull_intel_state(model_family="opus", trw_version="0.38.2") is None


def test_puller_constructor_strips_trailing_slash() -> None:
    """Backend URL has trailing slash stripped for clean path joining."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://example.com/api/",
        api_key="test-key",
    )
    assert puller._backend_url == "http://example.com/api"


def test_puller_default_timeout() -> None:
    """Default timeout is 5.0 seconds."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key")
    assert puller._timeout == 5.0
