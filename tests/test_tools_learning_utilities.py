"""Tests for extraction, collection, search, and success-pattern utilities.

PRD-CORE-280 slice e1: ``collect_promotable_learnings`` reads through
``list_active_learnings`` -> ``selected_store``, so its test routes through
``fake_memory_store`` rather than the in-process SQLite store the unpinned
``tmp_project`` checkout would otherwise open. Everything else here is pure
YAML/analytics and never touches the memory store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trw_mcp.state.analytics import (
    extract_learnings_mechanical,
    find_success_patterns,
    is_success_event,
)

# PRD-CORE-294 FR01 deleted trw_mcp.state.recall_search (search_patterns /
# collect_context) along with the execute_recall knobs that consumed it — the
# module no longer exists, so its coverage (formerly TestRecallSearch here) is
# deleted with it.


class TestAnalyticsExtraction:
    """Unit tests for mechanical learning extraction."""

    def test_extract_learnings_mechanical_errors(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical creates entries from error events."""
        trw_dir = tmp_project / ".trw"
        errors = [{"event": "tool_error", "data": "disk full", "ts": "2026-01-01"}]
        result = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result) == 1
        assert "Error pattern" in result[0]["summary"]

    def test_extract_learnings_mechanical_repeated_suppressed(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical no longer creates entries from repeated ops (PRD-FIX-021)."""
        trw_dir = tmp_project / ".trw"
        ops = [("git_push", 5)]
        result = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result) == 0  # Repeated-ops suppressed as telemetry noise

    def test_extract_mechanical_repeated_ops_no_entries(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical never creates repeated-op entries (PRD-FIX-021)."""
        trw_dir = tmp_project / ".trw"
        ops = [("git_push", 5)]
        result1 = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result1) == 0
        result2 = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result2) == 0

    def test_extract_mechanical_dedup_error_patterns(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical skips error patterns with existing active entries."""
        trw_dir = tmp_project / ".trw"
        errors = [{"event": "tool_error", "data": "disk full", "ts": "2026-01-01"}]
        # First call creates the entry
        result1 = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result1) == 1
        # Second call with same error should skip (dedup)
        result2 = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result2) == 0


class TestSuccessPatternDetection:
    """PRD-QUAL-001: Unit tests for success pattern detection in analytics."""

    def test_is_success_event_matches(self) -> None:
        """is_success_event detects success-related event types."""

        assert is_success_event({"event": "shard_complete"}) is True
        assert is_success_event({"event": "phase_gate_passed"}) is True
        assert is_success_event({"event": "tests_success"}) is True
        assert is_success_event({"event": "run_done"}) is True
        assert is_success_event({"event": "task_finished"}) is True
        assert is_success_event({"event": "prd_approved"}) is True
        assert is_success_event({"event": "delivery_complete"}) is True

    def test_is_success_event_rejects(self) -> None:
        """is_success_event rejects non-success event types."""

        assert is_success_event({"event": "error_occurred"}) is False
        assert is_success_event({"event": "shard_failed"}) is False
        assert is_success_event({"event": "phase_enter"}) is False
        assert is_success_event({"event": "run_init"}) is False

    def test_find_success_patterns_aggregates(self) -> None:
        """find_success_patterns aggregates success events by type."""

        events: list[dict[str, Any]] = [
            {"event": "shard_complete", "data": {"shard": "S1"}},
            {"event": "shard_complete", "data": {"shard": "S2"}},
            {"event": "shard_complete", "data": {"shard": "S3"}},
            {"event": "phase_gate_passed", "data": {"phase": "validate"}},
            {"event": "error_occurred", "data": {"msg": "should be ignored"}},
        ]

        patterns = find_success_patterns(events)
        assert len(patterns) >= 1

        shard_pattern = next(
            (p for p in patterns if p["event_type"] == "shard_complete"),
            None,
        )
        assert shard_pattern is not None
        assert shard_pattern["count"] == "3"
        assert "3x" in shard_pattern["summary"]

    def test_find_success_patterns_empty(self) -> None:
        """find_success_patterns returns empty for no success events."""

        events: list[dict[str, Any]] = [
            {"event": "error_occurred"},
            {"event": "phase_enter"},
        ]
        assert find_success_patterns(events) == []

    def test_find_success_patterns_sorted_by_count(self) -> None:
        """Patterns are sorted by count descending."""

        events: list[dict[str, Any]] = [
            {"event": "shard_complete"},
            {"event": "shard_complete"},
            {"event": "shard_complete"},
            {"event": "phase_gate_passed"},
        ]

        patterns = find_success_patterns(events)
        assert len(patterns) >= 2
        counts = [int(p["count"]) for p in patterns]
        assert counts == sorted(counts, reverse=True)

    def test_find_success_patterns_capped(self) -> None:
        """Patterns are capped at config.reflect_max_success_patterns."""
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        events: list[dict[str, Any]] = [{"event": f"success_type_{i}_complete"} for i in range(10)]

        patterns = find_success_patterns(events)
        assert len(patterns) <= config.reflect_max_success_patterns
