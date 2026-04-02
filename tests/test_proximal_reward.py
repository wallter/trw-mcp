"""Tests for scoring/proximal_reward.py — PRD-CORE-104-FR03.

Covers: proximal reward detection from event patterns.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.scoring.proximal_reward import (
    detect_proximal_signals,
    read_recent_events,
)


class TestDetectProximalSignals:
    """Tests for detect_proximal_signals()."""

    def test_test_rerun_within_2_calls(self) -> None:
        """nudge at i, build_check_complete at i+2 produces a signal."""
        events: list[dict[str, object]] = [
            {"event": "session_start"},
            {"event": "nudge_shown", "data": {"learning_id": "L-001", "phase": "implement"}},
            {"event": "tool_call"},
            {"event": "build_check_complete"},
        ]
        signals = detect_proximal_signals(events, max_offset=2)
        assert len(signals) == 1
        assert signals[0]["learning_id"] == "L-001"
        assert signals[0]["signal_type"] == "test_rerun"
        assert signals[0]["phase"] == "implement"
        assert signals[0]["turn_offset"] == 2

    def test_no_nudge_no_signal(self) -> None:
        """No nudge events produces empty signals."""
        events: list[dict[str, object]] = [
            {"event": "session_start"},
            {"event": "build_check_complete"},
            {"event": "test_run"},
        ]
        signals = detect_proximal_signals(events)
        assert signals == []

    def test_malformed_events_skipped(self) -> None:
        """Bad data in events does not crash."""
        events: list[dict[str, object]] = [
            {"event": "nudge_shown", "data": "not_a_dict"},
            {"event": "nudge_shown"},  # missing data
            {"event": "nudge_shown", "data": {"phase": "plan"}},  # missing learning_id
            {"event": "nudge_shown", "data": {"learning_id": "", "phase": "p"}},  # empty learning_id
            {},  # totally empty
            {"event": "test_run"},
        ]
        signals = detect_proximal_signals(events)
        assert signals == []

    def test_offset_boundary(self) -> None:
        """Signal at exactly max_offset is detected."""
        events: list[dict[str, object]] = [
            {"event": "nudge_shown", "data": {"learning_id": "L-002", "phase": "review"}},
            {"event": "tool_call"},
            {"event": "test_run"},  # offset=2, which is exactly max_offset=2
        ]
        signals = detect_proximal_signals(events, max_offset=2)
        assert len(signals) == 1
        assert signals[0]["turn_offset"] == 2

    def test_no_signal_beyond_offset(self) -> None:
        """Signal at max_offset+1 is not detected."""
        events: list[dict[str, object]] = [
            {"event": "nudge_shown", "data": {"learning_id": "L-003", "phase": "plan"}},
            {"event": "tool_call"},
            {"event": "tool_call"},
            {"event": "test_run"},  # offset=3, beyond max_offset=2
        ]
        signals = detect_proximal_signals(events, max_offset=2)
        assert signals == []

    def test_multiple_nudges(self) -> None:
        """Multiple nudge events each produce independent signals."""
        events: list[dict[str, object]] = [
            {"event": "nudge_shown", "data": {"learning_id": "L-A", "phase": "plan"}},
            {"event": "test_run"},
            {"event": "nudge_shown", "data": {"learning_id": "L-B", "phase": "impl"}},
            {"event": "build_check_complete"},
        ]
        signals = detect_proximal_signals(events, max_offset=2)
        assert len(signals) == 2
        ids = {s["learning_id"] for s in signals}
        assert ids == {"L-A", "L-B"}

    def test_test_run_event_also_matches(self) -> None:
        """test_run (not just build_check_complete) is detected."""
        events: list[dict[str, object]] = [
            {"event": "nudge_shown", "data": {"learning_id": "L-X", "phase": "test"}},
            {"event": "test_run"},
        ]
        signals = detect_proximal_signals(events, max_offset=2)
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "test_rerun"


class TestReadRecentEvents:
    """Tests for read_recent_events()."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Non-existent file returns empty list."""
        result = read_recent_events(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_reads_jsonl(self, tmp_path: Path) -> None:
        """Reads valid JSONL file."""
        events_path = tmp_path / "events.jsonl"
        events_path.write_text(
            '{"event": "session_start"}\n{"event": "nudge_shown"}\n',
            encoding="utf-8",
        )
        result = read_recent_events(events_path)
        assert len(result) == 2
        assert result[0]["event"] == "session_start"

    def test_limits_to_max_events(self, tmp_path: Path) -> None:
        """Only reads last max_events lines."""
        events_path = tmp_path / "events.jsonl"
        lines = [f'{{"event": "e{i}"}}\n' for i in range(10)]
        events_path.write_text("".join(lines), encoding="utf-8")
        result = read_recent_events(events_path, max_events=3)
        assert len(result) == 3
        assert result[0]["event"] == "e7"

    def test_corrupt_file_returns_empty(self, tmp_path: Path) -> None:
        """Binary/corrupt file returns empty list (fail-open)."""
        events_path = tmp_path / "events.jsonl"
        events_path.write_bytes(b"\x00\x01\x02\xff\xfe")
        result = read_recent_events(events_path)
        assert result == []
