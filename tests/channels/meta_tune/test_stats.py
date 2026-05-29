"""Tests for meta_tune._stats.

Uses synthetic JSONL events; no live MCP, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.channels.meta_tune._stats import (
    ChannelStatsReport,
    compute_channel_stats,
    format_stats_table,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    log_path = tmp_path / "channel-events.jsonl"
    with log_path.open("w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    return log_path


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
# compute_channel_stats
# ---------------------------------------------------------------------------


def test_compute_empty_log_returns_empty_report(tmp_path: Path) -> None:
    log = tmp_path / "channel-events.jsonl"
    log.write_text("")
    report = compute_channel_stats(log)
    assert isinstance(report, ChannelStatsReport)
    assert report.channels == []
    assert report.total_events == 0


def test_compute_missing_log_returns_empty_report(tmp_path: Path) -> None:
    report = compute_channel_stats(tmp_path / "nope.jsonl")
    assert report.channels == []
    assert report.total_events == 0


def test_compute_single_correlated_push(tmp_path: Path) -> None:
    events = [_push(), _outcome()]
    log = _write_events(tmp_path, events)
    report = compute_channel_stats(log, window_seconds=3600)
    assert report.total_events == 2
    assert len(report.channels) == 1
    entry = report.channels[0]
    assert entry.channel_id == "ch-01"
    assert entry.client == "claude-code"
    assert entry.total_pushes == 1
    assert entry.correlated == 1
    assert entry.raw_rate == pytest.approx(1.0)
    assert entry.adjusted_rate > 0


def test_compute_report_has_window_seconds(tmp_path: Path) -> None:
    log = _write_events(tmp_path, [_push()])
    report = compute_channel_stats(log, window_seconds=1800)
    assert report.window_seconds == 1800


def test_compute_report_log_path_populated(tmp_path: Path) -> None:
    log = _write_events(tmp_path, [_push()])
    report = compute_channel_stats(log)
    assert str(log) in report.log_path


def test_compute_multiple_channels(tmp_path: Path) -> None:
    events = [
        _push(channel_id="A", ts="2026-05-28T10:00:00.000Z"),
        _push(channel_id="B", ts="2026-05-28T10:00:00.000Z"),
        _outcome(channel_id="A", ts="2026-05-28T10:00:05.000Z"),
    ]
    log = _write_events(tmp_path, events)
    report = compute_channel_stats(log, window_seconds=3600)
    ids = {e.channel_id for e in report.channels}
    assert "A" in ids
    assert "B" in ids


def test_compute_throttle_status_populated(tmp_path: Path) -> None:
    log = _write_events(tmp_path, [_push()])
    report = compute_channel_stats(log)
    assert report.channels[0].throttle_status != ""


def test_compute_channels_sorted(tmp_path: Path) -> None:
    events = [
        _push(channel_id="Z-ch", client="claude-code"),
        _push(channel_id="A-ch", client="claude-code"),
    ]
    log = _write_events(tmp_path, events)
    report = compute_channel_stats(log)
    ids = [e.channel_id for e in report.channels]
    assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# format_stats_table
# ---------------------------------------------------------------------------


def test_format_stats_table_empty_report() -> None:
    report = ChannelStatsReport()
    out = format_stats_table(report)
    assert "No channel stats" in out


def test_format_stats_table_has_header(tmp_path: Path) -> None:
    log = _write_events(tmp_path, [_push(), _outcome()])
    report = compute_channel_stats(log)
    table = format_stats_table(report)
    assert "Channel" in table
    assert "Client" in table
    assert "Pushes" in table


def test_format_stats_table_includes_channel_id(tmp_path: Path) -> None:
    log = _write_events(tmp_path, [_push(channel_id="my-channel"), _outcome(channel_id="my-channel")])
    report = compute_channel_stats(log)
    table = format_stats_table(report)
    assert "my-channel" in table


def test_format_stats_table_includes_client(tmp_path: Path) -> None:
    log = _write_events(tmp_path, [_push(client="codex"), _outcome(client="codex")])
    report = compute_channel_stats(log)
    table = format_stats_table(report)
    assert "codex" in table
