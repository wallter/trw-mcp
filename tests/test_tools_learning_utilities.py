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

import pytest

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.analytics import (
    extract_learnings_from_llm,
    extract_learnings_mechanical,
    find_success_patterns,
    is_success_event,
)
from trw_mcp.state.claude_md import collect_context_data, collect_patterns, collect_promotable_learnings
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

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

    def test_extract_learnings_from_llm_saves_entries(self, tmp_project: Path) -> None:
        """extract_learnings_from_llm persists entries to disk."""
        trw_dir = tmp_project / ".trw"
        items: list[dict[str, Any]] = [
            {"summary": "LLM insight", "detail": "details", "tags": ["llm"], "impact": "0.7"},
        ]
        result = extract_learnings_from_llm(items, trw_dir)
        assert len(result) == 1
        assert result[0]["summary"] == "LLM insight"
        # Verify file was written
        entries_dir = trw_dir / "learnings" / "entries"
        assert len(list(entries_dir.glob("*.yaml"))) >= 1

    def test_extract_learnings_from_llm_filters_telemetry_noise(
        self,
        tmp_project: Path,
    ) -> None:
        """PRD-FIX-021: LLM-generated telemetry noise must be suppressed."""
        trw_dir = tmp_project / ".trw"
        items: list[dict[str, Any]] = [
            {"summary": "Repeated operation: file_modified (85x)", "detail": "noise", "impact": "0.5"},
            {"summary": "Success: reflection_complete (6x)", "detail": "noise", "impact": "0.5"},
            {"summary": "repeated operation: checkpoint (3x)", "detail": "noise", "impact": "0.5"},
            {"summary": "Actual actionable insight", "detail": "real", "tags": ["llm"], "impact": "0.7"},
        ]
        result = extract_learnings_from_llm(items, trw_dir)
        assert len(result) == 1
        assert result[0]["summary"] == "Actual actionable insight"

    def test_extract_learnings_from_llm_normalizes_audit_finding_metadata(
        self,
        tmp_project: Path,
    ) -> None:
        """Audit-tagged LLM learnings persist the FR06-required fields."""
        trw_dir = tmp_project / ".trw"
        items: list[dict[str, Any]] = [
            {
                "summary": "Sprint 90: FR06 audit finding",
                "detail": "Runtime path missing.",
                "tags": ["audit-finding", "PRD-QUAL-056", "test_gap"],
                "impact": "0.8",
            },
        ]

        extract_learnings_from_llm(items, trw_dir)

        entries = sorted((trw_dir / "learnings" / "entries").glob("*.yaml"))
        assert entries
        data = FileStateReader().read_yaml(entries[-1])
        assert data["type"] == "incident"
        assert data["confidence"] == "verified"
        assert data["domain"] == ["testing", "quality"]
        assert data["phase_affinity"] == ["implement", "validate"]


class TestClaudeMdCollection:
    """Unit tests for claude_md collection helpers."""

    def test_collect_promotable_learnings(
        self, tmp_project: Path, reader: FileStateReader, fake_memory_store: FakeMemoryStore
    ) -> None:
        """collect_promotable_learnings returns high-impact active entries.

        PRD-CORE-280 slice e1: ``collect_promotable_learnings`` reads through
        ``list_active_learnings`` -> ``selected_store`` -> ``store.list_entries``
        (see module docstring). At baseline this test relied on the unmigrated
        SQLite backend's one-time YAML-to-SQLite auto-migration to pick up
        directly-written YAML files; the fake store has no such migration, so
        entries are seeded with ``store.put`` directly instead -- same shape
        the store would hold after that migration ran.
        """
        config = TRWConfig()
        fake_memory_store.put("important", FAKE_NAMESPACE, {"entry_id": "L-high", "importance": 0.9})
        fake_memory_store.put("trivial", FAKE_NAMESPACE, {"entry_id": "L-low", "importance": 0.2})
        with pytest.warns(DeprecationWarning, match="collect_promotable_learnings is deprecated"):
            result = collect_promotable_learnings(tmp_project / ".trw", config, reader)
        assert any(d["id"] == "L-high" for d in result)
        assert not any(d["id"] == "L-low" for d in result)

    def test_collect_patterns(self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """collect_patterns returns non-index pattern files."""
        config = TRWConfig()
        patterns_dir = tmp_project / ".trw" / "patterns"
        writer.write_yaml(patterns_dir / "p1.yaml", {"name": "test-pattern"})
        writer.write_yaml(patterns_dir / "index.yaml", {"patterns": []})
        result = collect_patterns(tmp_project / ".trw", config, reader)
        assert len(result) == 1
        assert result[0]["name"] == "test-pattern"

    def test_collect_context_data(self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """collect_context_data returns arch and conv data."""
        config = TRWConfig()
        context_dir = tmp_project / ".trw" / "context"
        writer.write_yaml(context_dir / "architecture.yaml", {"style": "hexagonal"})
        writer.write_yaml(context_dir / "conventions.yaml", {"naming": "snake_case"})
        arch, conv = collect_context_data(tmp_project / ".trw", config, reader)
        assert arch["style"] == "hexagonal"
        assert conv["naming"] == "snake_case"


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
