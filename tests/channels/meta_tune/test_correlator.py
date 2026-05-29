"""Tests for meta_tune._correlator.

Uses synthetic JSONL data; no live MCP, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.channels._manifest_models import CLIENT_CORRECTION_FACTORS
from trw_mcp.channels.meta_tune._correlator import (
    adjusted_rate,
    correlate,
    load_events,
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


def _push_event(
    channel_id: str = "cc-01",
    client: str = "claude-code",
    session_id: str = "sess-1",
    file_path: str = "CLAUDE.md",
    ts: str = "2026-05-28T10:00:00.000Z",
    event_type: str = "push_write",
) -> dict:
    return {
        "schema_version": "channel-event/v1",
        "channel_id": channel_id,
        "client": client,
        "event_type": event_type,
        "ts": ts,
        "session_id": session_id,
        "file_path": file_path,
    }


def _outcome_event(
    channel_id: str = "cc-01",
    client: str = "claude-code",
    session_id: str = "sess-1",
    file_path: str = "CLAUDE.md",
    ts: str = "2026-05-28T10:00:30.000Z",
    event_type: str = "edit_correlated",
) -> dict:
    return {
        "schema_version": "channel-event/v1",
        "channel_id": channel_id,
        "client": client,
        "event_type": event_type,
        "ts": ts,
        "session_id": session_id,
        "file_path": file_path,
    }


# ---------------------------------------------------------------------------
# load_events
# ---------------------------------------------------------------------------


def test_load_events_empty_file(tmp_path: Path) -> None:
    log = tmp_path / "empty.jsonl"
    log.write_text("")
    assert load_events(log) == []


def test_load_events_missing_file(tmp_path: Path) -> None:
    assert load_events(tmp_path / "nope.jsonl") == []


def test_load_events_skips_malformed(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text('{"valid": 1}\nnot-json\n{"valid": 2}\n')
    result = load_events(log)
    assert len(result) == 2


def test_load_events_skips_blank_lines(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text('{"a": 1}\n\n{"b": 2}\n')
    result = load_events(log)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# correlate — basic
# ---------------------------------------------------------------------------


def test_correlate_push_followed_by_outcome_within_window(tmp_path: Path) -> None:
    events = [
        _push_event(ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T10:00:30.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert len(results) == 1
    r = results[0]
    assert r.channel_id == "cc-01"
    assert r.total_pushes == 1
    assert r.correlated == 1
    assert r.raw_rate == pytest.approx(1.0)


def test_correlate_push_no_outcome_uncorrelated(tmp_path: Path) -> None:
    events = [_push_event()]
    results = correlate(events, window_seconds=3600)
    assert len(results) == 1
    assert results[0].correlated == 0
    assert results[0].raw_rate == pytest.approx(0.0)


def test_correlate_outcome_outside_window_uncorrelated() -> None:
    events = [
        _push_event(ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T12:00:01.000Z"),  # 7201s later > 3600s
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 0


def test_correlate_outcome_before_push_uncorrelated() -> None:
    events = [
        _push_event(ts="2026-05-28T10:00:30.000Z"),
        _outcome_event(ts="2026-05-28T10:00:00.000Z"),  # earlier than push
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 0


def test_correlate_different_session_no_correlation() -> None:
    events = [
        _push_event(session_id="sess-A", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(session_id="sess-B", ts="2026-05-28T10:00:10.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 0


def test_correlate_different_file_no_correlation() -> None:
    events = [
        _push_event(file_path="CLAUDE.md", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(file_path="other.md", ts="2026-05-28T10:00:10.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 0


def test_correlate_multiple_pushes_partial_correlation() -> None:
    events = [
        _push_event(session_id="s1", ts="2026-05-28T10:00:00.000Z"),
        _push_event(session_id="s2", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(session_id="s1", ts="2026-05-28T10:00:10.000Z"),
        # s2 has no outcome
    ]
    results = correlate(events, window_seconds=3600)
    assert len(results) == 1
    assert results[0].total_pushes == 2
    assert results[0].correlated == 1
    assert results[0].raw_rate == pytest.approx(0.5)


def test_correlate_per_channel_grouping() -> None:
    # JOIN_KEY_FIELDS = (session_id, file_path) — channel_id is NOT a join key.
    # Both ch-A and ch-B pushes share session_id=s1 + file_path=CLAUDE.md,
    # so a single outcome for that key correlates BOTH pushes.
    # Use distinct sessions to isolate per-channel correlation.
    events = [
        _push_event(channel_id="ch-A", session_id="s-A", ts="2026-05-28T10:00:00.000Z"),
        _push_event(channel_id="ch-B", session_id="s-B", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(channel_id="ch-A", session_id="s-A", ts="2026-05-28T10:00:05.000Z"),
        # ch-B / s-B has no outcome
    ]
    results = correlate(events, window_seconds=3600)
    by_channel = {r.channel_id: r for r in results}
    assert by_channel["ch-A"].correlated == 1
    assert by_channel["ch-B"].correlated == 0


def test_correlate_empty_events() -> None:
    assert correlate([]) == []


def test_correlate_only_outcomes_no_pushes() -> None:
    events = [_outcome_event()]
    assert correlate(events) == []


# ---------------------------------------------------------------------------
# adjusted_rate
# ---------------------------------------------------------------------------


def test_adjusted_rate_applies_correction_factor() -> None:
    # claude-code factor = 0.85 → raw=0.85 → adj=1.0
    adj = adjusted_rate(0.85, "claude-code")
    assert adj == pytest.approx(1.0)


def test_adjusted_rate_capped_at_one() -> None:
    adj = adjusted_rate(1.0, "claude-code")
    assert adj <= 1.0


def test_adjusted_rate_zero_raw() -> None:
    adj = adjusted_rate(0.0, "claude-code")
    assert adj == pytest.approx(0.0)


def test_adjusted_rate_unknown_client_no_adjustment() -> None:
    raw = 0.5
    adj = adjusted_rate(raw, "unknown-client")
    assert adj == pytest.approx(raw)


def test_adjusted_rate_copilot_factor() -> None:
    factor = CLIENT_CORRECTION_FACTORS["copilot"]  # 0.50
    raw = 0.30
    adj = adjusted_rate(raw, "copilot")
    assert adj == pytest.approx(min(raw / factor, 1.0))


def test_adjusted_rate_all_known_clients() -> None:
    for client, factor in CLIENT_CORRECTION_FACTORS.items():
        raw = 0.4
        adj = adjusted_rate(raw, client)
        expected = min(raw / factor, 1.0)
        assert adj == pytest.approx(expected), f"client={client}"


# ---------------------------------------------------------------------------
# correlate via file (integration with load_events)
# ---------------------------------------------------------------------------


def test_correlate_from_file(tmp_path: Path) -> None:
    events = [
        _push_event(ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T10:00:05.000Z"),
    ]
    log_path = _write_events(tmp_path, events)
    loaded = load_events(log_path)
    results = correlate(loaded, window_seconds=3600)
    assert results[0].correlated == 1


def test_correlate_at_window_boundary_inclusive() -> None:
    """Outcome exactly at push_ts + window should be correlated."""
    events = [
        _push_event(ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T11:00:00.000Z"),  # exactly 3600s
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 1


def test_correlate_push_ephemeral_event_type() -> None:
    events = [
        _push_event(event_type="push_ephemeral", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T10:00:05.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 1


def test_correlate_pull_tool_call_event_type() -> None:
    events = [
        _push_event(event_type="pull_tool_call", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T10:00:05.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 1
