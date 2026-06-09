"""Tests for the trw_channel_stats MCP tool.

Uses synthetic JSONL fixtures; no live MCP, no network.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trw_mcp.tools.channel_stats import compute_channel_stats_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _push(
    channel_id: str = "ch-01",
    client: str = "claude-code",
    session_id: str = "s1",
    file_path: str = "CLAUDE.md",
    ts: str = "2026-05-28T10:00:00.000Z",
) -> dict:
    return {
        "schema_version": "channel-event/v1",
        "channel_id": channel_id,
        "client": client,
        "event_type": "push_write",
        "ts": ts,
        "session_id": session_id,
        "file_path": file_path,
    }


def _outcome(
    channel_id: str = "ch-01",
    client: str = "claude-code",
    session_id: str = "s1",
    file_path: str = "CLAUDE.md",
    ts: str = "2026-05-28T10:00:10.000Z",
) -> dict:
    return {
        "schema_version": "channel-event/v1",
        "channel_id": channel_id,
        "client": client,
        "event_type": "edit_correlated",
        "ts": ts,
        "session_id": session_id,
        "file_path": file_path,
    }


# ---------------------------------------------------------------------------
# compute_channel_stats_result — basic contract
# ---------------------------------------------------------------------------


def test_tool_returns_dict(tmp_path: Path) -> None:
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    assert isinstance(result, dict)


def test_tool_never_raises_on_missing_log(tmp_path: Path) -> None:
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    # Should not raise; returns status ok (empty) or error
    assert "status" in result


def test_tool_status_ok_on_valid_log(tmp_path: Path) -> None:
    log_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    _write_events(log_path, [_push(), _outcome()])
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    assert result["status"] == "ok"


def test_tool_channels_is_list(tmp_path: Path) -> None:
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    assert isinstance(result.get("channels"), list)


def test_tool_total_events_is_int(tmp_path: Path) -> None:
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    assert isinstance(result.get("total_events"), int)


def test_tool_window_seconds_reflects_hours(tmp_path: Path) -> None:
    result = compute_channel_stats_result(window_hours=2, repo_root=str(tmp_path))
    assert result.get("window_seconds") == 7200


def test_tool_with_correlated_events(tmp_path: Path) -> None:
    log_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    _write_events(log_path, [_push(), _outcome()])
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    assert result["total_events"] == 2
    assert len(result["channels"]) == 1
    ch = result["channels"][0]
    assert ch["correlated"] == 1
    assert ch["raw_rate"] == pytest.approx(1.0)


def test_tool_empty_log_returns_empty_channels(tmp_path: Path) -> None:
    log_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("")
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    assert result["channels"] == []
    assert result["total_events"] == 0


def test_tool_channel_entry_has_expected_keys(tmp_path: Path) -> None:
    log_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    _write_events(log_path, [_push(), _outcome()])
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    ch = result["channels"][0]
    expected_keys = {
        "channel_id",
        "client",
        "total_pushes",
        "correlated",
        "raw_rate",
        "adjusted_rate",
        "n_events",
        "tier_current",
        "throttle_status",
    }
    assert expected_keys.issubset(ch.keys())


def test_tool_error_path_returns_valid_dict() -> None:
    """repo_root=None with no git should return an error dict, not raise."""
    # Patch TRW_REPO_ROOT so auto-detect doesn't accidentally find a real repo
    env_backup = os.environ.pop("TRW_REPO_ROOT", None)
    try:
        result = compute_channel_stats_result(window_hours=1, repo_root="/nonexistent/path/xyz")
        # Should return a dict with status field — either ok (empty) or error
        assert isinstance(result, dict)
        assert "status" in result
    finally:
        if env_backup is not None:
            os.environ["TRW_REPO_ROOT"] = env_backup


def test_tool_never_raises_on_bad_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("not-json\nalso not json\n{broken\n")
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    assert isinstance(result, dict)
    assert result.get("channels") == []


def test_tool_log_path_in_result(tmp_path: Path) -> None:
    log_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    _write_events(log_path, [_push(), _outcome()])
    result = compute_channel_stats_result(window_hours=1, repo_root=str(tmp_path))
    assert "log_path" in result
