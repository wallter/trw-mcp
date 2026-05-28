"""Tests for _ttl.py — FR14 TTL staleness check + detached HEAD fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.channels._manifest_models import ChannelEntry, ChannelSurface
from trw_mcp.channels._ttl import CheckResult, check_staleness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    ttl_commits: int | None = None,
    ttl_days: int | None = None,
    tier_default: str = "T2",
) -> ChannelEntry:
    return ChannelEntry(
        id="test-ch",
        client="claude-code",
        surface=ChannelSurface.CLAUDE_MD_SEGMENT,
        telemetry_tag="test-ch",
        ttl_commits=ttl_commits,
        ttl_days=ttl_days,
        tier_default=tier_default,
    )


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# never-rendered fast path
# ---------------------------------------------------------------------------


def test_never_rendered_returns_ttl_unknown() -> None:
    result = check_staleness(
        entry=_entry(ttl_commits=10),
        last_sidecar_sha=None,
        last_render_ts=None,
    )
    assert result.ttl_unknown is True
    assert result.is_stale is False


# ---------------------------------------------------------------------------
# commit-based TTL
# ---------------------------------------------------------------------------


def test_commit_stale_when_count_exceeds_ttl() -> None:
    with patch("trw_mcp.channels._ttl._git_commits_since", return_value=11):
        result = check_staleness(
            entry=_entry(ttl_commits=10),
            last_sidecar_sha="abc123",
            last_render_ts=None,
        )
    assert result.is_stale is True
    assert result.commits_since == 11
    assert result.ttl_unknown is False


def test_commit_not_stale_when_count_within_ttl() -> None:
    with patch("trw_mcp.channels._ttl._git_commits_since", return_value=5):
        result = check_staleness(
            entry=_entry(ttl_commits=10),
            last_sidecar_sha="abc123",
            last_render_ts=None,
        )
    assert result.is_stale is False
    assert result.commits_since == 5


def test_commit_exactly_at_ttl_not_stale() -> None:
    """count == ttl_commits is NOT stale (strictly greater required)."""
    with patch("trw_mcp.channels._ttl._git_commits_since", return_value=10):
        result = check_staleness(
            entry=_entry(ttl_commits=10),
            last_sidecar_sha="abc123",
            last_render_ts=None,
        )
    assert result.is_stale is False


# ---------------------------------------------------------------------------
# Detached HEAD fallback (SYS-03 fix)
# ---------------------------------------------------------------------------


def test_detached_head_nonzero_exit_returns_ttl_unknown() -> None:
    """Non-zero exit from git → ttl_unknown=True, NOT is_stale=True."""
    mock_result = MagicMock()
    mock_result.returncode = 128
    mock_result.stdout = ""
    mock_result.stderr = "fatal: not a git repository"

    with patch("subprocess.run", return_value=mock_result):
        result = check_staleness(
            entry=_entry(ttl_commits=5),
            last_sidecar_sha="deadbeef",
            last_render_ts=None,
        )
    assert result.ttl_unknown is True
    assert result.is_stale is False


def test_detached_head_empty_stdout_returns_ttl_unknown() -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = check_staleness(
            entry=_entry(ttl_commits=5),
            last_sidecar_sha="deadbeef",
            last_render_ts=None,
        )
    assert result.ttl_unknown is True
    assert result.is_stale is False


def test_detached_head_unparseable_stdout_returns_ttl_unknown() -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "not-a-number\n"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = check_staleness(
            entry=_entry(ttl_commits=5),
            last_sidecar_sha="deadbeef",
            last_render_ts=None,
        )
    assert result.ttl_unknown is True
    assert result.is_stale is False


# ---------------------------------------------------------------------------
# days-based TTL
# ---------------------------------------------------------------------------


def test_days_stale_when_exceeds_ttl_days() -> None:
    old_ts = _iso(datetime.now(timezone.utc) - timedelta(days=15))
    result = check_staleness(
        entry=_entry(ttl_days=10),
        last_sidecar_sha="abc123",
        last_render_ts=old_ts,
    )
    assert result.is_stale is True
    assert result.days_since is not None
    assert result.days_since > 10


def test_days_not_stale_when_within_ttl() -> None:
    recent_ts = _iso(datetime.now(timezone.utc) - timedelta(days=5))
    result = check_staleness(
        entry=_entry(ttl_days=10),
        last_sidecar_sha="abc123",
        last_render_ts=recent_ts,
    )
    assert result.is_stale is False


def test_days_ttl_no_render_ts_skipped() -> None:
    """Missing last_render_ts → days TTL not evaluated → not stale."""
    result = check_staleness(
        entry=_entry(ttl_days=1),
        last_sidecar_sha="abc123",
        last_render_ts=None,
    )
    assert result.is_stale is False
    assert result.days_since is None


# ---------------------------------------------------------------------------
# No TTL config
# ---------------------------------------------------------------------------


def test_no_ttl_config_returns_not_stale() -> None:
    result = check_staleness(
        entry=_entry(),  # no ttl_commits, no ttl_days
        last_sidecar_sha="abc123",
        last_render_ts=None,
    )
    assert result.is_stale is False
    assert result.ttl_unknown is False


# ---------------------------------------------------------------------------
# Both commit and days TTL — either triggers stale
# ---------------------------------------------------------------------------


def test_stale_from_days_when_commits_ok() -> None:
    old_ts = _iso(datetime.now(timezone.utc) - timedelta(days=20))
    with patch("trw_mcp.channels._ttl._git_commits_since", return_value=2):
        result = check_staleness(
            entry=_entry(ttl_commits=10, ttl_days=7),
            last_sidecar_sha="abc123",
            last_render_ts=old_ts,
        )
    assert result.is_stale is True


def test_stale_from_commits_when_days_ok() -> None:
    recent_ts = _iso(datetime.now(timezone.utc) - timedelta(days=1))
    with patch("trw_mcp.channels._ttl._git_commits_since", return_value=15):
        result = check_staleness(
            entry=_entry(ttl_commits=10, ttl_days=30),
            last_sidecar_sha="abc123",
            last_render_ts=recent_ts,
        )
    assert result.is_stale is True
